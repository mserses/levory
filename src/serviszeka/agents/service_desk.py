"""Hizmet Masası yapay zekâ ajanı: olay sınıflandırma (triage) ve çözüm önerisi.

Akış: maskele -> bilgi bankasında ara (ilk 3) -> LLM (hızlı model) -> pydantic doğrulama
-> KB kaynak kontrolü -> policy.evaluate -> (AUTO_APPLIED ise) update -> AIDecision + audit.

Nöbetçi bildirimi işlem içinde gönderilmez, outbox'a eklenir: çağıran commit ettikten sonra
`notifications.send_pending(session, notifier)` ile gönderir.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from typing import Any, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, knowledge, notifications, policy, taxonomy
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import (
    CIClass,
    CIEnvironment,
    CIStatus,
    DecisionOutcome,
    Impact,
    Priority,
    RiskLevel,
    TicketKind,
    Urgency,
)
from serviszeka.domain.priority import calculate_priority
from serviszeka.llm import LLMClient, LLMError
from serviszeka.models import AIDecision, ConfigurationItem, Incident
from serviszeka.privacy import mask_pii
from serviszeka.services import cmdb
from serviszeka.services import incidents as incident_service

logger = logging.getLogger(__name__)

AGENT = "service_desk"
ACTION = "classify"
PRIORITY_ACTION = "set_priority"
ONCALL_EVENT = "incident.priority_raise_pending"
ACTOR = "ai:service_desk"
KB_TOP_K = 3
# Uydurma (getirilmeyen) KB kimliği başına güven cezası
HALLUCINATION_PENALTY = 0.2
# Sınıf listesinde olmayan her değer için güven cezası
OFF_TAXONOMY_PENALTY = 0.2
MAX_INPUT_SUMMARY = 4000

# Ajanın olaya uygulayabileceği alanlar (çözüm metni asla otomatik yazılmaz)
APPLICABLE_FIELDS: tuple[str, ...] = (
    "kind",
    "category",
    "service",
    "impact",
    "urgency",
    "assignment_group",
)
# Önceliği belirleyen alanlar (service_desk.set_priority politikasına tabi)
PRIORITY_FIELDS: frozenset[str] = frozenset({"impact", "urgency"})

SYSTEM_PROMPT = """Sen ServisZeka'nın ITIL 4 Hizmet Masası ajanısın.
Görevin: gelen kaydı sınıflandırmak, etki/aciliyeti belirlemek, doğru destek grubunu
önermek ve varsa bilgi bankası makalelerine dayanarak çözüm önermek.

Kurallar:
- kind: "incident" (çalışan bir şey bozuldu/yavaşladı) | "service_request" (yeni bir şey
  isteniyor: erişim, yazılım, cihaz, parola sıfırlama) | "question" (nasıl yapılır; bozuk bir şey yok).
- category, service, assignment_group, impact, urgency: YALNIZCA aşağıdaki listedeki
  anahtarlardan birini kullan; listede olmayan değer yazma.
- impact ve urgency kayıtta yazanlara göre belirlenir: etkilenen kişi sayısı yazmıyorsa
  impact "low"; urgency yalnızca metinde zaman baskısı veya iş durması varsa yükselir.
- Oltalama, zararlı yazılım veya hesap ele geçirme şüphesi varsa, başka bir hizmetle ilgili
  görünse bile category "guvenlik" ve assignment_group "security_ops" olur.
- Basit parola sıfırlama ve sorular "service_desk_l1" grubuna gider.
- kb_article_ids: YALNIZCA sana verilen bilgi bankası sonuçlarındaki id'leri kullan; uydurma.
- Emin değilsen confidence değerini düşük ver (0-1 arası).
- Maskelenmiş kişisel verileri ([TCKN], [EPOSTA] vb.) tahmin etmeye çalışma.

İzinli değerler:
{taxonomy}

Yanıt şeması (JSON):
{{"kind": str, "category": str, "service": str, "impact": str, "urgency": str,
 "assignment_group": str, "suggested_resolution": str|null,
 "kb_article_ids": [str], "confidence": float, "rationale": str}}"""


def system_prompt() -> str:
    return SYSTEM_PROMPT.format(taxonomy=taxonomy.prompt_section())


class TriageResult(BaseModel):
    """LLM çıktısının doğrulanmış hali."""

    model_config = ConfigDict(extra="ignore")

    kind: TicketKind
    category: str | None = Field(default=None, max_length=100)
    service: str | None = Field(default=None, max_length=100)
    impact: Impact
    urgency: Urgency
    assignment_group: str | None = Field(default=None, max_length=100)
    suggested_resolution: str | None = None
    kb_article_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("kind", "impact", "urgency", mode="before")
    @classmethod
    def _lower(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("category", "service", "assignment_group", "suggested_resolution")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v.strip() or None if isinstance(v, str) else v


class DecisionError(Exception):
    """Karar bulunamadı ya da onaylanamaz durumda."""


STALE_DECISION_MESSAGE = "Kayıt öneriden sonra değişti; yeniden triage gerekli"


class StaleDecisionError(DecisionError):
    """Karar verildikten sonra olay değişti; öneri bayat."""

    def __init__(self) -> None:
        super().__init__(STALE_DECISION_MESSAGE)


def _incident_text(incident: Incident) -> str:
    parts = [f"Başlık: {incident.title}", f"Açıklama: {incident.description or '-'}"]
    if incident.service:
        parts.append(f"Hizmet: {incident.service}")
    if incident.category:
        parts.append(f"Kategori: {incident.category}")
    return "\n".join(parts)


def _build_user_prompt(masked_text: str, hits: list[knowledge.SearchHit]) -> str:
    kb = [
        {"id": h.article_id, "number": h.number, "title": h.title, "snippet": h.snippet}
        for h in hits
    ]
    return (
        f"Kayıt:\n{masked_text}\n\n"
        f"Bilgi bankası sonuçları (yalnızca bunlara atıf yap):\n"
        f"{json.dumps(kb, ensure_ascii=False, indent=2) if kb else '[] (sonuç yok)'}"
    )


# --- CMDB etki kanıtı -------------------------------------------------------------------

# Taban kuralları: olaya bağlı CMDB kanıtı önerilen etkiyi yalnızca YÜKSELTEBİLİR
CMDB_IMPACT_DEPTH = 5
CRITICAL_CI_FLOOR = Impact.MEDIUM  # prod'da kritiklik 1 CI -> etki en az medium
MULTI_SERVICE_FLOOR = Impact.HIGH  # etki analizinde >= 2 iş hizmeti -> etki high
MULTI_SERVICE_THRESHOLD = 2
_IMPACT_RANK = {Impact.LOW: 0, Impact.MEDIUM: 1, Impact.HIGH: 2}


@dataclass
class CMDBEvidence:
    """Olaya bağlı CI'lardan türetilen etki kanıtı (deterministik)."""

    cis: list[ConfigurationItem]
    # Etki analizinde etkilenen (kökler dahil), prod ortamındaki, emekli olmayan iş hizmetleri
    business_services: list[ConfigurationItem] = dc_field(default_factory=list)
    impact_floor: Impact | None = None
    reasons: list[str] = dc_field(default_factory=list)


def _counts(ci: ConfigurationItem) -> bool:
    return CIEnvironment(ci.environment) == CIEnvironment.PROD and (
        CIStatus(ci.status) != CIStatus.RETIRED
    )


def impact_floor(
    cis: Sequence[ConfigurationItem], business_services: Sequence[ConfigurationItem]
) -> tuple[Impact | None, list[str]]:
    """CMDB kanıtından etki tabanı ve gerekçeleri (taban yoksa None)."""
    floor: Impact | None = None
    reasons: list[str] = []
    critical = [c for c in cis if c.criticality == 1 and _counts(c)]
    if critical:
        floor = CRITICAL_CI_FLOOR
        reasons.append(
            "Prod ortamında kritiklik 1 yapılandırma öğesi etkileniyor: "
            + ", ".join(mask_pii(c.name) for c in critical)
        )
    if len(business_services) >= MULTI_SERVICE_THRESHOLD:
        floor = MULTI_SERVICE_FLOOR
        reasons.append(
            f"Etki analizine göre {len(business_services)} iş hizmeti etkileniyor: "
            + ", ".join(mask_pii(c.name) for c in business_services)
        )
    return floor, reasons


async def cmdb_evidence(session: AsyncSession, incident: Incident) -> CMDBEvidence | None:
    """Olaya bağlı CI yoksa None; varsa etki analizi + taban kuralı."""
    cis = await incident_service.list_affected_cis(session, incident.id)
    if not cis:
        return None
    analysis = await cmdb.impact_analysis(session, [c.id for c in cis], CMDB_IMPACT_DEPTH)
    services = [
        c for c in analysis.business_services if CIClass(c.ci_class) == CIClass.SERVICE and _counts(c)
    ]
    floor, reasons = impact_floor(cis, services)
    return CMDBEvidence(cis=cis, business_services=services, impact_floor=floor, reasons=reasons)


def _cmdb_prompt_section(evidence: CMDBEvidence) -> str:
    """Maskelenmiş CMDB bağlamı (LLM istemine ve karar girdisine eklenir)."""
    items = [
        {
            "name": mask_pii(c.name),
            "class": CIClass(c.ci_class).value,
            "environment": CIEnvironment(c.environment).value,
            "criticality": c.criticality,
            "status": CIStatus(c.status).value,
            "description": mask_pii(c.description or "")[:300],
        }
        for c in evidence.cis
    ]
    services = [mask_pii(c.name) for c in evidence.business_services]
    return (
        "Etkilenen yapılandırma öğeleri (CMDB; kritiklik 1 = en kritik):\n"
        f"{json.dumps(items, ensure_ascii=False, indent=2)}\n"
        f"Etki analizine göre etkilenen prod iş hizmetleri ({len(services)}): "
        f"{json.dumps(services, ensure_ascii=False)}\n"
        "Etkiyi belirlerken bu CMDB bilgisini kanıt olarak kullan."
    )


def assess_risk(priority: Priority) -> RiskLevel:
    """Önerilen önceliğe göre risk: P1 (büyük olay) insan onayı ister."""
    if priority == Priority.P1:
        return RiskLevel.HIGH
    if priority == Priority.P2:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


_RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
# Parça sonuçları birleştirilirken en kısıtlayıcı olan kazanır
_OUTCOME_RANK = {
    DecisionOutcome.AUTO_APPLIED: 0,
    DecisionOutcome.SUGGESTED: 1,
    DecisionOutcome.NEEDS_APPROVAL: 2,
    DecisionOutcome.ESCALATED: 3,
    DecisionOutcome.REJECTED: 4,
}


def _proposed_changes(result: TriageResult) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    return {k: data[k] for k in APPLICABLE_FIELDS if data.get(k) is not None}


async def _persist(
    session: AsyncSession,
    *,
    incident: Incident,
    outcome: DecisionOutcome,
    confidence: float,
    model: str,
    input_summary: str,
    output: dict[str, Any],
    rationale: str,
    sources: list[str],
    reason: str,
    incident_version: int,
) -> AIDecision:
    decision = AIDecision(
        incident_id=incident.id,
        agent=AGENT,
        action=ACTION,
        input_summary=input_summary[:MAX_INPUT_SUMMARY],
        output={**output, "policy_reason": reason},
        rationale=rationale,
        sources=sources,
        model=model,
        confidence=confidence,
        outcome=outcome,
        incident_version=incident_version,
        created_at=datetime.now(UTC),
    )
    session.add(decision)
    await session.flush()
    await audit.record(
        session,
        actor=ACTOR,
        action="ai_decision.triage",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={
            "incident_id": incident.id,
            "incident_number": incident.number,
            "outcome": outcome.value,
            "confidence": confidence,
            "model": model,
            "sources": sources,
            "reason": reason,
            "incident_version": incident_version,
        },
    )
    return decision


async def triage(
    session: AsyncSession,
    incident: Incident,
    llm: LLMClient,
    *,
    settings: Settings | None = None,
) -> AIDecision:
    """Olayı sınıflandırır; politika izin verirse uygular. Her durumda AIDecision döner.

    Nöbetçi bildirimi outbox'a eklenir; commit sonrası `notifications.send_pending` gönderir.
    """
    settings = settings or get_settings()
    # Sahte (fake) sağlayıcı karar kaydında kendi adını taşır; gerçek istemci hızlı modeli
    model = getattr(llm, "model_name", None) or settings.llm_fast_model
    base_version = incident.version
    masked = mask_pii(_incident_text(incident))
    evidence = await cmdb_evidence(session, incident)
    if evidence is not None:
        masked = f"{masked}\n\n{mask_pii(_cmdb_prompt_section(evidence))}"
    hits = await knowledge.search(
        session, mask_pii(f"{incident.title} {incident.description}"), KB_TOP_K
    )
    user_prompt = _build_user_prompt(masked, hits)

    async def escalate(reason: str, output: dict[str, Any]) -> AIDecision:
        return await _persist(
            session,
            incident=incident,
            outcome=DecisionOutcome.ESCALATED,
            confidence=0.0,
            model=model,
            input_summary=masked,
            output=output,
            rationale="",
            sources=[],
            reason=reason,
            incident_version=base_version,
        )

    try:
        raw = await llm.complete_json(system=system_prompt(), user=user_prompt, model=model)
    except LLMError as exc:
        return await escalate(
            f"LLM çağrısı başarısız: {exc}; insana yönlendirildi.", {"error": str(exc)}
        )
    try:
        result = TriageResult.model_validate(raw)
    except ValidationError as exc:
        return await escalate(
            "LLM çıktısı şemaya uymuyor; insana yönlendirildi.",
            {
                "error": "invalid_output",
                "details": json.loads(exc.json(include_url=False, include_input=False)),
                "raw": raw,
            },
        )

    # Kaynak doğrulama: yalnızca getirilen makaleler (id veya numara ile) kabul edilir
    by_key = {h.article_id: h.article_id for h in hits} | {h.number: h.article_id for h in hits}
    sources: list[str] = []
    dropped: list[str] = []
    for ref in result.kb_article_ids:
        art_id = by_key.get(str(ref).strip())
        if art_id is None:
            dropped.append(str(ref))
        elif art_id not in sources:
            sources.append(art_id)
    # Sınıf listesi doğrulama: listede olmayan değer uygulanmaz, güveni düşürür
    off_taxonomy: dict[str, str] = {}
    for field in taxonomy.TAXONOMY_FIELDS:
        value = getattr(result, field)
        if value is not None and value not in taxonomy.allowed(field):
            off_taxonomy[field] = value
            setattr(result, field, None)

    # CMDB taban kuralı: yalnızca olay türünde ve yalnızca YÜKSELTİR; öncelik onay kuralları
    # (P1/P2 yükseltmesi insan onayı) aşağıda aynen uygulanır
    llm_impact = result.impact
    floor_applied = False
    if (
        evidence is not None
        and evidence.impact_floor is not None
        and result.kind == TicketKind.INCIDENT
        and _IMPACT_RANK[evidence.impact_floor] > _IMPACT_RANK[result.impact]
    ):
        result.impact = evidence.impact_floor
        floor_applied = True

    confidence = result.confidence
    penalty = HALLUCINATION_PENALTY * len(dropped) + OFF_TAXONOMY_PENALTY * len(off_taxonomy)
    if penalty:
        confidence = max(0.0, round(confidence - penalty, 4))

    current_priority = Priority(incident.priority)
    proposed_priority = calculate_priority(result.impact, result.urgency)
    proposed = _proposed_changes(result)
    classify_fields = {k: v for k, v in proposed.items() if k not in PRIORITY_FIELDS}
    priority_fields = {k: v for k, v in proposed.items() if k in PRIORITY_FIELDS}
    priority_touched = any(str(getattr(incident, k)) != str(v) for k, v in priority_fields.items())

    # Sınıflandırma (öncelik dışı alanlar): mevcut önceliğe göre risk, büyük olaya dokunulmaz
    classify_risk = assess_risk(current_priority)
    verdict = policy.evaluate(
        agent=AGENT, action=ACTION, confidence=confidence, risk=classify_risk, settings=settings
    )
    # Öncelik: yalnızca P3..P5 içinde ve düşürme değilse otomatik; aksi halde onay
    priority_risk = policy.priority_change_risk(current_priority, proposed_priority)
    priority_verdict: policy.PolicyDecision | None = None
    if priority_touched:
        priority_verdict = policy.evaluate(
            agent=AGENT,
            action=PRIORITY_ACTION,
            confidence=confidence,
            risk=priority_risk,
            settings=settings,
        )
        if priority_verdict.outcome == DecisionOutcome.NEEDS_APPROVAL:
            priority_verdict = policy.PolicyDecision(
                DecisionOutcome.NEEDS_APPROVAL,
                _priority_reason(current_priority, proposed_priority),
            )

    outcome, reason = verdict.outcome, verdict.reason
    if priority_verdict is not None and (
        _OUTCOME_RANK[priority_verdict.outcome] > _OUTCOME_RANK[outcome]
    ):
        outcome, reason = priority_verdict.outcome, priority_verdict.reason

    # Uygulanacak alanlar: her parça kendi politika sonucuna göre
    to_apply: dict[str, Any] = {}
    if verdict.outcome == DecisionOutcome.AUTO_APPLIED:
        to_apply.update(classify_fields)
        if priority_verdict is None or priority_verdict.outcome == DecisionOutcome.AUTO_APPLIED:
            to_apply.update(priority_fields)

    output: dict[str, Any] = {
        **result.model_dump(mode="json"),
        "kb_article_ids": sources,
        "dropped_kb_ids": dropped,
        "off_taxonomy": off_taxonomy,
        "llm_confidence": result.confidence,
        "llm_impact": llm_impact.value,
        "cmdb": None
        if evidence is None
        else {
            "ci_ids": [c.id for c in evidence.cis],
            "business_service_ids": [c.id for c in evidence.business_services],
            "impact_floor": evidence.impact_floor.value if evidence.impact_floor else None,
            "floor_reasons": evidence.reasons,
            "floor_applied": floor_applied,
        },
        "proposed": proposed,
        "current_priority": current_priority.value,
        "proposed_priority": proposed_priority.value,
        "risk": max(classify_risk, priority_risk, key=_RISK_RANK.__getitem__).value,
        "priority_risk": priority_risk.value,
        "applied": False,
        "applied_fields": [],
        "pending_fields": [],
    }

    # Kararın dayandığı sürüm: kendi kısmi uygulamasından sonraki sürüm
    decision_version = base_version
    if to_apply:
        try:
            applied_incident = await incident_service.apply_changes(
                session,
                incident.id,
                {**to_apply, "ai_confidence": confidence},
                ACTOR,
                expected_version=base_version,
                audit_details={"source": "ai_triage"},
            )
            decision_version = applied_incident.version
            output["applied_fields"] = sorted(to_apply)
            output["pending_fields"] = sorted(set(proposed) - set(to_apply))
            output["applied"] = outcome == DecisionOutcome.AUTO_APPLIED
        except (
            incident_service.VersionConflictError,
            incident_service.InvalidTransitionError,
        ) as exc:
            # Ajan çalışırken kayıt değişti ya da kapalı: insan verisinin üzerine yazılmaz
            if outcome == DecisionOutcome.AUTO_APPLIED:
                outcome = DecisionOutcome.SUGGESTED
                reason = f"{exc} Karar öneri olarak bırakıldı."
            else:
                reason = f"{reason} {exc}"
    if not output["applied_fields"]:
        output["pending_fields"] = sorted(proposed)

    decision = await _persist(
        session,
        incident=incident,
        outcome=outcome,
        confidence=confidence,
        model=model,
        input_summary=masked,
        output=output,
        rationale=result.rationale,
        sources=sources,
        reason=reason,
        incident_version=decision_version,
    )

    # Yalnızca etki/aciliyet gerçekten değişiyorsa onay bekleyen bir yükseltme vardır
    if priority_touched and policy.is_urgent_raise(current_priority, proposed_priority):
        _stage_oncall(
            session,
            decision=decision,
            incident=incident,
            current=current_priority,
            proposed=proposed_priority,
            reason=reason,
        )
    return decision


def _priority_reason(current: Priority, proposed: Priority) -> str:
    if policy.is_urgent_raise(current, proposed):
        return (
            f"Öncelik {current.value} -> {proposed.value} yükseltmesi otomatik uygulanamaz; "
            "insan onayı gerekli, nöbetçi bilgilendirildi."
        )
    if policy.is_downgrade(current, proposed):
        return (
            f"Öncelik {current.value} -> {proposed.value} düşürmesi otomatik uygulanamaz; "
            "insan onayı gerekli."
        )
    return (
        f"Öncelik {current.value} -> {proposed.value} yalnızca P3..P5 içinde otomatik "
        "değiştirilebilir; insan onayı gerekli."
    )


def _stage_oncall(
    session: AsyncSession,
    *,
    decision: AIDecision,
    incident: Incident,
    current: Priority,
    proposed: Priority,
    reason: str,
) -> None:
    """P1/P2 yükseltmesini nöbetçi outbox'ına ekler; gönderim commit sonrasıdır."""
    payload = {
        "incident_id": incident.id,
        "incident_number": incident.number,
        "title": mask_pii(incident.title or ""),
        "current_priority": current.value,
        "proposed_priority": proposed.value,
        "decision_id": decision.id,
        "outcome": DecisionOutcome(decision.outcome).value,
        "reason": reason,
    }
    notifications.stage(
        session,
        notifications.PendingNotification(
            event=ONCALL_EVENT,
            payload=payload,
            actor=ACTOR,
            entity_type="ai_decision",
            entity_id=decision.id,
            details={"incident_id": incident.id, "proposed_priority": proposed.value},
        ),
    )


async def get_decision(session: AsyncSession, decision_id: str) -> AIDecision | None:
    return await session.get(AIDecision, decision_id)


async def list_decisions(session: AsyncSession, incident_id: str) -> list[AIDecision]:
    stmt = (
        select(AIDecision)
        .where(AIDecision.incident_id == incident_id)
        .order_by(AIDecision.created_at.desc())
    )
    return list((await session.scalars(stmt)).all())


INBOX_OUTCOMES = (
    DecisionOutcome.NEEDS_APPROVAL,
    DecisionOutcome.SUGGESTED,
    DecisionOutcome.ESCALATED,
)


async def list_inbox(
    session: AsyncSession,
    outcomes: Sequence[DecisionOutcome] = INBOX_OUTCOMES,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[AIDecision, Incident | None]]:
    """Olaylar arası karar kutusu: henüz onaylanmamış kararlar, en yeni önce."""
    stmt = (
        select(AIDecision, Incident)
        .outerjoin(Incident, AIDecision.incident_id == Incident.id)
        .where(AIDecision.outcome.in_(list(outcomes)), AIDecision.approved_by.is_(None))
        .order_by(AIDecision.created_at.desc(), AIDecision.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [(d, i) for d, i in (await session.execute(stmt)).all()]


APPROVABLE_OUTCOMES = frozenset({DecisionOutcome.NEEDS_APPROVAL, DecisionOutcome.SUGGESTED})


async def approve_decision(session: AsyncSession, decision_id: str, approver: str) -> AIDecision:
    """Öneri/onay bekleyen kararı insan onayıyla olaya uygular."""
    decision = await get_decision(session, decision_id)
    if decision is None:
        raise DecisionError(f"Karar bulunamadı: {decision_id}")
    if decision.outcome not in APPROVABLE_OUTCOMES:
        raise DecisionError(
            f"Bu karar onaylanamaz (sonuç: {DecisionOutcome(decision.outcome).value})."
        )
    if decision.approved_by:
        raise DecisionError(f"Karar zaten {decision.approved_by} tarafından onaylanmış.")
    if decision.incident_id is None:
        raise DecisionError("Karar bir olaya bağlı değil.")

    # Sürüm kontrolü apply_changes içinde veritabanına karşı (satır kilidiyle) yapılır
    base_version = decision.incident_version
    proposed = dict((decision.output or {}).get("proposed") or {})
    if base_version is None:
        # Eski karar (0003 öncesi): dayandığı sürüm bilinmiyor, bayatlık doğrulanamaz
        current = await incident_service.get_incident(session, decision.incident_id)
        await _reject_stale(session, decision, approver, None, current and current.version)
    try:
        await incident_service.apply_changes(
            session,
            decision.incident_id,
            {**proposed, "ai_confidence": decision.confidence},
            approver,
            expected_version=base_version,
            audit_details={"source": "ai_triage_approval", "decision_id": decision.id},
        )
    except incident_service.VersionConflictError as exc:
        # Karar verildikten sonra kayıt değişti: insan verisinin üzerine yazılmaz
        await _reject_stale(session, decision, approver, base_version, exc.actual)
    decision.approved_by = approver
    decision.approved_at = datetime.now(UTC)
    decision.output = {**(decision.output or {}), "applied": True}
    await session.flush()
    await audit.record(
        session,
        actor=approver,
        action="ai_decision.approve",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={"incident_id": decision.incident_id, "applied": proposed},
    )
    return decision


async def _reject_stale(
    session: AsyncSession,
    decision: AIDecision,
    approver: str,
    decision_version: int | None,
    incident_version: int | None,
) -> NoReturn:
    """Bayat onay reddini denetime yazar ve StaleDecisionError yükseltir (commit çağıranda)."""
    await audit.record(
        session,
        actor=approver,
        action="ai_decision.approve_rejected",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={
            "incident_id": decision.incident_id,
            "reason": "stale_decision",
            "decision_version": decision_version,
            "incident_version": incident_version,
        },
    )
    raise StaleDecisionError()

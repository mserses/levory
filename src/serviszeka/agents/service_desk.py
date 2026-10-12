"""Hizmet Masası yapay zekâ ajanı: olay sınıflandırma (triage) ve çözüm önerisi.

Akış: maskele -> bilgi bankasında ara (ilk 3) -> LLM (hızlı model) -> pydantic doğrulama
-> KB kaynak kontrolü -> policy.evaluate -> (AUTO_APPLIED ise) update -> AIDecision + audit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, knowledge, policy
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import (
    DecisionOutcome,
    Impact,
    Priority,
    RiskLevel,
    TicketKind,
    Urgency,
)
from serviszeka.domain.priority import calculate_priority
from serviszeka.llm import LLMClient, LLMError
from serviszeka.models import AIDecision, Incident
from serviszeka.privacy import mask_pii
from serviszeka.services import incidents as incident_service

AGENT = "service_desk"
ACTION = "classify"
ACTOR = "ai:service_desk"
KB_TOP_K = 3
# Uydurma (getirilmeyen) KB kimliği başına güven cezası
HALLUCINATION_PENALTY = 0.2
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

SYSTEM_PROMPT = """Sen ServisZeka'nın ITIL 4 Hizmet Masası ajanısın.
Görevin: gelen kaydı sınıflandırmak, etki/aciliyeti belirlemek, doğru destek grubunu
önermek ve varsa bilgi bankası makalelerine dayanarak çözüm önermek.

Kurallar:
- kind: "incident" | "service_request" | "question"
- impact ve urgency: "high" | "medium" | "low"
- kb_article_ids: YALNIZCA sana verilen bilgi bankası sonuçlarındaki id'leri kullan; uydurma.
- Emin değilsen confidence değerini düşük ver (0-1 arası).
- Maskelenmiş kişisel verileri ([TCKN], [EPOSTA] vb.) tahmin etmeye çalışma.

Yanıt şeması (JSON):
{"kind": str, "category": str|null, "service": str|null, "impact": str, "urgency": str,
 "assignment_group": str|null, "suggested_resolution": str|null,
 "kb_article_ids": [str], "confidence": float, "rationale": str}"""


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


def assess_risk(priority: Priority) -> RiskLevel:
    """Önerilen önceliğe göre risk: P1 (büyük olay) insan onayı ister."""
    if priority == Priority.P1:
        return RiskLevel.HIGH
    if priority == Priority.P2:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


_RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


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
    """Olayı sınıflandırır; politika izin verirse uygular. Her durumda AIDecision döner."""
    settings = settings or get_settings()
    model = settings.llm_fast_model
    base_version = incident.version
    masked = mask_pii(_incident_text(incident))
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
        )

    try:
        raw = await llm.complete_json(system=SYSTEM_PROMPT, user=user_prompt, model=model)
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
    confidence = result.confidence
    if dropped:
        confidence = max(0.0, round(confidence - HALLUCINATION_PENALTY * len(dropped), 4))

    proposed_priority = calculate_priority(result.impact, result.urgency)
    # Risk hem mevcut hem önerilen önceliğe göre: P1 olay otomatik düşürülemez
    risk = max(
        assess_risk(proposed_priority),
        assess_risk(Priority(incident.priority)),
        key=_RISK_RANK.__getitem__,
    )
    verdict = policy.evaluate(
        agent=AGENT, action=ACTION, confidence=confidence, risk=risk, settings=settings
    )
    outcome = verdict.outcome
    reason = verdict.reason
    proposed = _proposed_changes(result)
    output: dict[str, Any] = {
        **result.model_dump(mode="json"),
        "kb_article_ids": sources,
        "dropped_kb_ids": dropped,
        "llm_confidence": result.confidence,
        "proposed": proposed,
        "proposed_priority": proposed_priority.value,
        "risk": risk.value,
        "applied": False,
    }

    if outcome == DecisionOutcome.AUTO_APPLIED:
        try:
            await incident_service.apply_changes(
                session,
                incident.id,
                {**proposed, "ai_confidence": confidence},
                ACTOR,
                expected_version=base_version,
                audit_details={"source": "ai_triage"},
            )
            output["applied"] = True
        except (
            incident_service.VersionConflictError,
            incident_service.InvalidTransitionError,
        ) as exc:
            # Ajan çalışırken kayıt değişti ya da kapalı: insan verisinin üzerine yazılmaz
            outcome = DecisionOutcome.SUGGESTED
            reason = f"{exc} Karar öneri olarak bırakıldı."

    return await _persist(
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

    proposed = dict((decision.output or {}).get("proposed") or {})
    await incident_service.apply_changes(
        session,
        decision.incident_id,
        {**proposed, "ai_confidence": decision.confidence},
        approver,
        audit_details={"source": "ai_triage_approval", "decision_id": decision.id},
    )
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

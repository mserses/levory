"""Değişiklik risk ajanı: deterministik risk skoru + LLM anlatısı, politika denetimli.

Akış: değişikliği kilitle (yalnızca draft/assess) -> deterministik skor (CMDB etki analizi,
geçmiş başarısızlıklar, geri dönüş planı) -> LLM (hızlı model; yalnızca Türkçe anlatı, kontrol
listesi ve en fazla ±10 gerekçeli düzeltme) -> nihai skor [0, 100] -> policy.evaluate ->
AIDecision + audit -> (yalnızca AUTO_APPLIED ise) risk alanları yazılır.

Ajan hiçbir zaman durum geçişi yapmaz ve onay vermez. Riski yükselten öneri düşük risklidir
(gölge mod kapalı ve güven yeterliyse uygulanır); riski düşüren öneri yüksek risklidir ve
change_manager onayı ister (`services.changes.approve_ai_decision`).

Deterministik formül (0-100'e kırpılır):

| Bileşen | Puan |
|---|---|
| change_type | standart 5, normal 15, acil 30 |
| criticality | kök + etkilenen CI'ların en yüksek kritikliği: 1 → 35, 2 → 25, 3 → 10, 4/yok → 0 |
| production | kök CI'lardan biri prod ise 10 |
| business_services | etki analizindeki iş hizmeti başına 5, en fazla 20 |
| failure_history | aynı CI'larda son 180 günde sonuçlanan değişikliklerin başarısız oranı × 20 |
| recent_incidents | son 30 günde kök CI'lara bağlı (`incident_cis`) olay başına 5, en fazla 15 (bağ yoksa 0) |
| backout_plan | yok 10, zayıf (< 20 karakter ya da < 3 kelime) 5 |
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, models, policy
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import ChangeType, CIEnvironment, DecisionOutcome, RiskLevel
from serviszeka.llm import LLMClient, LLMError
from serviszeka.models import AIDecision, ChangeRequest, Incident, change_affected_cis
from serviszeka.privacy import mask_pii
from serviszeka.services import changes as change_service
from serviszeka.services import cmdb
from serviszeka.services.sla import ensure_utc

logger = logging.getLogger(__name__)

AGENT = "change_risk"
ACTION = "assess_risk"
ACTOR = "ai:change_risk"

FAILURE_WINDOW = timedelta(days=180)
INCIDENT_WINDOW = timedelta(days=30)
MAX_ADJUSTMENT = 10
MAX_INPUT_SUMMARY = 2000

TYPE_POINTS: dict[ChangeType, int] = {
    ChangeType.STANDARD: 5,
    ChangeType.NORMAL: 15,
    ChangeType.EMERGENCY: 30,
}
CRITICALITY_POINTS: dict[int, int] = {1: 35, 2: 25, 3: 10, 4: 0}
PRODUCTION_POINTS = 10
SERVICE_POINTS, SERVICE_CAP = 5, 20
FAILURE_MAX = 20
INCIDENT_POINTS, INCIDENT_CAP = 5, 15
BACKOUT_MISSING, BACKOUT_WEAK = 10, 5
WEAK_BACKOUT_CHARS, WEAK_BACKOUT_WORDS = 20, 3

NO_INCIDENT_LINK_NOTE = (
    "Olay–CI bağı modelde yok; son 30 gün olay bileşeni 0 olarak alındı."
)

SYSTEM_PROMPT = """Sen ServisZeka'nın ITIL 4 değişiklik risk ajanısın.
Risk skoru sana deterministik olarak hesaplanmış verilir; skoru sen belirlemezsin.
Görevin, Türkçe olarak:
- narrative: CAB için kısa, somut bir risk anlatısı (en fazla 5 cümle),
- checklist: uygulama öncesi doğrulanacak risk maddeleri (en fazla 8 kısa madde),
- adjustment: yalnızca verilen bileşenlerin kaçırdığı somut bir neden varsa -10 ile +10 arası
  tam sayı düzeltme, yoksa 0,
- justification: düzeltmenin gerekçesi (düzeltme 0 değilse zorunlu),
- confidence: değerlendirmene güvenin (0-1).
Durum geçişi ya da onay önerme; maskelenmiş kişisel verileri ([EPOSTA] vb.) tahmin etme.

Yanıt şeması (JSON):
{"narrative": str, "checklist": [str], "adjustment": int, "justification": str,
 "confidence": float}"""


class RiskNarrative(BaseModel):
    """LLM çıktısının doğrulanmış hali."""

    model_config = ConfigDict(extra="ignore")

    narrative: str = Field(max_length=4000)
    checklist: list[str] = Field(default_factory=list, max_length=20)
    adjustment: int = 0
    justification: str = Field(default="", max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("narrative", "justification", mode="before")
    @classmethod
    def _strict_str(cls, v: Any) -> Any:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("metin bekleniyor")  # noqa: TRY004 (pydantic ValueError bekler)
        return v.strip()

    @field_validator("checklist")
    @classmethod
    def _clean_items(cls, v: list[str]) -> list[str]:
        return [s.strip()[:300] for s in v if s and s.strip()]

    @field_validator("adjustment", mode="before")
    @classmethod
    def _round_adjustment(cls, v: Any) -> Any:
        if isinstance(v, float) and math.isfinite(v):
            return round(v)
        return v


# --- deterministik skor --------------------------------------------------------------------


def clamp_score(score: int) -> int:
    return max(0, min(100, int(score)))


def backout_points(plan: str | None) -> int:
    text = (plan or "").strip()
    if not text:
        return BACKOUT_MISSING
    if len(text) < WEAK_BACKOUT_CHARS or len(text.split()) < WEAK_BACKOUT_WORDS:
        return BACKOUT_WEAK
    return 0


def score_breakdown(
    *,
    change_type: ChangeType,
    highest_criticality: int | None,
    production: bool,
    business_services: int,
    failure_share: float,
    recent_incidents: int,
    backout_plan: str | None,
) -> dict[str, int]:
    """Belgelenmiş formülün bileşenleri (toplamı kırpılmadan önceki skordur)."""
    return {
        "change_type": TYPE_POINTS[ChangeType(change_type)],
        "criticality": CRITICALITY_POINTS.get(highest_criticality or 4, 0),
        "production": PRODUCTION_POINTS if production else 0,
        "business_services": min(SERVICE_CAP, SERVICE_POINTS * business_services),
        "failure_history": round(FAILURE_MAX * max(0.0, min(1.0, failure_share))),
        "recent_incidents": min(INCIDENT_CAP, INCIDENT_POINTS * recent_incidents),
        "backout_plan": backout_points(backout_plan),
    }


def _ended_at(change: ChangeRequest) -> datetime | None:
    moment = change.actual_end or change.closed_at or change.updated_at
    return ensure_utc(moment) if moment else None


async def _failure_history(
    session: AsyncSession, change: ChangeRequest, ci_ids: list[str], now: datetime
) -> tuple[int, int]:
    """(sonuçlanan değişiklik sayısı, başarısız olanlar) — aynı CI'lar, son 180 gün."""
    if not ci_ids:
        return 0, 0
    # Alt sorgu (JOIN + DISTINCT değil): PostgreSQL JSON sütunlarında DISTINCT yapamaz
    sharing = select(change_affected_cis.c.change_id).where(change_affected_cis.c.ci_id.in_(ci_ids))
    stmt = select(ChangeRequest).where(
        ChangeRequest.id.in_(sharing),
        ChangeRequest.id != change.id,
        ChangeRequest.implementation_outcome.is_not(None),
    )
    cutoff = now - FAILURE_WINDOW
    total = failed = 0
    for past in (await session.scalars(stmt)).all():
        ended = _ended_at(past)
        if ended is None or ended < cutoff or ended > now:
            continue
        total += 1
        # Geri alınan (rolled back) uygulama "failed" sonucu ile kaydedilir
        if past.implementation_outcome == "failed":
            failed += 1
    return total, failed


async def _recent_incidents(
    session: AsyncSession, ci_ids: list[str], now: datetime
) -> int | None:
    """Son 30 günde kök CI'lara bağlı farklı olay sayısı; olay–CI bağı yoksa None."""
    link = getattr(models, "incident_cis", None)
    if link is None:
        return None
    if not ci_ids:
        return 0
    stmt = (
        select(Incident.id, Incident.created_at)
        .join(link, link.c.incident_id == Incident.id)
        .where(link.c.ci_id.in_(ci_ids))
        .distinct()
    )
    cutoff = now - INCIDENT_WINDOW
    return sum(
        1
        for _, created in (await session.execute(stmt)).all()
        if created is not None and cutoff <= ensure_utc(created) <= now
    )


async def deterministic_risk(
    session: AsyncSession, change: ChangeRequest, *, now: datetime
) -> dict[str, Any]:
    roots = list(change.affected_cis)
    ci_ids = [c.id for c in roots]
    highest: int | None = None
    services: list[str] = []
    affected_count = 0
    truncated = False
    if roots:
        impact = await cmdb.impact_analysis(session, ci_ids)
        highest = impact.highest_criticality
        services = sorted(s.name for s in impact.business_services)
        affected_count = len(impact.affected)
        truncated = impact.truncated
        if truncated:
            # Analiz sınırda kesildi: görülmeyen CI'lar en kritik kabul edilir (temkinli)
            highest = 1
    production = any(c.environment == CIEnvironment.PROD for c in roots)
    past_total, past_failed = await _failure_history(session, change, ci_ids, now)
    share = past_failed / past_total if past_total else 0.0
    incidents = await _recent_incidents(session, ci_ids, now)
    factors = score_breakdown(
        change_type=ChangeType(change.change_type),
        highest_criticality=highest,
        production=production,
        business_services=len(services),
        failure_share=share,
        recent_incidents=incidents or 0,
        backout_plan=change.backout_plan,
    )
    score = clamp_score(sum(factors.values()))
    return {
        "score": score,
        "level": change_service.level_for_score(score).value,
        "factors": factors,
        "inputs": {
            "change_type": ChangeType(change.change_type).value,
            "affected_ci_ids": ci_ids,
            "highest_criticality": highest,
            "production": production,
            "affected_count": affected_count,
            "impact_truncated": truncated,
            "business_services": services,
            "past_changes": past_total,
            "past_failures": past_failed,
            "failure_window_days": FAILURE_WINDOW.days,
            # None = olay–CI bağı yok, ölçülemedi (bileşen 0)
            "recent_incidents": incidents,
            "incident_window_days": INCIDENT_WINDOW.days,
            "backout_plan_present": bool((change.backout_plan or "").strip()),
        },
        "notes": [NO_INCIDENT_LINK_NOTE] if incidents is None else [],
    }


# --- LLM ------------------------------------------------------------------------------------


def _change_text(change: ChangeRequest) -> str:
    return "\n".join(
        [
            f"Numara: {change.number}",
            f"Başlık: {change.title}",
            f"Açıklama: {change.description or '-'}",
            f"Tür: {ChangeType(change.change_type).value}",
            f"Uygulama planı: {change.implementation_plan or '-'}",
            f"Geri dönüş planı: {change.backout_plan or '-'}",
            f"Test planı: {change.test_plan or '-'}",
            f"Etkilenen CI'lar: {', '.join(c.name for c in change.affected_cis) or '-'}",
        ]
    )


def _user_prompt(masked_text: str, det: dict[str, Any]) -> str:
    summary = {"score": det["score"], "level": det["level"], "factors": det["factors"],
               "inputs": det["inputs"], "notes": det["notes"]}
    return (
        f"Değişiklik:\n{masked_text}\n\n"
        f"Deterministik risk değerlendirmesi:\n{json.dumps(summary, ensure_ascii=False, indent=2)}"
    )


async def _narrate(
    llm: LLMClient, model: str, masked_text: str, det: dict[str, Any]
) -> tuple[RiskNarrative | None, dict[str, Any]]:
    """(doğrulanmış çıktı ya da None, kayda yazılacak LLM bölümü)."""
    try:
        raw = await llm.complete_json(
            system=SYSTEM_PROMPT, user=_user_prompt(masked_text, det), model=model
        )
    except LLMError as exc:
        return None, {"error": f"LLM çağrısı başarısız: {exc}"}
    try:
        result = RiskNarrative.model_validate(raw)
    except ValidationError as exc:
        return None, {
            "error": "LLM çıktısı şemaya uymuyor",
            "details": json.loads(exc.json(include_url=False, include_input=False)),
            "raw": raw,
        }
    requested = result.adjustment
    adjustment = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, requested))
    if not result.justification:
        adjustment = 0  # gerekçesiz düzeltme uygulanmaz
    return result, {
        "narrative": result.narrative,
        "checklist": result.checklist,
        "requested_adjustment": requested,
        "adjustment": adjustment,
        "justification": result.justification,
        "llm_confidence": result.confidence,
    }


# --- ana akış --------------------------------------------------------------------------------


async def assess(
    session: AsyncSession,
    change: ChangeRequest,
    llm: LLMClient,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
) -> AIDecision:
    """Değişikliğin riskini değerlendirir; her durumda AIDecision döner (commit etmez).

    Risk alanları yalnızca politika AUTO_APPLIED derse yazılır; durum ve onaylar asla değişmez.
    `InvalidTransitionError`: değişiklik draft/assess dışında (yetkilendirme başladıktan sonra
    risk değiştirmek onayları geçersiz kılardı).
    """
    settings = settings or get_settings()
    now = ensure_utc(now) if now else datetime.now(UTC)
    model = getattr(llm, "model_name", None) or settings.llm_fast_model

    change = await change_service._lock(session, change.id, None)
    if change.state not in change_service.AI_RISK_STATES:
        raise change_service.InvalidTransitionError(
            "Yapay zekâ risk değerlendirmesi yalnızca taslak/değerlendirme aşamasında yapılabilir."
        )
    base_version = change.version

    det = await deterministic_risk(session, change, now=now)
    masked = mask_pii(_change_text(change))
    result, llm_section = await _narrate(llm, model, masked, det)

    adjustment = llm_section.get("adjustment", 0)
    final = clamp_score(det["score"] + adjustment)
    confidence = result.confidence if result is not None else 0.0
    current = change.risk_score if change.risk_score is not None else det["score"]
    # Yükseltme (ya da aynı skor) düşük risk; düşürme her zaman insan onayı ister
    action_risk = RiskLevel.LOW if final >= current else RiskLevel.HIGH

    if result is None:
        outcome = DecisionOutcome.ESCALATED
        reason = f"{llm_section['error']}; insana yönlendirildi."
    else:
        verdict = policy.evaluate(
            agent=AGENT, action=ACTION, confidence=confidence, risk=action_risk, settings=settings
        )
        outcome, reason = verdict.outcome, verdict.reason

    proposed = {
        "risk_score": final,
        "risk_level": change_service.level_for_score(final).value,
    }
    decision = AIDecision(
        change_id=change.id,
        agent=AGENT,
        action=ACTION,
        input_summary=masked[:MAX_INPUT_SUMMARY],
        output={
            "proposed": proposed,
            "current_risk_score": current,
            "change_version": base_version,
            "action_risk": action_risk.value,
            "deterministic": det,
            "llm": llm_section,
            "policy_reason": reason,
            "applied": False,
        },
        rationale=llm_section.get("narrative", ""),
        sources=det["inputs"]["affected_ci_ids"],
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
        action="ai_decision.change_risk",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={
            "change_id": change.id,
            "change_number": change.number,
            "agent": AGENT,
            "outcome": outcome.value,
            "confidence": confidence,
            "model": model,
            "deterministic_score": det["score"],
            "adjustment": adjustment,
            "proposed": proposed,
            "action_risk": action_risk.value,
            "reason": reason,
        },
    )
    if outcome == DecisionOutcome.AUTO_APPLIED:
        # Yalnızca risk alanları; uygunluğunu yitiren standart değişiklik normal'e çevrilir
        await change_service._set_risk(
            session, change, final, ACTOR, "ai_auto", {"decision_id": decision.id}
        )
        change.version += 1
        change.updated_at = datetime.now(UTC)
        decision.output = {**decision.output, "applied": True}
        await session.flush()
    return decision

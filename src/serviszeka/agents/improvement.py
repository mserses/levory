"""Sürekli iyileştirme (ITIL 4 Continual Improvement) ajanı: deterministik sinyaller, fikir önerisi,
uygulama sonrası ölçüm.

Ajan veritabanına **doğrudan yazmaz**: her yazma Faz 3 araç boru hattından (`tools.execute_tool`)
yapay zekâ aktörü `ai:csi` ile geçer (`policy.evaluate` -> `AIDecision` -> `audit.record`; yalnızca
AUTO_APPLIED ise çalışır, aksi halde insan onayı için `ActionRequest`). Ajan iyileştirmeyi asla
onaylamaz, başlatmaz, kapatmaz ya da reddetmez (bunlar araç değildir; servis `ai:` aktörü reddeder).
LLM (`serviszeka.llm`) yalnızca Türkçe gerekçe metnini yazar; sayılar, eşikler ve kararlar
deterministiktir ve kayda `evidence` olarak işlenir.

1. `detect_signals(session, window_days, now)` — salt okuma. Pencere `W = [now - gün, now)`,
   önceki eşit pencere `P` (services/kpi.py):

   | Sinyal (anahtar) | Kural | KPI (kırılım) / hedef | Kaynak |
   |---|---|---|---|
   | `mttr_increase:assignment_group:<g>` | grubun MTTR'si `P`'ye göre > %20 arttı (iki pencerede ≥ 5 olay) | `mttr` (grup) / `P` değeri | kpi |
   | `sla_breaches:category:<c>` | kategoride `W`'de ≥ 3 çözüm SLA ihlali ve uyum < %90 | `sla_resolve_compliance` (kategori) / 90 | kpi |
   | `ai_triage_acceptance:category:<c>` | kategoride ≥ 5 triage kararı, kabul < %60 | `ai_triage_acceptance` (kategori) / 80 | ai |
   | `reopen_rate:assignment_group:<g>` | grupta ≥ 5 çözüm/yeniden açma, oran > %15 | `reopen_rate` (grup) / 10 | kpi |
   | `kb_unhelpful:<makale>` | yayındaki makale `W`'de ≥ 5 olayda önerildi, hiçbiri ilk temasta (yeniden atama/açma olmadan) çözülmedi | `fcr_rate` / — | ai |
   | `sla_forecast:assignment_group:<g>` | grupta ihlal olasılığı ≥ 0.7 olan ≥ 3 açık olay (`sla_forecast`) | `sla_resolve_compliance` (grup) / 90 | kpi |
   | `known_error_no_fix:<sorun>` | ≥ 14 gündür `known_error`, bağlı kalıcı çözüm (RFC) yok | `mttr` (kategori) / taban × 0.8 | problem |
   | `pir_failed_change:<değişiklik>` | `W`'de PIR notuyla kapanan başarısız değişiklik | `change_success_rate` (tür) / 90 | pir |

2. `propose_improvements(...)` — her yeni sinyal için `csi.create_improvement` (R1). Aynı sinyal
   açık bir kayıtta, bekleyen bir istekte ya da `SZ_CSI_COOLDOWN_DAYS` içinde kapanan/reddedilen
   kayıtta varsa yeniden önerilmez. Ölçüm penceresi sinyal penceresine eşittir.
3. `measure_due(...)` — uygulaması biten ve ölçüm penceresi dolan kayıt için
   `csi.measure_improvement` (R1): önce/sonra KPI, ölçülen değer, hedefe ulaşıldı mı.
4. `run_review(...)` — 2 + 3 (zamanlayıcı işi `csi_review`, günde bir).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import ROLE_AI_AGENT, CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import (
    ActionRequestState,
    ImprovementSource,
    IncidentStatus,
    ProblemState,
)
from serviszeka.llm import LLMClient, LLMError
from serviszeka.models import (
    ActionRequest,
    AIDecision,
    ChangeRequest,
    Incident,
    KnowledgeArticle,
    Problem,
)
from serviszeka.privacy import mask_pii
from serviszeka.services import improvements as svc
from serviszeka.services import kpi, sla_forecast
from serviszeka.services.improvement_schemas import MAX_DESCRIPTION, SignalRead
from serviszeka.services.sla import ensure_utc

AGENT = "csi"
ACTOR = CurrentUser("ai:csi", frozenset({ROLE_AI_AGENT}))
MODEL = "deterministic-csi"

MIN_SAMPLE = 5
MTTR_INCREASE_PCT = 20.0
SLA_MIN_BREACHES = 3
SLA_TARGET = 90.0
AI_ACCEPTANCE_MIN = 60.0
AI_ACCEPTANCE_TARGET = 80.0
REOPEN_MAX = 15.0
REOPEN_TARGET = 10.0
KB_MIN_SUGGESTIONS = 5
FORECAST_MIN_PROBABILITY = 0.7
FORECAST_MIN_AT_RISK = 3
KNOWN_ERROR_MIN_AGE_DAYS = 14
KNOWN_ERROR_MTTR_REDUCTION = 0.8
CHANGE_SUCCESS_TARGET = 90.0
MEASURE_CONFIDENCE = 0.95
MAX_NARRATIVE = 4000
MAX_PIR_TEXT = 500

# Fake LLM (llm/fake.py) bu başlığa bakarak anlatı üretir
CSI_SECTION = "Sürekli iyileştirme önerisi (anlatı):"

SYSTEM_PROMPT = (
    "Sen bir ITIL 4 sürekli iyileştirme uzmanısın. Verilen deterministik sinyal ve kanıt "
    "sayılarına dayanarak 2-4 cümlelik Türkçe bir iyileştirme gerekçesi yaz: sorun ne, neden "
    "önemli, hangi yönde bir iyileştirme denenebilir. Sayıları değiştirme, yeni sayı uydurma, "
    "kişisel veri yazma. Yalnızca gerekçe yaz; onay ya da karar verme. "
    'Şema: {"narrative": "..."}'
)


@dataclass
class Signal:
    key: str
    kind: str
    source: ImprovementSource
    title: str
    summary: str
    evidence: dict[str, Any]
    kpi_key: str | None = None
    dimension: tuple[str, str] | None = None
    baseline_value: float | None = None
    target_value: float | None = None
    benefit: int = 3
    effort: int = 3
    confidence: float = 0.7
    problem_id: str | None = None
    change_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def score(self) -> float:
        return svc.priority_score(self.benefit, self.effort)

    def to_read(self) -> SignalRead:
        return SignalRead(
            key=self.key,
            kind=self.kind,
            source=self.source,
            title=self.title,
            kpi_key=self.kpi_key,
            kpi_dimension=self.dimension[0] if self.dimension else None,
            kpi_dimension_value=self.dimension[1] if self.dimension else None,
            baseline_value=self.baseline_value,
            target_value=self.target_value,
            benefit_estimate=self.benefit,
            effort=self.effort,
            confidence=self.confidence,
            problem_id=self.problem_id,
            change_id=self.change_id,
            evidence=self.evidence,
            summary=self.summary,
        )

    def evidence_text(self) -> str:
        parts = [
            f"{k}={v}" for k, v in self.evidence.items() if isinstance(v, int | float | str | bool)
        ]
        return "; ".join(parts)

    def create_args(self, description: str, measure_days: int) -> dict[str, Any]:
        args: dict[str, Any] = {
            "title": self.title[:300],
            "description": description[:MAX_DESCRIPTION],
            "source": self.source.value,
            "benefit_estimate": self.benefit,
            "effort": self.effort,
            "measure_days": measure_days,
            "evidence": self.evidence,
            "signal_key": self.key,
        }
        if self.kpi_key:
            args["kpi_key"] = self.kpi_key
        if self.dimension:
            args["kpi_dimension"], args["kpi_dimension_value"] = self.dimension
        if self.target_value is not None:
            args["target_value"] = self.target_value
        if self.problem_id:
            args["problem_id"] = self.problem_id
        if self.change_id:
            args["change_id"] = self.change_id
        return args


def _windows(now: datetime, days: int) -> tuple[kpi.Window, kpi.Window]:
    w = kpi.Window.ending(now, days)
    return w, w.previous()


def _stats(result: kpi.KPIResult, dim: str) -> dict[str, Any]:
    return {s.key: s for s in result.breakdown.get(dim, []) if s.key is not None}


def _win(w: kpi.Window) -> dict[str, str]:
    return {"start": w.start.isoformat(), "end": w.end.isoformat()}


# --- sinyaller ------------------------------------------------------------------------------------


async def _mttr_signals(session, w, prev, now) -> list[Signal]:
    cur = _stats(await kpi.compute(session, "mttr", w, now), "assignment_group")
    old = _stats(await kpi.compute(session, "mttr", prev, now), "assignment_group")
    out = []
    for group, stat in sorted(cur.items()):
        before = old.get(group)
        if stat.value is None or stat.count < MIN_SAMPLE or before is None:
            continue
        if before.value is None or before.value <= 0 or before.count < MIN_SAMPLE:
            continue
        pct = round(100.0 * (stat.value - before.value) / before.value, 1)
        if pct <= MTTR_INCREASE_PCT:
            continue
        out.append(
            Signal(
                key=f"mttr_increase:assignment_group:{group}",
                kind="mttr_increase",
                source=ImprovementSource.KPI,
                title=f"{group} grubunda MTTR artışı (+%{pct:.0f})",
                summary=(
                    f"{group} grubunun MTTR'si {before.value} saatten {stat.value} saate çıktı "
                    f"(+{pct}%; {before.count} -> {stat.count} olay)."
                ),
                evidence={
                    "current": stat.value, "previous": before.value, "delta_pct": pct,
                    "count": stat.count, "previous_count": before.count,
                    "window": _win(w), "previous_window": _win(prev),
                },
                kpi_key="mttr",
                dimension=("assignment_group", group),
                baseline_value=stat.value,
                target_value=before.value,
                benefit=4,
                effort=3,
                confidence=round(min(0.9, 0.6 + 0.3 * min(pct, 100.0) / 100.0), 2),
            )
        )  # fmt: skip
    return out


async def _sla_signals(session, w, now) -> list[Signal]:
    result = await kpi.compute(session, "sla_resolve_compliance", w, now)
    out = []
    for category, stat in sorted(_stats(result, "category").items()):
        if stat.value is None:
            continue
        breached = round(stat.count * (1 - stat.value / 100.0))
        if breached < SLA_MIN_BREACHES or stat.value >= SLA_TARGET:
            continue
        out.append(
            Signal(
                key=f"sla_breaches:category:{category}",
                kind="sla_breaches",
                source=ImprovementSource.KPI,
                title=f"{category} kategorisinde tekrarlayan çözüm SLA ihlalleri",
                summary=(
                    f"{category} kategorisinde son pencerede {breached} çözüm SLA ihlali; uyum "
                    f"%{stat.value} ({stat.count} olay)."
                ),
                evidence={
                    "breached": breached, "count": stat.count, "compliance": stat.value,
                    "window": _win(w),
                },
                kpi_key="sla_resolve_compliance",
                dimension=("category", category),
                baseline_value=stat.value,
                target_value=SLA_TARGET,
                benefit=4,
                effort=3,
                confidence=0.8,
            )
        )  # fmt: skip
    return out


async def _ai_signals(session, w, now) -> list[Signal]:
    result = await kpi.compute(session, "ai_triage_acceptance", w, now)
    out = []
    for category, stat in sorted(_stats(result, "category").items()):
        if stat.value is None or stat.count < MIN_SAMPLE or stat.value >= AI_ACCEPTANCE_MIN:
            continue
        out.append(
            Signal(
                key=f"ai_triage_acceptance:category:{category}",
                kind="ai_triage_acceptance",
                source=ImprovementSource.AI,
                title=f"{category} kategorisinde düşük yapay zekâ triage kabulü",
                summary=(
                    f"{category} kategorisinde {stat.count} triage kararından kabul oranı "
                    f"%{stat.value}."
                ),
                evidence={"acceptance": stat.value, "decisions": stat.count, "window": _win(w)},
                kpi_key="ai_triage_acceptance",
                dimension=("category", category),
                baseline_value=stat.value,
                target_value=AI_ACCEPTANCE_TARGET,
                benefit=3,
                effort=2,
                confidence=0.75,
            )
        )  # fmt: skip
    return out


async def _reopen_signals(session, w, now) -> list[Signal]:
    result = await kpi.compute(session, "reopen_rate", w, now)
    out = []
    for group, stat in sorted(_stats(result, "assignment_group").items()):
        if stat.value is None or stat.count < MIN_SAMPLE or stat.value <= REOPEN_MAX:
            continue
        reopened = round(stat.count * stat.value / 100.0)
        out.append(
            Signal(
                key=f"reopen_rate:assignment_group:{group}",
                kind="reopen_rate",
                source=ImprovementSource.KPI,
                title=f"{group} grubunda yüksek yeniden açılma oranı",
                summary=(
                    f"{group} grubunda {stat.count} çözülen/yeniden açılan olaydan {reopened} "
                    f"tanesi yeniden açıldı (%{stat.value})."
                ),
                evidence={
                    "rate": stat.value, "reopened": reopened, "population": stat.count,
                    "window": _win(w),
                },
                kpi_key="reopen_rate",
                dimension=("assignment_group", group),
                baseline_value=stat.value,
                target_value=REOPEN_TARGET,
                benefit=3,
                effort=2,
                confidence=0.75,
            )
        )  # fmt: skip
    return out


def _helped(inc: Incident | None, events: list[Any]) -> bool:
    """İlk temasta çözüm vekili: çözüldü, yeniden atanmadı, yeniden açılmadı."""
    if inc is None or inc.resolved_at is None or inc.status == IncidentStatus.CANCELLED:
        return False
    return not any(kpi.is_reassignment(e.details) or kpi.is_reopen(e.details) for e in events)


async def _kb_signals(session, w) -> list[Signal]:
    decisions = (
        await session.scalars(
            select(AIDecision).where(
                AIDecision.agent == "service_desk",
                AIDecision.action == "classify",
                AIDecision.incident_id.is_not(None),
                w.contains(AIDecision.created_at),
            )
        )
    ).all()
    suggested: dict[str, set[str]] = defaultdict(set)
    for d in decisions:
        for article_id in d.sources or []:
            suggested[str(article_id)].add(d.incident_id)
    candidates = {a: ids for a, ids in suggested.items() if len(ids) >= KB_MIN_SUGGESTIONS}
    if not candidates:
        return []
    articles = {
        a.id: a
        for a in (
            await session.scalars(
                select(KnowledgeArticle).where(KnowledgeArticle.id.in_(list(candidates)))
            )
        ).all()
        if a.state == "published"
    }
    incident_ids = sorted({i for ids in candidates.values() for i in ids})
    incidents = {
        i.id: i
        for i in (await session.scalars(select(Incident).where(Incident.id.in_(incident_ids)))).all()
    }
    history = await kpi.incident_history(session, incident_ids)
    out = []
    for article_id, ids in sorted(candidates.items()):
        article = articles.get(article_id)
        if article is None:
            continue
        helped = sum(_helped(incidents.get(i), history.get(i, [])) for i in ids)
        if helped:
            continue
        resolved = sum(1 for i in ids if incidents.get(i) and incidents[i].resolved_at)
        out.append(
            Signal(
                key=f"kb_unhelpful:{article_id}",
                kind="kb_unhelpful",
                source=ImprovementSource.AI,
                title=f"{article.number} makalesi önerildiği olaylarda işe yaramıyor",
                summary=(
                    f"{article.number} '{article.title}' son pencerede {len(ids)} olayda önerildi; "
                    f"hiçbiri ilk temasta çözülmedi ({resolved} çözülen)."
                ),
                evidence={
                    "article_number": article.number, "suggested": len(ids), "helped": 0,
                    "resolved": resolved, "window": _win(w),
                },
                kpi_key="fcr_rate",
                benefit=2,
                effort=1,
                confidence=0.7,
            )
        )  # fmt: skip
    return out


async def _forecast_signals(session, now) -> list[Signal]:
    report = await sla_forecast.forecast(
        session, now=now, min_probability=FORECAST_MIN_PROBABILITY, limit=1000
    )
    by_group: dict[str, list[Any]] = defaultdict(list)
    for item in report.items:
        if item.assignment_group and item.probability is not None:
            by_group[item.assignment_group].append(item)
    out = []
    for group, items in sorted(by_group.items()):
        if len(items) < FORECAST_MIN_AT_RISK:
            continue
        probs = [i.probability for i in items]
        mean = round(sum(probs) / len(probs), 3)
        out.append(
            Signal(
                key=f"sla_forecast:assignment_group:{group}",
                kind="sla_forecast",
                source=ImprovementSource.KPI,
                title=f"{group} grubunda SLA ihlal riski yüksek açık olaylar",
                summary=(
                    f"{group} grubunda {len(items)} açık olayın çözüm SLA'sını kaçırma olasılığı "
                    f"≥ {FORECAST_MIN_PROBABILITY} (ortalama {mean})."
                ),
                evidence={
                    "at_risk": len(items), "mean_probability": mean,
                    "max_probability": round(max(probs), 3),
                    "incidents": sorted(i.number for i in items)[:20],
                },
                kpi_key="sla_resolve_compliance",
                dimension=("assignment_group", group),
                target_value=SLA_TARGET,
                benefit=4,
                effort=2,
                confidence=0.7,
            )
        )  # fmt: skip
    return out


async def _known_error_signals(session, now, days) -> list[Signal]:
    cutoff = now - timedelta(days=KNOWN_ERROR_MIN_AGE_DAYS)
    rows = (
        await session.scalars(
            select(Problem)
            .where(Problem.state == ProblemState.KNOWN_ERROR)
            .order_by(Problem.number)
        )
    ).all()
    out = []
    for problem in rows:
        if problem.created_at is None or ensure_utc(problem.created_at) > cutoff or problem.changes:
            continue
        age = (now - ensure_utc(problem.created_at)).days
        dimension = ("category", problem.category) if problem.category else None
        baseline, _ = await svc.metric_value(
            session, "mttr", kpi.Window(now - timedelta(days=days), now),
            dimension=dimension[0] if dimension else None,
            dimension_value=dimension[1] if dimension else None, now=now,
        )  # fmt: skip
        target = None if baseline is None else round(baseline * KNOWN_ERROR_MTTR_REDUCTION, 3)
        out.append(
            Signal(
                key=f"known_error_no_fix:{problem.id}",
                kind="known_error_no_fix",
                source=ImprovementSource.PROBLEM,
                title=f"{problem.number} bilinen hatası için kalıcı çözüm yok",
                summary=(
                    f"{problem.number} '{problem.title}' {age} gündür bilinen hata; bağlı kalıcı "
                    f"çözüm (RFC) yok, {len(problem.incidents)} olay bağlı."
                ),
                evidence={
                    "problem_number": problem.number, "age_days": age,
                    "incidents": len(problem.incidents), "mttr_baseline": baseline,
                },
                kpi_key="mttr",
                dimension=dimension,
                baseline_value=baseline,
                target_value=target,
                benefit=4,
                effort=4,
                confidence=0.7,
                problem_id=problem.id,
            )
        )  # fmt: skip
    return out


async def _pir_signals(session, w) -> list[Signal]:
    rows = (
        await session.scalars(
            select(ChangeRequest)
            .where(
                ChangeRequest.implementation_outcome == "failed",
                ChangeRequest.review_notes.is_not(None),
                ChangeRequest.closed_at.is_not(None),
                w.contains(ChangeRequest.closed_at),
            )
            .order_by(ChangeRequest.number)
        )
    ).all()
    out = []
    for change in rows:
        notes = " ".join((change.review_notes or "").split())
        if not notes:
            continue
        change_type = getattr(change.change_type, "value", change.change_type)
        pir = mask_pii(notes[:MAX_PIR_TEXT])
        out.append(
            Signal(
                key=f"pir_failed_change:{change.id}",
                kind="pir_failed_change",
                source=ImprovementSource.PIR,
                title=f"PIR: {change.number} başarısız değişiklikten dersler",
                summary=f"{change.number} ({change_type}) başarısız oldu; PIR: {pir}",
                evidence={"change_number": change.number, "change_type": change_type, "pir": pir},
                kpi_key="change_success_rate",
                dimension=("change_type", change_type),
                target_value=CHANGE_SUCCESS_TARGET,
                benefit=3,
                effort=3,
                confidence=0.7,
                change_id=change.id,
            )
        )  # fmt: skip
    return out


async def detect_signals(
    session: AsyncSession, *, window_days: int | None = None, now: datetime | None = None
) -> list[Signal]:
    """Tüm deterministik sinyaller (salt okuma), öncelik puanına göre azalan."""
    now = ensure_utc(now) if now else datetime.now(UTC)
    days = window_days or get_settings().csi_window_days
    w, prev = _windows(now, days)
    signals = [
        *await _mttr_signals(session, w, prev, now),
        *await _sla_signals(session, w, now),
        *await _ai_signals(session, w, now),
        *await _reopen_signals(session, w, now),
        *await _kb_signals(session, w),
        *await _forecast_signals(session, now),
        *await _known_error_signals(session, now, days),
        *await _pir_signals(session, w),
    ]
    return sorted(signals, key=lambda s: (-s.score, s.key))


# --- anlatı (LLM) ---------------------------------------------------------------------------------


def narrative_prompt(signal: Signal) -> str:
    lines = [
        CSI_SECTION,
        f"Sinyal: {signal.kind}",
        f"Başlık: {signal.title}",
        f"Özet: {signal.summary}",
        f"Kanıt: {json.dumps(signal.evidence, ensure_ascii=False, default=str)}",
    ]
    return mask_pii("\n".join(lines))


async def write_narrative(
    signal: Signal, llm: LLMClient | None, settings: Settings
) -> tuple[str | None, str]:
    """LLM gerekçesi (yoksa/başarısızsa None) ve model adı."""
    if llm is None:
        return None, MODEL
    try:
        raw = await llm.complete_json(
            system=SYSTEM_PROMPT, user=narrative_prompt(signal), model=settings.llm_fast_model
        )
    except LLMError:
        return None, MODEL
    text = raw.get("narrative") if isinstance(raw, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None, MODEL
    text = mask_pii(" ".join(text.split()))[:MAX_NARRATIVE]
    return text, getattr(llm, "model_name", None) or settings.llm_fast_model


def describe(signal: Signal, narrative: str | None) -> str:
    body = narrative or f"Otomatik tespit: {signal.summary}"
    return f"{body}\n\nKanıt: {signal.evidence_text()}"[:MAX_DESCRIPTION]


# --- öneri ve ölçüm -------------------------------------------------------------------------------


async def _pending_args(session: AsyncSession, tool: str, key: str) -> set[str]:
    rows = await session.scalars(
        select(ActionRequest).where(
            ActionRequest.tool == tool, ActionRequest.state == ActionRequestState.PENDING
        )
    )
    return {str((ar.args or {}).get(key)) for ar in rows.all() if (ar.args or {}).get(key)}


async def propose_improvements(
    session: AsyncSession,
    *,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    window_days: int | None = None,
    signals: list[Signal] | None = None,
) -> list[Any]:
    """Yeni her sinyal için `csi.create_improvement` (R1) önerir; `ToolResult` listesi (commit yok)."""
    from serviszeka.tools.executor import execute_tool

    settings = settings or get_settings()
    now = ensure_utc(now) if now else datetime.now(UTC)
    days = window_days or settings.csi_window_days
    if signals is None:
        signals = await detect_signals(session, window_days=days, now=now)
    pending = await _pending_args(session, "csi.create_improvement", "signal_key")
    results = []
    for signal in signals:
        if signal.key in pending or await svc.signal_blocked(
            session, signal.key, now=now, cooldown_days=settings.csi_cooldown_days
        ):
            continue
        narrative, model = await write_narrative(signal, llm, settings)
        results.append(
            await execute_tool(
                session,
                ACTOR,
                "csi.create_improvement",
                signal.create_args(describe(signal, narrative), days),
                reason=f"{signal.kind} sinyali: {signal.summary}"[:1000],
                confidence=signal.confidence,
                model=model,
                settings=settings,
                now=now,
            )
        )
    return results


async def measure_due(
    session: AsyncSession, *, settings: Settings | None = None, now: datetime | None = None
) -> list[Any]:
    """Ölçüm penceresi dolan uygulanmış iyileştirmeler için `csi.measure_improvement` (R1)."""
    from serviszeka.tools.executor import execute_tool

    settings = settings or get_settings()
    now = ensure_utc(now) if now else datetime.now(UTC)
    pending = await _pending_args(session, "csi.measure_improvement", "improvement_id")
    results = []
    for imp in await svc.due_for_measurement(session, now):
        if imp.id in pending:
            continue
        results.append(
            await execute_tool(
                session,
                ACTOR,
                "csi.measure_improvement",
                {"improvement_id": imp.id},
                reason=f"{imp.number}: ölçüm penceresi doldu ({imp.measure_days} gün).",
                confidence=MEASURE_CONFIDENCE,
                model=MODEL,
                settings=settings,
                now=now,
            )
        )
    return results


async def run_review(
    session: AsyncSession,
    *,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Sinyalleri önerir ve ölçümü gelen iyileştirmeleri ölçer (commit çağıranın işi)."""
    from serviszeka.tools.executor import ToolStatus

    settings = settings or get_settings()
    now = ensure_utc(now) if now else datetime.now(UTC)
    signals = await detect_signals(session, window_days=settings.csi_window_days, now=now)
    proposed = await propose_improvements(
        session, llm=llm, settings=settings, now=now, signals=signals
    )
    measured = await measure_due(session, settings=settings, now=now)
    return {
        "signals": len(signals),
        "proposed": sum(r.status == ToolStatus.EXECUTED for r in proposed),
        "measured": sum(r.status == ToolStatus.EXECUTED for r in measured),
        "pending_approval": sum(
            r.status == ToolStatus.PENDING_APPROVAL for r in [*proposed, *measured]
        ),
    }

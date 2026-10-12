"""Hizmet Masası ajanının sınıflandırma doğruluğunu etiketli veri setiyle ölçer.

Faz 1 kapısı: kayıtların en az %70'i doğru yönlendiriliyor olmalı. "Doğru yönlendirme"
= kind, category ve assignment_group üçünün de doğru olması. Öncelik (etki x aciliyet)
ayrıca raporlanır.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from serviszeka.agents import service_desk
from serviszeka.config import Settings, get_settings
from serviszeka.db import Base
from serviszeka.domain.enums import Impact, Urgency
from serviszeka.domain.priority import calculate_priority
from serviszeka.llm import LLMClient
from serviszeka.services import incidents as incident_service
from serviszeka.services.schemas import IncidentCreate

LABEL_FIELDS: tuple[str, ...] = (
    "kind",
    "category",
    "service",
    "assignment_group",
    "impact",
    "urgency",
)
ROUTING_FIELDS: tuple[str, ...] = ("kind", "category", "assignment_group")
GATE_THRESHOLD = 0.70


class _SilentNotifier:
    """Değerlendirmede nöbetçiye bildirim gitmez."""

    async def notify(self, event: str, payload: dict[str, Any]) -> None:
        return None


@dataclass
class TicketResult:
    id: str
    gold: dict[str, str]
    predicted: dict[str, str | None]
    outcome: str
    confidence: float
    seconds: float
    error: str | None = None

    @property
    def routing_correct(self) -> bool:
        return all(self.predicted.get(f) == self.gold[f] for f in ROUTING_FIELDS)


@dataclass
class EvalReport:
    total: int
    routing_accuracy: float
    exact_match: float
    priority_accuracy: float
    field_accuracy: dict[str, float]
    outcomes: dict[str, int]
    errors: int
    mean_seconds: float
    gate_passed: bool
    category_confusions: list[tuple[str, str, int]] = field(default_factory=list)
    tickets: list[dict[str, Any]] = field(default_factory=list)


def load_dataset(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _priority(labels: dict[str, Any]) -> str | None:
    try:
        return calculate_priority(Impact(labels["impact"]), Urgency(labels["urgency"])).value
    except (KeyError, ValueError):
        return None


def summarize(results: list[TicketResult]) -> EvalReport:
    n = len(results) or 1
    field_acc = {f: sum(r.predicted.get(f) == r.gold[f] for r in results) / n for f in LABEL_FIELDS}
    routing = sum(r.routing_correct for r in results) / n
    exact = sum(all(r.predicted.get(f) == r.gold[f] for f in LABEL_FIELDS) for r in results) / n
    prio = sum(_priority(r.predicted) == _priority(r.gold) for r in results) / n
    confusions = Counter(
        (r.gold["category"], str(r.predicted.get("category")))
        for r in results
        if r.predicted.get("category") != r.gold["category"]
    )
    return EvalReport(
        total=len(results),
        routing_accuracy=round(routing, 4),
        exact_match=round(exact, 4),
        priority_accuracy=round(prio, 4),
        field_accuracy={k: round(v, 4) for k, v in field_acc.items()},
        outcomes=dict(Counter(r.outcome for r in results)),
        errors=sum(r.error is not None for r in results),
        mean_seconds=round(sum(r.seconds for r in results) / n, 3),
        gate_passed=bool(results) and routing >= GATE_THRESHOLD,
        category_confusions=[(g, p, c) for (g, p), c in confusions.most_common(10)],
        tickets=[asdict(r) | {"routing_correct": r.routing_correct} for r in results],
    )


async def evaluate(
    dataset: list[dict[str, Any]],
    llm: LLMClient,
    *,
    settings: Settings | None = None,
    concurrency: int = 4,
) -> EvalReport:
    """Her kaydı gerçek triage akışından geçirir (maskeleme, doğrulama, politika dahil).

    Gölge mod zorunlu: değerlendirme hiçbir kaydı değiştirmez, yalnızca öneriyi okur.
    """
    settings = (settings or get_settings()).model_copy(update={"ai_shadow_mode": True})
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp) / 'eval.db'}")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            sem = asyncio.Semaphore(concurrency)
            # SQLite tek yazar: kayıt oluşturma sırayla, LLM çağrıları paralel
            write_lock = asyncio.Lock()

            async def run_one(row: dict[str, Any]) -> TicketResult:
                async with sem:
                    started = time.perf_counter()
                    error = None
                    output: dict[str, Any] = {}
                    outcome, confidence = "error", 0.0
                    try:
                        async with maker() as session:
                            async with write_lock:
                                incident = await incident_service.create_incident(
                                    session,
                                    IncidentCreate(
                                        title=row["title"],
                                        description=row["description"],
                                        caller="eval",
                                    ),
                                    actor="eval",
                                )
                                await session.commit()
                            decision = await service_desk.triage(
                                session,
                                incident,
                                llm,
                                settings=settings,
                                notifier=_SilentNotifier(),
                            )
                            output = decision.output or {}
                            outcome = decision.outcome.value
                            confidence = decision.confidence
                            if output.get("error"):
                                error = str(output["error"])
                    except Exception as exc:  # noqa: BLE001 — tek kayıt hatası tüm koşuyu durdurmaz
                        error = f"{type(exc).__name__}: {exc}"
                    return TicketResult(
                        id=row["id"],
                        gold=row["labels"],
                        predicted={f: output.get(f) for f in LABEL_FIELDS},
                        outcome=outcome,
                        confidence=confidence,
                        seconds=round(time.perf_counter() - started, 3),
                        error=error,
                    )

            results = await asyncio.gather(*(run_one(r) for r in dataset))
        finally:
            await engine.dispose()
    return summarize(list(results))


def render_markdown(report: EvalReport, *, model: str = "") -> str:
    status = "GEÇTİ" if report.gate_passed else "KALDI"
    lines = [
        "# Hizmet Masası sınıflandırma değerlendirmesi",
        "",
        f"- Model: {model or '-'}",
        f"- Kayıt sayısı: {report.total}",
        (
            f"- **Doğru yönlendirme (kind + category + assignment_group): "
            f"%{report.routing_accuracy * 100:.1f}**, kapı %{GATE_THRESHOLD * 100:.0f}: **{status}**"
        ),
        f"- Öncelik doğruluğu (etki x aciliyet): %{report.priority_accuracy * 100:.1f}",
        f"- Altı alanın tümü doğru: %{report.exact_match * 100:.1f}",
        f"- Hata: {report.errors}, ortalama süre: {report.mean_seconds} sn",
        "",
        "| Alan | Doğruluk |",
        "| --- | --- |",
        *(f"| {k} | %{v * 100:.1f} |" for k, v in report.field_accuracy.items()),
        "",
        "| Politika sonucu | Kayıt |",
        "| --- | --- |",
        *(f"| {k} | {v} |" for k, v in sorted(report.outcomes.items())),
    ]
    if report.category_confusions:
        lines += ["", "| Gerçek kategori | Tahmin | Kayıt |", "| --- | --- | --- |"]
        lines += [f"| {g} | {p} | {c} |" for g, p, c in report.category_confusions]
    return "\n".join(lines) + "\n"

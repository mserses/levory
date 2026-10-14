"""Sürekli iyileştirme (CSI) testleri için sentetik veri: olaylar, kararlar, sorun, PIR'li değişiklik."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

from serviszeka import audit
from serviszeka.auth import CurrentUser
from serviszeka.domain.enums import (
    ChangeState,
    ChangeType,
    DecisionOutcome,
    IncidentStatus,
    Priority,
    ProblemState,
    RiskLevel,
    TicketKind,
)
from serviszeka.models import AIDecision, ChangeRequest, Incident, KnowledgeArticle, Problem

NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)
D = timedelta(days=1)

AGENT = CurrentUser("ayse", frozenset({"agent"}))
APPROVER = CurrentUser("onayci", frozenset({"approver"}))
AI_CSI = CurrentUser("ai:csi", frozenset({"ai_agent"}))

_seq = count(1)


def inc(
    *,
    created: datetime,
    resolved: datetime | None = None,
    priority: Priority = Priority.P3,
    group: str | None = "network_ops",
    category: str | None = "ag",
    status: IncidentStatus | None = None,
    resolve_due: datetime | None = None,
) -> Incident:
    if status is None:
        status = IncidentStatus.RESOLVED if resolved else IncidentStatus.IN_PROGRESS
    n = next(_seq)
    return Incident(
        number=f"INC{n:07d}",
        kind=TicketKind.INCIDENT,
        title=f"Olay {n}",
        caller="ali",
        category=category,
        service=None,
        priority=priority,
        status=status,
        assignment_group=group,
        created_at=created,
        updated_at=created,
        resolved_at=resolved,
        responded_at=created + timedelta(minutes=10),
        sla_paused_seconds=0,
        resolve_due_at=resolve_due,
    )


async def add(session, *rows: Any) -> list[Any]:
    session.add_all(rows)
    await session.flush()
    return list(rows)


async def mttr_rows(
    session, *, group: str, category: str, hours: float, start: datetime, n: int = 6
) -> list[Incident]:
    """`n` olay, `start`'tan itibaren günde bir açılıp `hours` saatte çözülür."""
    rows = []
    for i in range(n):
        created = start + i * D
        rows.append(
            inc(created=created, resolved=created + timedelta(hours=hours), group=group,
                category=category)
        )  # fmt: skip
    return await add(session, *rows)


class Frozen:
    """`audit.record` zamanını sabitler (denetim geçmişine dayalı KPI'lar için)."""

    def __init__(self, monkeypatch) -> None:
        self.now = NOW
        clock = self

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now

        monkeypatch.setattr(audit, "datetime", _DT)

    async def update(self, session, incident_id: str, at: datetime, changes: dict) -> None:
        self.now = at
        await audit.record(
            session,
            actor="ayse",
            action="incident.update",
            entity_type="incident",
            entity_id=incident_id,
            details={"changes": changes},
        )


async def seed_signals(session, frozen: Frozen) -> dict[str, Any]:
    """Her CSI sinyalinden birini tetikleyen veri (pencere: son 30 gün, NOW'a kadar)."""
    out: dict[str, Any] = {}
    # 1) dba grubunun MTTR'si önceki pencerede 4 sa, bu pencerede 8 sa (+%100)
    await mttr_rows(session, group="dba", category="veritabani", hours=4, start=NOW - 55 * D, n=5)
    await mttr_rows(session, group="dba", category="veritabani", hours=8, start=NOW - 25 * D, n=5)
    # 2) yazıcı kategorisinde 4 çözüm SLA ihlali (uyum %0)
    for i in range(4):
        created = NOW - (20 - i) * D
        await add(
            session,
            inc(created=created, resolved=created + 30 * H, resolve_due=created + 8 * H,
                group="end_user_support", category="yazici"),
        )  # fmt: skip
    # 3) + 5) e-posta kategorisinde 5 triage kararı uygulanmadı; önerilen KB makalesi hiç işe yaramadı
    article = KnowledgeArticle(number="KB0000901", title="Outlook profili", body="Adımlar",
                               state="published")  # fmt: skip
    await add(session, article)
    out["article_id"] = article.id
    for i in range(5):
        created = NOW - (10 - i) * D
        (i_row,) = await add(
            session, inc(created=created, group="collaboration", category="eposta_isbirligi")
        )
        await add(
            session,
            AIDecision(
                incident_id=i_row.id, agent="service_desk", action="classify",
                input_summary="[maskeli]", output={"proposed": {"category": "ag"}},
                sources=[article.id], model="fake", confidence=0.5,
                outcome=DecisionOutcome.NEEDS_APPROVAL,
                created_at=created + timedelta(minutes=1),
            ),
        )  # fmt: skip
    # 4) servis masası grubunda 5 çözümden 2'si yeniden açıldı (%40)
    for i in range(5):
        created = NOW - (15 - i) * D
        (row,) = await add(
            session,
            inc(created=created, resolved=created + 2 * H, group="service_desk_l1",
                category="erisim_kimlik"),
        )  # fmt: skip
        await frozen.update(session, row.id, created + 2 * H, {"status": ["in_progress", "resolved"]})
        if i < 2:
            await frozen.update(
                session, row.id, created + 5 * H, {"status": ["resolved", "in_progress"]}
            )
    # 6) altyapı grubunda geçmişte hep ihlal; şu an 3 açık olay ihlal riski taşıyor
    for i in range(12):
        created = NOW - (100 + i) * D
        await add(
            session,
            inc(created=created, resolved=created + 24 * H, resolve_due=created + 8 * H,
                group="infra_cloud", category="altyapi_bulut"),
        )  # fmt: skip
    for _ in range(3):
        await add(
            session,
            inc(created=NOW - 2 * H, resolve_due=NOW + 6 * H, group="infra_cloud",
                category="altyapi_bulut"),
        )  # fmt: skip
    # 7) 20 gündür kalıcı çözümü (RFC) olmayan bilinen hata
    problem = Problem(
        number="PRB0000901", title="VPN kopmaları", state=ProblemState.KNOWN_ERROR,
        priority=Priority.P3, category="ag", workaround="İstemciyi yeniden başlatın",
        detected_by="ayse", created_by="ayse", created_at=NOW - 20 * D, updated_at=NOW - 20 * D,
    )  # fmt: skip
    await add(session, problem)
    out["problem_id"] = problem.id
    # 8) PIR notuyla kapanan başarısız değişiklik
    change = ChangeRequest(
        number="CHG0000901", title="Çekirdek anahtar yazılımı", change_type=ChangeType.NORMAL,
        state=ChangeState.CLOSED, risk_level=RiskLevel.MEDIUM, requested_by="mehmet",
        implementation_outcome="failed", review_notes="PIR: geri alma planı eksikti.",
        actual_start=NOW - 5 * D, actual_end=NOW - 5 * D + 2 * H, closed_at=NOW - 4 * D,
        created_at=NOW - 9 * D, updated_at=NOW - 4 * D,
    )  # fmt: skip
    await add(session, change)
    out["change_id"] = change.id
    await session.commit()
    return out


EXPECTED_SIGNALS = {
    "mttr_increase:assignment_group:dba",
    "sla_breaches:category:yazici",
    "ai_triage_acceptance:category:eposta_isbirligi",
    "reopen_rate:assignment_group:service_desk_l1",
    "sla_forecast:assignment_group:infra_cloud",
}

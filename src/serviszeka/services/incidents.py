"""ITIL 4 Olay Yönetimi (Incident Management) servis katmanı.

- Numara: INC + 7 hane, sıradaki en yüksek numaradan devam eder.
- Öncelik etki x aciliyet matrisinden hesaplanır; SLA vadeleri öncelikten türetilir.
- İyimser kilit: expected_version uyuşmazsa VersionConflictError.
- Her değişiklik sürümü artırır ve denetim zincirine yazılır.
- Fonksiyonlar flush eder, commit etmez.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.domain.enums import Impact, IncidentStatus, Priority, TicketKind, Urgency
from serviszeka.domain.priority import calculate_priority
from serviszeka.models import Incident
from serviszeka.services import sla
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate

S = IncidentStatus

# İzin verilen durum geçişleri (ITIL 4 olay yaşam döngüsü)
ALLOWED_TRANSITIONS: Mapping[IncidentStatus, frozenset[IncidentStatus]] = {
    S.NEW: frozenset({S.ASSIGNED, S.IN_PROGRESS, S.ON_HOLD, S.RESOLVED, S.CANCELLED}),
    S.ASSIGNED: frozenset({S.IN_PROGRESS, S.ON_HOLD, S.RESOLVED, S.CANCELLED}),
    S.IN_PROGRESS: frozenset({S.ASSIGNED, S.ON_HOLD, S.RESOLVED, S.CANCELLED}),
    S.ON_HOLD: frozenset({S.ASSIGNED, S.IN_PROGRESS, S.RESOLVED, S.CANCELLED}),
    # Çözülen olay yeniden açılabilir ya da kapatılabilir
    S.RESOLVED: frozenset({S.IN_PROGRESS, S.CLOSED}),
    S.CLOSED: frozenset(),
    S.CANCELLED: frozenset(),
}

# Güncellemede uygulanabilen alanlar; None ile temizlenemeyenler ayrıca listelenir
UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "caller",
        "impact",
        "urgency",
        "service",
        "category",
        "kind",
        "status",
        "assignee",
        "assignment_group",
        "resolution",
        "ai_confidence",
    }
)
_NON_NULLABLE: frozenset[str] = frozenset(
    {"title", "description", "caller", "impact", "urgency", "kind", "status"}
)
_ENUM_FIELDS: Mapping[str, type[Enum]] = {
    "impact": Impact,
    "urgency": Urgency,
    "kind": TicketKind,
    "status": IncidentStatus,
}

_NUMBER_RE = re.compile(r"^INC(\d+)$")


class IncidentError(Exception):
    """Olay servisi hatalarının tabanı."""


class IncidentNotFoundError(IncidentError, LookupError):
    def __init__(self, incident_id: str) -> None:
        super().__init__(f"Olay bulunamadı: {incident_id}")
        self.incident_id = incident_id


class VersionConflictError(IncidentError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Sürüm çakışması: beklenen {expected}, güncel {actual}. Kaydı yenileyip tekrar deneyin."
        )
        self.expected = expected
        self.actual = actual


# Kısa takma ad (görev tanımındaki ad)
VersionConflict = VersionConflictError


class InvalidTransitionError(IncidentError, ValueError):
    """İzin verilmeyen durum geçişi ya da eksik zorunlu alan."""


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return sla.ensure_utc(value).isoformat()
    return value


async def next_number(session: AsyncSession) -> str:
    """INC + 7 haneli sıradaki numara (tek ortak yardımcı; senkron motoru da kullanır)."""
    numbers = (
        await session.scalars(select(Incident.number).where(Incident.number.like("INC%")))
    ).all()
    highest = max((int(m.group(1)) for n in numbers if (m := _NUMBER_RE.match(n or ""))), default=0)
    return f"INC{highest + 1:07d}"


def apply_sla(incident: Incident, policy: sla.SLAPolicy | None = None) -> None:
    start = incident.created_at or _now()
    due = sla.compute_due_dates(incident.priority, start, policy)
    incident.response_due_at = due.response_due_at
    incident.resolve_due_at = due.resolve_due_at


async def create_incident(session: AsyncSession, data: IncidentCreate, actor: str) -> Incident:
    now = _now()
    impact = data.impact or Impact.LOW
    urgency = data.urgency or Urgency.LOW
    incident = Incident(
        number=await next_number(session),
        kind=data.kind or TicketKind.INCIDENT,
        title=data.title,
        description=data.description,
        caller=data.caller,
        category=data.category,
        service=data.service,
        impact=impact,
        urgency=urgency,
        priority=calculate_priority(impact, urgency),
        status=IncidentStatus.NEW,
        version=1,
        created_at=now,
        updated_at=now,
    )
    apply_sla(incident)
    session.add(incident)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="incident.create",
        entity_type="incident",
        entity_id=incident.id,
        details={
            "number": incident.number,
            "priority": incident.priority.value,
            "impact": impact.value,
            "urgency": urgency.value,
            "kind": incident.kind.value,
            "version": incident.version,
        },
    )
    return incident


async def get_incident(session: AsyncSession, incident_id: str) -> Incident | None:
    return await session.get(Incident, incident_id)


async def list_incidents(
    session: AsyncSession,
    *,
    status: IncidentStatus | str | None = None,
    priority: Priority | str | None = None,
    assignee: str | None = None,
    assignment_group: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Incident]:
    stmt = select(Incident)
    if status is not None:
        stmt = stmt.where(Incident.status == IncidentStatus(status))
    if priority is not None:
        stmt = stmt.where(Incident.priority == Priority(priority))
    if assignee is not None:
        stmt = stmt.where(Incident.assignee == assignee)
    if assignment_group is not None:
        stmt = stmt.where(Incident.assignment_group == assignment_group)
    stmt = stmt.order_by(Incident.created_at.desc(), Incident.number.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


def _check_transition(incident: Incident, new_status: IncidentStatus, resolution: str | None):
    current = IncidentStatus(incident.status)
    if new_status == current:
        return
    if new_status not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"Durum geçişi geçersiz: {current.value} -> {new_status.value}"
        )
    if new_status == S.RESOLVED and not (resolution or "").strip():
        raise InvalidTransitionError("Olayı çözmek için çözüm (resolution) metni zorunludur.")


async def apply_changes(
    session: AsyncSession,
    incident_id: str,
    changes: Mapping[str, Any],
    actor: str,
    expected_version: int | None = None,
    *,
    audit_details: Mapping[str, Any] | None = None,
) -> Incident:
    """Alan sözlüğünü kurallara göre uygular (update_incident ve ajanlar ortak kullanır)."""
    # Sürüm kontrolü kimlik haritasındaki bayat kopyaya değil, veritabanına karşı yapılır;
    # PostgreSQL'de satır kilidi (FOR UPDATE) kontrol ile yazma arasını atomik kılar.
    incident = await get_incident(session, incident_id)
    if incident is None:
        raise IncidentNotFoundError(incident_id)
    db_version = await session.scalar(
        select(Incident.version).where(Incident.id == incident_id).with_for_update()
    )
    if db_version != incident.version:
        await session.refresh(incident)  # başka bir işlem kaydı değiştirmiş
    if expected_version is not None and expected_version != incident.version:
        raise VersionConflictError(expected_version, incident.version)

    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"Güncellenemeyen alan(lar): {', '.join(sorted(unknown))}")

    norm: dict[str, Any] = {}
    for name, value in changes.items():
        if value is None and name in _NON_NULLABLE:
            continue
        if value is not None and name in _ENUM_FIELDS:
            value = _ENUM_FIELDS[name](value)
        norm[name] = value

    diff: dict[str, list[Any]] = {}

    def setf(name: str, value: Any) -> None:
        old = getattr(incident, name)
        if old != value:
            diff[name] = [_jsonable(old), _jsonable(value)]
            setattr(incident, name, value)

    now = _now()
    old_status = IncidentStatus(incident.status)
    new_status = norm.pop("status", old_status)
    resolution = norm.get("resolution", incident.resolution)
    _check_transition(incident, new_status, resolution)
    if old_status in (S.CLOSED, S.CANCELLED) and any(
        getattr(incident, k) != v for k, v in norm.items()
    ):
        raise InvalidTransitionError(f"{old_status.value} durumundaki olay değiştirilemez.")

    for name, value in norm.items():
        setf(name, value)

    # Öncelik yalnızca etki/aciliyet değiştiğinde yeniden hesaplanır (uzaktan gelen
    # öncelik ilgisiz bir güncellemede ezilmez); değişirse SLA vadeleri de güncellenir
    new_priority = calculate_priority(incident.impact, incident.urgency)
    if ("impact" in diff or "urgency" in diff) and new_priority != incident.priority:
        setf("priority", new_priority)
        start = incident.created_at or now
        due = sla.compute_due_dates(new_priority, start)
        setf("response_due_at", due.response_due_at)
        setf("resolve_due_at", due.resolve_due_at)

    if new_status != old_status:
        setf("status", new_status)
        # İlk yanıt: NEW durumundan ilk çıkış
        if old_status == S.NEW and incident.responded_at is None:
            setf("responded_at", now)
        if new_status == S.RESOLVED:
            setf("resolved_at", now)
        elif old_status == S.RESOLVED and new_status != S.CLOSED:
            # Yeniden açıldı: çözüm zamanı sıfırlanır
            setf("resolved_at", None)

    if not diff:
        return incident

    incident.version = (incident.version or 0) + 1
    incident.updated_at = now
    await session.flush()
    details: dict[str, Any] = {"changes": diff, "version": incident.version}
    if audit_details:
        details.update(audit_details)
    await audit.record(
        session,
        actor=actor,
        action="incident.update",
        entity_type="incident",
        entity_id=incident.id,
        details=details,
    )
    return incident


async def update_incident(
    session: AsyncSession,
    incident_id: str,
    data: IncidentUpdate,
    actor: str,
    expected_version: int | None = None,
) -> Incident:
    """Kısmi güncelleme; yalnızca gönderilen alanlar (exclude_unset) uygulanır."""
    changes = data.model_dump(exclude_unset=True)
    changes.pop("expected_version", None)  # IncidentPatch gövdesi verilirse
    return await apply_changes(session, incident_id, changes, actor, expected_version)


# Geriye dönük uyumluluk
_apply_sla = apply_sla
next_incident_number = next_number

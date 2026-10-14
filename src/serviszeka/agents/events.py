"""Olay yönetimi ajanı (ITIL 4 Monitoring & Event Management): bağıntılı uyarı grubundan olay.

Ajan veritabanına **doğrudan yazmaz**: her yazma Faz 3 araç boru hattından (`tools.execute_tool`)
yapay zekâ aktörü `ai:events` ile geçer (`policy.evaluate` -> `AIDecision` -> `audit.record`;
yalnızca AUTO_APPLIED ise çalışır, aksi halde insan onayı için `ActionRequest`).

`process_groups(session, group_ids)` — `services.events.ingest` sonrasında dokunulan her grup için:

| Durum | Eylem |
|---|---|
| Susturulmuş / birleştirilmiş grup | hiçbir şey |
| Olaysız, açık, eşik üstü önemde bastırılmamış ateşleyen uyarı var | `event.create_incident` (R1; P1/P2 olacaksa R2 -> onay) |
| Olaylı, yeni/çözülen uyarı var (`needs_note`) | `event.create_incident` (güncelleme: çalışma notu + CI bağı) |
| Olaylı ve tüm uyarılar çözüldü | not + `incident.resolve` **önerisi** (R2, asla otomatik değil) |

Olay fırtınası: son bir saatte `SZ_EVENTS_MAX_INCIDENTS_PER_HOUR` olay açıldıysa P3-P5 açılmaz
(`skipped`, neden "olay fırtınası"); P1/P2 yine insan onayına önerilir.

Tekilleştirme: grup başına bekleyen bir `event.create_incident` isteği varsa yenisi açılmaz;
reddedilmiş açma isteği yeniden önerilmez (insan kararı). Çözüm önerisi olay başına bir kez
(süresi dolan hariç). Güven: kök CI çözülmüşse 0.9, değilse 0.7 (deterministik kurallar).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import policy
from serviszeka.auth import ROLE_AI_AGENT, CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import ActionRequestState, EventGroupState, IncidentStatus
from serviszeka.models import ActionRequest, EventGroup, Incident
from serviszeka.services import events

AGENT = "events"
ACTOR = CurrentUser("ai:events", frozenset({ROLE_AI_AGENT}))
MODEL = "deterministic-correlation"
CREATE_TOOL = "event.create_incident"
RESOLVE_TOOL = "incident.resolve"
STORM_WINDOW = timedelta(hours=1)
_OPEN_INCIDENT = frozenset(
    {
        IncidentStatus.NEW,
        IncidentStatus.ASSIGNED,
        IncidentStatus.IN_PROGRESS,
        IncidentStatus.ON_HOLD,
    }
)


@dataclass
class GroupOutcome:
    group_id: str
    action: str  # skipped | create | update | propose_resolve
    status: str | None = None  # ToolStatus değeri
    reason: str = ""
    action_request_id: str | None = None
    incident_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}


_ACTIVE = (ActionRequestState.PENDING, ActionRequestState.APPROVED)


async def _create_blocked(session: AsyncSession, group: EventGroup) -> str | None:
    """Bekleyen (ya da reddedilmiş açma) istek varsa nedeni.

    İndeksli sütunla (`ActionRequest.event_group_id`, göç 0014) yalnızca bu grubun istekleri
    okunur; tüm isteklerin JSON argümanları Python'da taranmaz.
    """
    states = [*_ACTIVE]
    if group.incident_id is None:
        states.append(ActionRequestState.REJECTED)
    ar = await session.scalar(
        select(ActionRequest)
        .where(
            ActionRequest.event_group_id == group.id,
            ActionRequest.tool == CREATE_TOOL,
            ActionRequest.requested_by == ACTOR.username,
            ActionRequest.state.in_(states),
        )
        # Bekleyen/onaylanan istek reddedilenden önce gelir (neden metni)
        .order_by(ActionRequest.state.in_(_ACTIVE).desc(), ActionRequest.created_at)
        .limit(1)
    )
    if ar is None:
        return None
    if ActionRequestState(ar.state) in _ACTIVE:
        return f"bekleyen istek {ar.number}"
    return f"olay açma isteği reddedildi ({ar.number})"


async def _resolve_proposed(session: AsyncSession, incident_id: str) -> bool:
    found = await session.scalar(
        select(ActionRequest.id)
        .where(
            ActionRequest.incident_id == incident_id,  # indeksli bağ sütunu
            ActionRequest.tool == RESOLVE_TOOL,
            ActionRequest.requested_by == ACTOR.username,
            ActionRequest.state != ActionRequestState.EXPIRED,
        )
        .limit(1)
    )
    return found is not None


async def _storm(
    session: AsyncSession, group: EventGroup, *, settings: Settings, now: datetime
) -> str | None:
    """Olay fırtınası: son bir saatte sınır kadar olay açıldıysa otomatik (P3-P5) açma durur.

    P1/P2 olacak grup yine önerilir (zaten R2: insan onayı); diğer gruplar açık kalır ve sonraki
    gönderimde (sınırın altına inince) işlenir.
    """
    limit = settings.events_max_incidents_per_hour
    opened = await events.incidents_opened_since(session, now - STORM_WINDOW)
    if opened < limit:
        return None
    plan = await events.incident_plan(session, group)
    if plan.priority in policy.URGENT_PRIORITIES:
        return None
    return f"olay fırtınası: son bir saatte {opened} olay açıldı (sınır {limit})"


def _confidence(group: EventGroup) -> float:
    return 0.9 if group.root_ci_id else 0.7


async def _call(
    session: AsyncSession,
    tool: str,
    args: dict[str, Any],
    *,
    group: EventGroup,
    reason: str,
    settings: Settings,
    now: datetime,
) -> Any:
    from serviszeka.tools import execute_tool

    return await execute_tool(
        session,
        ACTOR,
        tool,
        args,
        reason=reason,
        confidence=_confidence(group),
        model=MODEL,
        settings=settings,
        now=now,
    )


async def process_group(
    session: AsyncSession, group: EventGroup, *, settings: Settings, now: datetime
) -> GroupOutcome:
    out = GroupOutcome(group.id, "skipped", incident_id=group.incident_id)
    state = EventGroupState(group.state)
    if state == EventGroupState.MERGED:
        out.reason = "birleştirildi"
        return out
    if events.is_silenced(group, now):
        out.reason = "susturuldu"
        return out

    if group.incident_id is None:
        if not (
            events.is_actionable(group, settings, now)
            and await events.has_active_firing(session, group.id)
        ):
            out.reason = "eşik altı / bastırılmış / çözülmüş"
            return out
        blocked = await _create_blocked(session, group)
        if blocked:
            out.reason = blocked
            return out
        storm = await _storm(session, group, settings=settings, now=now)
        if storm:
            out.reason = storm
            return out
        r = await _call(
            session,
            CREATE_TOOL,
            {"group_id": group.id},
            group=group,
            reason=f"{group.number}: {group.event_count} bağıntılı uyarı ({group.severity}).",
            settings=settings,
            now=now,
        )
        out.action, out.status = "create", r.status.value
        out.action_request_id = r.action_request_id
        out.incident_id = (r.result or {}).get("incident_id") or group.incident_id
        return out

    if events.needs_note(group):
        if await _create_blocked(session, group):
            out.reason = "bekleyen not isteği"
        else:
            r = await _call(
                session,
                CREATE_TOOL,
                {"group_id": group.id},
                group=group,
                reason=f"{group.number}: yeni ya da çözülen uyarılar olaya not olarak eklenir.",
                settings=settings,
                now=now,
            )
            out.action, out.status = "update", r.status.value
            out.action_request_id = r.action_request_id

    if state == EventGroupState.RESOLVED:
        incident = await session.get(Incident, group.incident_id)
        if (
            incident is not None
            and IncidentStatus(incident.status) in _OPEN_INCIDENT
            and not await _resolve_proposed(session, incident.id)
        ):
            r = await _call(
                session,
                RESOLVE_TOOL,
                {
                    "incident_id": incident.id,
                    # Yapay zekâ yazması sürüm taşır (iyimser kilit): onaya kadar olay değişirse
                    # yürütme 409 ile durur, insan güncel kayda bakar
                    "expected_version": incident.version,
                    "resolution": (
                        f"İzleme: {group.number} grubundaki tüm uyarılar çözüldü. "
                        "Hizmetin normale döndüğünü doğrulayıp çözümü onaylayın."
                    ),
                },
                group=group,
                reason="Tüm uyarılar çözüldü; olayın çözümü önerilir (insan onayı).",
                settings=settings,
                now=now,
            )
            out.action, out.status = "propose_resolve", r.status.value
            out.action_request_id = r.action_request_id
    return out


async def process_groups(
    session: AsyncSession,
    group_ids: list[str],
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[GroupOutcome]:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    outcomes: list[GroupOutcome] = []
    for group_id in dict.fromkeys(group_ids):
        group = await session.get(EventGroup, group_id)
        if group is None:
            continue
        outcomes.append(await process_group(session, group, settings=settings, now=now))
    return outcomes

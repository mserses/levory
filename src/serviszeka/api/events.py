"""Faz 4 — izleme ve olay yönetimi uç noktaları (`/events`).

- `POST /events/alertmanager` (Prometheus Alertmanager webhook v4) ve `POST /events/generic`
  (belgelenmiş JSON, `services/event_schemas.GenericPayload`): **bearer belirteç değil**, paylaşılan
  gizli (`Authorization: Bearer <gizli>` / `X-ServisZeka-Token`) ya da HMAC-SHA256 imza
  (`X-ServisZeka-Signature` + `X-ServisZeka-Timestamp`) ile doğrulanır; `SZ_EVENTS_WEBHOOK_SECRET`
  boşsa her istek 401. Gövde sınırı `SZ_EVENTS_MAX_BODY_BYTES` (413), zaman damgası penceresi ve
  yinelenen teslimat (`duplicate: true`, hiçbir şey yazılmaz). Uyarılar kaydedilir, CMDB ile
  bağıntılanır, olay ajanı (`ai:events`) araç boru hattından olay açar/not ekler.
- `GET /events`, `GET /events/groups`, `GET /events/groups/{id}`: agent / admin (`AgentUser`).
- `POST /events/groups/{id}/acknowledge` (`event.acknowledge`, R1) ve `POST .../silence`
  (`event.silence`, R2 -> `ActionRequest`, başka bir onaylayan `/actions` üzerinden onaylar):
  agent / admin; araç boru hattından insan kimliğiyle (`execute_tool`), `AgentResultRead` döner.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.agents import events as event_agent
from serviszeka.auth import AgentUser, CurrentUser, get_current_user
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import EventGroupState, EventSeverity, EventStatus
from serviszeka.notifications import Notifier, get_notifier, send_pending
from serviszeka.services import events as svc
from serviszeka.services.event_schemas import (
    EventGroupDetailRead,
    EventGroupListRead,
    EventGroupRead,
    EventListRead,
    EventRead,
    WebhookResult,
)
from serviszeka.services.problem_schemas import AgentResultRead
from serviszeka.tools import ToolError, execute_tool

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(get_current_user)])
# Webhook'lar kendi paylaşılan gizli / HMAC doğrulamasını yapar (izleme sistemi OIDC kullanmaz)
webhook_router = APIRouter(prefix="/events", tags=["events"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_event_settings() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_event_settings)]


def get_event_notifier(settings: SettingsDep) -> Notifier:
    return get_notifier(settings)


NotifierDep = Annotated[Notifier, Depends(get_event_notifier)]

_UNAUTHORIZED = {"WWW-Authenticate": 'Bearer realm="serviszeka-events"'}


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_big = int(declared) > limit
        except ValueError:
            raise HTTPException(400, "Content-Length geçersiz.") from None
        if too_big:
            raise HTTPException(413, f"Gövde en fazla {limit} bayt olabilir.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"Gövde en fazla {limit} bayt olabilir.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _receive(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    notifier: Notifier,
    *,
    source: str,
    parse: Callable[[Any, datetime], list[svc.IncomingAlert]],
) -> WebhookResult:
    now = datetime.now(UTC)
    try:
        svc.check_preconditions(settings, request.headers)
    except svc.WebhookAuthError as exc:
        raise HTTPException(401, str(exc), headers=_UNAUTHORIZED) from exc
    body = await _read_body(request, settings.events_max_body_bytes)
    try:
        auth = svc.authenticate_webhook(settings, request.headers, body, source=source, now=now)
    except svc.WebhookAuthError as exc:
        raise HTTPException(401, str(exc), headers=_UNAUTHORIZED) from exc
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        # RecursionError: aşırı iç içe gövde (ör. 50 000 "[") 500 değil 422
        raise HTTPException(422, "Gövde geçerli JSON değil.") from exc
    try:
        alerts = parse(payload, now)
    except svc.EventPayloadError as exc:
        raise HTTPException(422, {"message": str(exc), "errors": exc.errors}) from exc
    except RecursionError as exc:
        raise HTTPException(422, "Gövde çok derin iç içe.") from exc
    if not await svc.register_delivery(session, auth, source=source, now=now, settings=settings):
        await session.commit()  # yalnızca süresi geçen teslimat kayıtlarının temizliği
        return WebhookResult(duplicate=True, received=len(alerts))
    result = await svc.ingest(
        session, alerts, settings=settings, now=now, delivery=auth.dedup_key[:16]
    )
    outcomes = await event_agent.process_groups(
        session, result.group_ids, settings=settings, now=now
    )
    await session.commit()
    await send_pending(session, notifier)
    return WebhookResult(
        received=result.received,
        created=result.created,
        updated=result.updated,
        ignored=result.ignored,
        group_ids=result.group_ids,
        actions=[o.to_dict() for o in outcomes],
    )


@webhook_router.post("/alertmanager", response_model=WebhookResult)
async def alertmanager_webhook(
    request: Request, session: SessionDep, settings: SettingsDep, notifier: NotifierDep
) -> WebhookResult:
    """Prometheus Alertmanager webhook (v4). Kimlik: paylaşılan gizli ya da HMAC imza."""
    return await _receive(
        request,
        session,
        settings,
        notifier,
        source="alertmanager",
        parse=lambda payload, _now: svc.parse_alertmanager(payload),
    )


@webhook_router.post("/generic", response_model=WebhookResult)
async def generic_webhook(
    request: Request, session: SessionDep, settings: SettingsDep, notifier: NotifierDep
) -> WebhookResult:
    """Genel JSON olay gövdesi (`{"events": [...]}`, bkz. docs/faz4.md)."""
    return await _receive(
        request,
        session,
        settings,
        notifier,
        source="generic",
        parse=lambda payload, now: svc.parse_generic(payload, now=now),
    )


# --- okuma (agent / admin) --------------------------------------------------------------------


@router.get("", response_model=EventListRead)
async def list_events(
    session: SessionDep,
    _user: AgentUser,
    status: EventStatus | None = None,
    severity: EventSeverity | None = None,
    group_id: Annotated[str | None, Query(max_length=36)] = None,
    ci_id: Annotated[str | None, Query(max_length=36)] = None,
    source: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventListRead:
    rows = await svc.list_events(
        session,
        status=status,
        severity=severity,
        group_id=group_id,
        ci_id=ci_id,
        source=source,
        limit=limit,
        offset=offset,
    )
    return EventListRead(items=[EventRead.model_validate(r) for r in rows])


@router.get("/groups", response_model=EventGroupListRead)
async def list_groups(
    session: SessionDep,
    _user: AgentUser,
    state: EventGroupState | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventGroupListRead:
    rows = await svc.list_groups(session, state=state, limit=limit, offset=offset)
    return EventGroupListRead(items=[EventGroupRead.model_validate(r) for r in rows])


@router.get("/groups/{group_id}", response_model=EventGroupDetailRead)
async def get_group(
    group_id: str, session: SessionDep, settings: SettingsDep, _user: AgentUser
) -> EventGroupDetailRead:
    try:
        group = await svc.get_group(session, group_id)
    except svc.EventGroupNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    corr = await svc.correlation(session, group, settings=settings, now=datetime.now(UTC))
    events = await svc.group_events(session, group.id)
    return EventGroupDetailRead(
        **corr.model_dump(), events=[EventRead.model_validate(e) for e in events]
    )


# --- insan eylemleri (agent / admin): araç boru hattından ---------------------------------------


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class SilenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: int = Field(ge=5, le=10080, description="Susturma süresi (dk, en fazla 7 gün)")
    comment: str = Field(min_length=1, max_length=500, description="Gerekçe")


async def _run_group_tool(
    session: AsyncSession,
    settings: Settings,
    notifier: Notifier,
    user: CurrentUser,
    group_id: str,
    tool: str,
    args: dict[str, Any],
) -> AgentResultRead:
    try:
        await svc.get_group(session, group_id)
    except svc.EventGroupNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        result = await execute_tool(
            session, user, tool, {"group_id": group_id, **args}, settings=settings
        )
    except ToolError as exc:
        await session.rollback()
        raise HTTPException(exc.status_code, exc.to_dict()) from exc
    await session.commit()
    await send_pending(session, notifier)
    return AgentResultRead.model_validate(
        {**result.to_dict(), "decision_id": result.decision_id,
         "action_request_id": result.action_request_id}
    )  # fmt: skip


@router.post("/groups/{group_id}/acknowledge", response_model=AgentResultRead)
async def acknowledge_group(
    group_id: str,
    body: AcknowledgeRequest,
    session: SessionDep,
    settings: SettingsDep,
    notifier: NotifierDep,
    user: AgentUser,
) -> AgentResultRead:
    """Grubu üstlenir (`event.acknowledge`, R1; kullanıcının kimliğiyle hemen çalışır)."""
    return await _run_group_tool(
        session, settings, notifier, user, group_id, "event.acknowledge", body.model_dump()
    )


@router.post("/groups/{group_id}/silence", response_model=AgentResultRead)
async def silence_group(
    group_id: str,
    body: SilenceRequest,
    session: SessionDep,
    settings: SettingsDep,
    notifier: NotifierDep,
    user: AgentUser,
) -> AgentResultRead:
    """Susturma isteği (`event.silence`, R2): bekleyen eylem isteği açılır, başka bir onaylayan
    `/actions/{id}/approve` ile onaylayınca grup susturulur."""
    return await _run_group_tool(
        session, settings, notifier, user, group_id, "event.silence", body.model_dump()
    )

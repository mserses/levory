"""Olay yönetimi, SLA ve Hizmet Masası ajanı uç noktaları."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import llm as llm_module
from serviszeka.agents import service_desk
from serviszeka.auth import AgentUser, ApproverUser, ReaderUser, get_current_user
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import IncidentStatus, Priority
from serviszeka.notifications import Notifier, get_notifier, send_pending
from serviszeka.services import incidents as svc
from serviszeka.services import sla
from serviszeka.services.schemas import (
    AIDecisionRead,
    ApproveRequest,
    IncidentCreate,
    IncidentPatch,
    IncidentRead,
    SLAAlertRead,
)

# Yönlendirici düzeyinde kimlik doğrulama zorunlu; uç noktalar ayrıca rol ister
router = APIRouter(
    prefix="/incidents", tags=["incidents"], dependencies=[Depends(get_current_user)]
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_llm_client() -> llm_module.LLMClient:
    """LLM bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    try:
        return llm_module.get_llm()
    except llm_module.LLMError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


def get_app_settings() -> Settings:
    return get_settings()


LLMDep = Annotated[llm_module.LLMClient, Depends(get_llm_client)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_oncall_notifier(settings: SettingsDep) -> Notifier:
    """Nöbetçi bildirim bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    return get_notifier(settings)


NotifierDep = Annotated[Notifier, Depends(get_oncall_notifier)]

_ETAG_RE = re.compile(r'^\s*(?:W/)?"?(\d+)"?\s*$')


def _etag(version: int) -> str:
    return f'"{version}"'


def _parse_if_match(value: str | None) -> int | None:
    if value is None or value.strip() in ("", "*"):
        return None
    m = _ETAG_RE.match(value)
    if not m:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'If-Match başlığı geçersiz (örn. "3").')
    return int(m.group(1))


async def _get_or_404(session: AsyncSession, incident_id: str):
    incident = await svc.get_incident(session, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Olay bulunamadı")
    return incident


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
async def create_incident(
    data: IncidentCreate, session: SessionDep, response: Response, user: AgentUser
) -> IncidentRead:
    incident = await svc.create_incident(session, data, user.username)
    await session.commit()
    await session.refresh(incident)
    response.headers["ETag"] = _etag(incident.version)
    return IncidentRead.model_validate(incident)


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    session: SessionDep,
    _user: ReaderUser,
    status_: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    priority: Priority | None = None,
    assignee: str | None = None,
    assignment_group: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncidentRead]:
    rows = await svc.list_incidents(
        session,
        status=status_,
        priority=priority,
        assignee=assignee,
        assignment_group=assignment_group,
        limit=limit,
        offset=offset,
    )
    return [IncidentRead.model_validate(r) for r in rows]


@router.get("/sla/alerts", response_model=list[SLAAlertRead])
async def sla_alerts(
    session: SessionDep, _user: ReaderUser, now: datetime | None = None
) -> list[SLAAlertRead]:
    alerts = await sla.check_breaches(session, now)
    return [SLAAlertRead.model_validate(a) for a in alerts]


@router.post("/decisions/{decision_id}/approve", response_model=AIDecisionRead)
async def approve_decision(
    decision_id: str,
    session: SessionDep,
    user: ApproverUser,
    body: ApproveRequest | None = None,
) -> AIDecisionRead:
    # Onaylayan her zaman belirteçteki kullanıcıdır; gövde yalnızca doğrulama içindir
    approver = user.username
    if body and body.approver and body.approver != approver:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Başkası adına onay verilemez")
    if await service_desk.get_decision(session, decision_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Karar bulunamadı")
    try:
        decision = await service_desk.approve_decision(session, decision_id, approver)
    except service_desk.StaleDecisionError as exc:
        await session.commit()  # red denetim kaydı kalıcı olmalı
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except service_desk.DecisionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except svc.InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await session.refresh(decision)
    return AIDecisionRead.model_validate(decision)


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: str, session: SessionDep, response: Response, _user: ReaderUser
) -> IncidentRead:
    incident = await _get_or_404(session, incident_id)
    response.headers["ETag"] = _etag(incident.version)
    return IncidentRead.model_validate(incident)


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: str,
    data: IncidentPatch,
    session: SessionDep,
    response: Response,
    user: AgentUser,
    if_match: Annotated[str | None, Header()] = None,
) -> IncidentRead:
    await _get_or_404(session, incident_id)
    expected = _parse_if_match(if_match)
    if expected is None:
        expected = data.expected_version
    try:
        incident = await svc.update_incident(
            session, incident_id, data, user.username, expected_version=expected
        )
    except svc.VersionConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except svc.InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await session.commit()
    await session.refresh(incident)
    response.headers["ETag"] = _etag(incident.version)
    return IncidentRead.model_validate(incident)


@router.post("/{incident_id}/triage", response_model=AIDecisionRead)
async def triage_incident(
    incident_id: str,
    session: SessionDep,
    llm: LLMDep,
    settings: SettingsDep,
    notifier: NotifierDep,
    _user: AgentUser,
) -> AIDecisionRead:
    incident = await _get_or_404(session, incident_id)
    decision = await service_desk.triage(session, incident, llm, settings=settings)
    await session.commit()
    # expire_on_commit=False: yanıt için yeniden okuma (ve açık işlem) gerekmez
    result = AIDecisionRead.model_validate(decision)
    # Nöbetçi yalnızca karar kalıcı olduktan sonra uyarılır; hata yanıtı bozmaz
    await send_pending(session, notifier)
    return result


@router.get("/{incident_id}/decisions", response_model=list[AIDecisionRead])
async def list_decisions(
    incident_id: str, session: SessionDep, _user: ReaderUser
) -> list[AIDecisionRead]:
    await _get_or_404(session, incident_id)
    rows = await service_desk.list_decisions(session, incident_id)
    return [AIDecisionRead.model_validate(r) for r in rows]

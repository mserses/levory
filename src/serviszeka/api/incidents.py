"""Olay yönetimi, SLA ve Hizmet Masası ajanı uç noktaları."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import llm as llm_module
from serviszeka.agents import service_desk
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import IncidentStatus, Priority
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

router = APIRouter(prefix="/incidents", tags=["incidents"])

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
ActorHeader = Annotated[str | None, Header(alias="X-Actor")]

_ETAG_RE = re.compile(r'^\s*(?:W/)?"?(\d+)"?\s*$')


def _actor(x_actor: str | None) -> str:
    return x_actor or "api"


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
    data: IncidentCreate, session: SessionDep, response: Response, x_actor: ActorHeader = None
) -> IncidentRead:
    incident = await svc.create_incident(session, data, _actor(x_actor))
    await session.commit()
    await session.refresh(incident)
    response.headers["ETag"] = _etag(incident.version)
    return IncidentRead.model_validate(incident)


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    session: SessionDep,
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
async def sla_alerts(session: SessionDep, now: datetime | None = None) -> list[SLAAlertRead]:
    alerts = await sla.check_breaches(session, now)
    return [SLAAlertRead.model_validate(a) for a in alerts]


@router.post("/decisions/{decision_id}/approve", response_model=AIDecisionRead)
async def approve_decision(
    decision_id: str,
    session: SessionDep,
    body: ApproveRequest | None = None,
    x_actor: ActorHeader = None,
) -> AIDecisionRead:
    approver = (body.approver if body else None) or x_actor
    if not approver:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Onaylayan kişi (approver veya X-Actor) zorunludur."
        )
    if await service_desk.get_decision(session, decision_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Karar bulunamadı")
    try:
        decision = await service_desk.approve_decision(session, decision_id, approver)
    except service_desk.DecisionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except svc.InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await session.refresh(decision)
    return AIDecisionRead.model_validate(decision)


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(incident_id: str, session: SessionDep, response: Response) -> IncidentRead:
    incident = await _get_or_404(session, incident_id)
    response.headers["ETag"] = _etag(incident.version)
    return IncidentRead.model_validate(incident)


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: str,
    data: IncidentPatch,
    session: SessionDep,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    x_actor: ActorHeader = None,
) -> IncidentRead:
    await _get_or_404(session, incident_id)
    expected = _parse_if_match(if_match)
    if expected is None:
        expected = data.expected_version
    try:
        incident = await svc.update_incident(
            session, incident_id, data, _actor(x_actor), expected_version=expected
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
    incident_id: str, session: SessionDep, llm: LLMDep, settings: SettingsDep
) -> AIDecisionRead:
    incident = await _get_or_404(session, incident_id)
    decision = await service_desk.triage(session, incident, llm, settings=settings)
    await session.commit()
    await session.refresh(decision)
    return AIDecisionRead.model_validate(decision)


@router.get("/{incident_id}/decisions", response_model=list[AIDecisionRead])
async def list_decisions(incident_id: str, session: SessionDep) -> list[AIDecisionRead]:
    await _get_or_404(session, incident_id)
    rows = await service_desk.list_decisions(session, incident_id)
    return [AIDecisionRead.model_validate(r) for r in rows]

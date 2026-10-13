"""Eylem istekleri (Faz 3): yapay zekâ/insan araç çağrılarının insan onayı kutusu.

Okuma ve karar: approver / admin; R3 istek bir değişikliğe bağlıysa change_manager da karar
verebilir (servis denetler). Talep eden kendi isteğine, yapay zekâ hiçbir isteğe karar veremez.
Ham argümanlar asla dönmez; yalnızca maskelenmiş `args_display`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import ActionApproverUser, get_current_user
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import ActionRequestState, ApprovalDecision, RiskClass
from serviszeka.domain.types import UTCDateTime
from serviszeka.models import ActionRequest
from serviszeka.notifications import Notifier, get_notifier, send_pending
from serviszeka.tools import actions as svc
from serviszeka.tools import registry

router = APIRouter(prefix="/actions", tags=["actions"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_action_settings() -> Settings:
    """Ayar bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_action_settings)]


def get_action_llm() -> Any:
    """LLM bağımlılığı: None -> araç gerekirse `serviszeka.llm.get_llm` ile tembel kurar."""
    return None


def get_action_notifier(settings: SettingsDep) -> Notifier:
    return get_notifier(settings)


class ActionApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    approver: str
    approver_roles: list[str]
    decision: ApprovalDecision
    comment: str
    created_at: UTCDateTime


class ActionRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    tool: str
    tool_description: str | None = Field(default=None, description="Aracın Türkçe açıklaması")
    risk_class: RiskClass
    state: ActionRequestState
    required_approvals: int
    requested_by: str = Field(description="ai:<ajan> ya da insan kullanıcı adı")
    reason: str
    confidence: float | None
    args_display: dict[str, Any] = Field(description="Maskelenmiş argümanlar (gösterim için)")
    incident_id: str | None
    change_id: str | None
    request_id: str | None
    decision_id: str | None
    result: dict[str, Any] | None
    error: str | None
    executed_by: str | None
    executed_at: UTCDateTime | None
    expires_at: UTCDateTime
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime
    approvals: list[ActionApprovalRead] = Field(default_factory=list)


class ActionDecisionBody(BaseModel):
    comment: str = Field(default="", max_length=2000)


async def _read(session: AsyncSession, ar: ActionRequest) -> ActionRequestRead:
    spec = registry.find(ar.tool)
    approvals = await svc.list_approvals(session, ar.id)
    return ActionRequestRead.model_validate(ar).model_copy(
        update={
            "tool_description": spec.description if spec else None,
            "approvals": [ActionApprovalRead.model_validate(a) for a in approvals],
        }
    )


def _http(exc: svc.ActionError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _get_or_404(session: AsyncSession, action_id: str) -> ActionRequest:
    ar = await svc.get_action(session, action_id)
    if ar is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eylem isteği bulunamadı")
    return ar


@router.get("", response_model=list[ActionRequestRead])
async def list_actions(
    session: SessionDep,
    _user: ActionApproverUser,
    state: ActionRequestState | None = None,
    tool: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ActionRequestRead]:
    """Eylem istekleri, en yeni önce. Süresi dolan bekleyen istekler önce `expired` yapılır."""
    if await svc.expire_due(session):
        await session.commit()
    rows = await svc.list_actions(session, state=state, tool=tool, limit=limit, offset=offset)
    return [await _read(session, r) for r in rows]


@router.get("/{action_id}", response_model=ActionRequestRead)
async def get_action(
    action_id: str, session: SessionDep, _user: ActionApproverUser
) -> ActionRequestRead:
    return await _read(session, await _get_or_404(session, action_id))


@router.post("/{action_id}/approve", response_model=ActionRequestRead)
async def approve_action(
    action_id: str,
    session: SessionDep,
    settings: SettingsDep,
    user: ActionApproverUser,
    llm: Annotated[Any, Depends(get_action_llm)],
    notifier: Annotated[Notifier, Depends(get_action_notifier)],
    body: ActionDecisionBody | None = None,
) -> ActionRequestRead:
    """Onay verir; onay kuralı sağlanınca istek onaylayan kimliğiyle hemen yürütülür."""
    try:
        ar = await svc.approve(
            session, action_id, user, (body.comment if body else ""), settings=settings, llm=llm
        )
    except svc.ActionExpiredError as exc:
        await session.commit()  # süre aşımı kalıcı olmalı
        raise _http(exc) from exc
    except svc.ActionError as exc:
        raise _http(exc) from exc
    await session.commit()
    result = await _read(session, ar)
    await send_pending(session, notifier)
    return result


@router.post("/{action_id}/reject", response_model=ActionRequestRead)
async def reject_action(
    action_id: str,
    session: SessionDep,
    user: ActionApproverUser,
    body: ActionDecisionBody | None = None,
) -> ActionRequestRead:
    try:
        ar = await svc.reject(session, action_id, user, (body.comment if body else ""))
    except svc.ActionExpiredError as exc:
        await session.commit()
        raise _http(exc) from exc
    except svc.ActionError as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, ar)

"""ITSM entegrasyon uç noktaları (ServiceNow / Jira SM, iki yönlü senkron)."""

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import AdminUser, ApproverUser, get_current_user
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import ExternalSystem
from serviszeka.domain.types import UTCDateTime
from serviszeka.integrations.base import ConnectorError, ITSMConnector
from serviszeka.integrations.jira_sm import JiraSMConnector
from serviszeka.integrations.servicenow import ServiceNowConnector
from serviszeka.integrations.servicenow_cmdb import ServiceNowCMDBConnector, pull_cmdb
from serviszeka.integrations.sync import CONFLICT_POLICIES, SyncEngine
from serviszeka.models import Incident, SyncEvent

# Pull/push/durum: admin; çakışma listeleme/çözme: approver (admin her şeyi yapabilir)
router = APIRouter(
    prefix="/integrations", tags=["integrations"], dependencies=[Depends(get_current_user)]
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_SYSTEM_LABELS = {ExternalSystem.SERVICENOW: "ServiceNow", ExternalSystem.JIRA_SM: "Jira SM"}


def is_configured(settings: Settings, system: ExternalSystem) -> bool:
    if system == ExternalSystem.SERVICENOW:
        return bool(
            settings.servicenow_instance_url
            and settings.servicenow_username
            and settings.servicenow_password
        )
    return bool(settings.jira_base_url and settings.jira_email and settings.jira_api_token)


def build_connectors(settings: Settings) -> dict[ExternalSystem, ITSMConnector]:
    """Ayarlarda yapılandırılmış sistemler için bağlayıcılar."""
    connectors: dict[ExternalSystem, ITSMConnector] = {}
    if is_configured(settings, ExternalSystem.SERVICENOW):
        connectors[ExternalSystem.SERVICENOW] = ServiceNowConnector(
            settings.servicenow_instance_url,  # type: ignore[arg-type]
            settings.servicenow_username,  # type: ignore[arg-type]
            settings.servicenow_password,  # type: ignore[arg-type]
        )
    if is_configured(settings, ExternalSystem.JIRA_SM):
        connectors[ExternalSystem.JIRA_SM] = JiraSMConnector(
            settings.jira_base_url,  # type: ignore[arg-type]
            settings.jira_email,  # type: ignore[arg-type]
            settings.jira_api_token,  # type: ignore[arg-type]
            service_desk_id=settings.jira_service_desk_id,
            request_type_id=settings.jira_request_type_id,
        )
    return connectors


async def get_connectors() -> AsyncIterator[dict[ExternalSystem, ITSMConnector]]:
    """FastAPI bağımlılığı: istek sonunda HTTP istemcilerini kapatır."""
    connectors = build_connectors(get_settings())
    try:
        yield connectors
    finally:
        for c in connectors.values():
            close = getattr(c, "aclose", None)
            if close:
                await close()


ConnectorsDep = Annotated[dict[ExternalSystem, ITSMConnector], Depends(get_connectors)]


async def get_servicenow_cmdb() -> AsyncIterator[ServiceNowCMDBConnector | None]:
    """ServiceNow CMDB bağlayıcısı (yapılandırılmamışsa None); testlerde geçersiz kılınır."""
    settings = get_settings()
    if not is_configured(settings, ExternalSystem.SERVICENOW):
        yield None
        return
    connector = ServiceNowCMDBConnector(
        settings.servicenow_instance_url,  # type: ignore[arg-type]
        settings.servicenow_username,  # type: ignore[arg-type]
        settings.servicenow_password,  # type: ignore[arg-type]
    )
    try:
        yield connector
    finally:
        await connector.aclose()


CMDBConnectorDep = Annotated[ServiceNowCMDBConnector | None, Depends(get_servicenow_cmdb)]


def _engine(session: AsyncSession, connectors: dict[ExternalSystem, ITSMConnector]) -> SyncEngine:
    policy = get_settings().sync_conflict_policy
    if policy not in CONFLICT_POLICIES:
        policy = "manual"
    return SyncEngine(session, connectors, conflict_policy=policy)


def _require(connectors: dict[ExternalSystem, ITSMConnector], system: ExternalSystem) -> None:
    if system not in connectors:
        raise HTTPException(
            status_code=400,
            detail=f"{_SYSTEM_LABELS[system]} entegrasyonu yapılandırılmamış",
        )


class SystemStatus(BaseModel):
    system: ExternalSystem
    configured: bool


class SyncEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    link_id: str | None
    system: ExternalSystem
    direction: str
    status: str
    message: str
    payload: dict[str, Any]
    created_at: UTCDateTime


@router.get("/status", response_model=list[SystemStatus])
async def status(connectors: ConnectorsDep, _user: AdminUser) -> list[SystemStatus]:
    return [SystemStatus(system=s, configured=s in connectors) for s in ExternalSystem]


@router.post("/{system}/pull")
async def pull(
    system: ExternalSystem, session: SessionDep, connectors: ConnectorsDep, _user: AdminUser
) -> dict[str, Any]:
    _require(connectors, system)
    result = await _engine(session, connectors).pull(system)
    await session.commit()
    return result


class CMDBPullCounts(BaseModel):
    created: int
    updated: int
    unchanged: int
    skipped: int


class CMDBPullError(BaseModel):
    entity_type: str
    external_id: str
    message: str


class CMDBPullResult(BaseModel):
    system: ExternalSystem
    full: bool
    # Artımlı çekimde kullanılan filigran (uzak sys_updated_on, dahil); tam çekimde null
    since: dict[str, str | None]
    cis: CMDBPullCounts
    relationships: CMDBPullCounts
    errors: list[CMDBPullError]


@router.post("/servicenow/cmdb/pull", response_model=CMDBPullResult)
async def pull_servicenow_cmdb(
    session: SessionDep,
    connector: CMDBConnectorDep,
    user: AdminUser,
    full: Annotated[
        bool, Query(description="Filigranı yok say, seçili sınıflardaki tüm CI'ları çek")
    ] = False,
) -> CMDBPullResult:
    """ServiceNow CMDB'sinden (cmdb_ci seçili sınıflar + cmdb_rel_ci) CI ve ilişkileri içe alır."""
    if connector is None:
        raise HTTPException(status_code=400, detail="ServiceNow entegrasyonu yapılandırılmamış")
    try:
        result = await pull_cmdb(session, connector, actor=user.username, full=full)
    except ConnectorError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await session.commit()
    return CMDBPullResult.model_validate(result)


@router.post("/push/{incident_id}", response_model=list[SyncEventOut])
async def push(
    incident_id: str,
    session: SessionDep,
    connectors: ConnectorsDep,
    _user: AdminUser,
    system: ExternalSystem | None = None,
) -> list[SyncEventOut]:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Olay kaydı bulunamadı")
    if system is not None:
        _require(connectors, system)
    elif not connectors:
        raise HTTPException(status_code=400, detail="Hiçbir ITSM entegrasyonu yapılandırılmamış")
    events = await _engine(session, connectors).push(incident, [system] if system else None)
    await session.commit()
    return [SyncEventOut.model_validate(e) for e in events]


@router.get("/conflicts", response_model=list[SyncEventOut])
async def conflicts(
    session: SessionDep, connectors: ConnectorsDep, _user: ApproverUser
) -> list[SyncEventOut]:
    events = await _engine(session, connectors).list_conflicts()
    return [SyncEventOut.model_validate(e) for e in events]


@router.post("/conflicts/{event_id}/resolve", response_model=SyncEventOut)
async def resolve(
    event_id: str,
    session: SessionDep,
    connectors: ConnectorsDep,
    winner: Annotated[Literal["local", "remote"], Query()],
    user: ApproverUser,
) -> SyncEventOut:
    engine = _engine(session, connectors)
    ev = await session.get(SyncEvent, event_id)
    if ev is None or ev.status != "conflict":
        raise HTTPException(status_code=404, detail="Açık çakışma kaydı bulunamadı")
    _require(connectors, ev.system)
    try:
        result = await engine.resolve_conflict(event_id, winner, actor=user.username)
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await session.commit()
    return SyncEventOut.model_validate(result)

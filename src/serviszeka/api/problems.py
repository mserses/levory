"""Sorun yönetimi (ITIL 4 Problem Management) uç noktaları.

Okuma ve yazma: agent / approver / admin (`ProblemUser`); requester (self-servis) ve yapay zekâ
belirteci (`ai_agent`) göremez (403). Kim neyi yapabilir servis katmanında da denetlenir
(`ai:` aktör sorunu çözemez/kapatamaz, kesin RCA yazamaz). Yapay zekâ uçları (`/rca/draft`,
`/fix`, `/trends/propose`) sorun ajanını `ai:problem` aktörüyle Faz 3 araç boru hattından çalıştırır:
politika izin vermezse eylem isteği açılır (onay `/actions`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import llm as llm_module
from serviszeka.agents import problem as problem_agent
from serviszeka.api.cmdb import etag, parse_if_match
from serviszeka.auth import (
    ROLE_AGENT,
    ROLE_APPROVER,
    CurrentUser,
    get_current_user,
    require_roles,
)
from serviszeka.config import Settings, get_settings
from serviszeka.db import get_session
from serviszeka.domain.enums import ProblemState
from serviszeka.models import Problem
from serviszeka.services import problems as svc
from serviszeka.services.problem_schemas import (
    AgentResultRead,
    AgentResultsRead,
    ChangeLinkRequest,
    CILinkRequest,
    IncidentLinkRequest,
    ProblemCreate,
    ProblemRead,
    ProblemTransitionRequest,
    ProblemUpdate,
    ProposeFixRequest,
    RCARecordRequest,
    TrendClusterRead,
    TrendsRead,
    TrendsRequest,
)
from serviszeka.tools import ToolError

router = APIRouter(prefix="/problems", tags=["problems"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
# Sorun kayıtları: agent / approver / admin; requester ve ai_agent belirteci 403
ProblemUser = Annotated[CurrentUser, Depends(require_roles(ROLE_AGENT, ROLE_APPROVER))]


def get_problem_settings() -> Settings:
    """Ayar bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_problem_settings)]


def get_problem_llm() -> llm_module.LLMClient:
    """LLM bağımlılığı (testlerde dependency_overrides ile değiştirilir)."""
    try:
        return llm_module.get_llm()
    except llm_module.LLMError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


LLMDep = Annotated[llm_module.LLMClient, Depends(get_problem_llm)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ToolError):
        return HTTPException(exc.status_code, exc.message)
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, svc.ProblemVersionConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, problem_agent.RCADraftError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


_ERRORS = (svc.ProblemError, ToolError, problem_agent.RCADraftError)


async def _read(session: AsyncSession, problem: Problem, response: Response | None = None) -> ProblemRead:
    await session.refresh(problem, attribute_names=["incidents", "affected_cis", "changes"])
    if response is not None:
        response.headers["ETag"] = etag(problem.version)
    return ProblemRead.model_validate(problem)


async def _get_or_404(session: AsyncSession, problem_id: str) -> Problem:
    problem = await svc.get_problem(session, problem_id)
    if problem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sorun bulunamadı")
    return problem


def _agent_result(result) -> AgentResultRead:
    return AgentResultRead.model_validate(
        {**result.to_dict(), "decision_id": result.decision_id,
         "action_request_id": result.action_request_id}
    )  # fmt: skip


# --- liste / eğilim (/{problem_id}'den önce) ---------------------------------------------------


@router.get("", response_model=list[ProblemRead])
async def list_problems(
    session: SessionDep,
    _user: ProblemUser,
    state: ProblemState | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    incident_id: str | None = None,
    ci_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProblemRead]:
    rows = await svc.list_problems(
        session, state=state, q=q, incident_id=incident_id, ci_id=ci_id, limit=limit, offset=offset
    )
    return [ProblemRead.model_validate(r) for r in rows]


@router.post("", response_model=ProblemRead, status_code=status.HTTP_201_CREATED)
async def create_problem(
    body: ProblemCreate, session: SessionDep, response: Response, user: ProblemUser
) -> ProblemRead:
    try:
        problem = await svc.create_problem(session, body, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.post("/trends", response_model=TrendsRead)
async def detect_trends(body: TrendsRequest, session: SessionDep, _user: ProblemUser) -> TrendsRead:
    """Olay eğilimleri (aynı CI + kategori, benzer başlık); salt okuma, hiçbir şey yazmaz."""
    clusters = await problem_agent.detect_trends(session, body.window_days)
    return TrendsRead(
        window_days=body.window_days,
        clusters=[TrendClusterRead.model_validate(c.to_dict()) for c in clusters],
    )


@router.post("/trends/propose", response_model=AgentResultsRead)
async def propose_from_trends(
    body: TrendsRequest, session: SessionDep, settings: SettingsDep, _user: ProblemUser
) -> AgentResultsRead:
    """Eşiği aşan her küme için sorun önerisi (`problem.create` R1, aktör `ai:problem`)."""
    try:
        results = await problem_agent.propose_problems(
            session, window_days=body.window_days, settings=settings
        )
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return AgentResultsRead(results=[_agent_result(r) for r in results])


# --- tek kayıt ---------------------------------------------------------------------------------


@router.get("/{problem_id}", response_model=ProblemRead)
async def get_problem(
    problem_id: str, session: SessionDep, response: Response, _user: ProblemUser
) -> ProblemRead:
    return await _read(session, await _get_or_404(session, problem_id), response)


@router.patch("/{problem_id}", response_model=ProblemRead)
async def update_problem(
    problem_id: str,
    body: ProblemUpdate,
    session: SessionDep,
    response: Response,
    user: ProblemUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ProblemRead:
    expected = parse_if_match(if_match)
    try:
        problem = await svc.update_problem(
            session, problem_id, body, user, expected_version=expected
        )
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.post("/{problem_id}/transition", response_model=ProblemRead)
async def transition(
    problem_id: str,
    body: ProblemTransitionRequest,
    session: SessionDep,
    response: Response,
    user: ProblemUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ProblemRead:
    expected = parse_if_match(if_match)
    if expected is None:
        expected = body.expected_version
    try:
        problem = await svc.transition(
            session, problem_id, body.to_state, user, note=body.note, expected_version=expected
        )
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.post("/{problem_id}/incidents", response_model=ProblemRead)
async def link_incidents(
    problem_id: str,
    body: IncidentLinkRequest,
    session: SessionDep,
    response: Response,
    user: ProblemUser,
) -> ProblemRead:
    try:
        problem = await svc.link_incidents(session, problem_id, body.incident_ids, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.delete("/{problem_id}/incidents/{incident_id}", response_model=ProblemRead)
async def unlink_incident(
    problem_id: str, incident_id: str, session: SessionDep, response: Response, user: ProblemUser
) -> ProblemRead:
    try:
        problem = await svc.unlink_incident(session, problem_id, incident_id, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.post("/{problem_id}/cis", response_model=ProblemRead)
async def link_cis(
    problem_id: str, body: CILinkRequest, session: SessionDep, response: Response, user: ProblemUser
) -> ProblemRead:
    try:
        problem = await svc.link_cis(session, problem_id, body.ci_ids, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.post("/{problem_id}/changes", response_model=ProblemRead)
async def link_changes(
    problem_id: str,
    body: ChangeLinkRequest,
    session: SessionDep,
    response: Response,
    user: ProblemUser,
) -> ProblemRead:
    """Kalıcı çözüm değişikliklerini (RFC) bağlar."""
    try:
        problem = await svc.link_changes(session, problem_id, body.change_ids, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.put("/{problem_id}/rca", response_model=ProblemRead)
async def record_rca(
    problem_id: str,
    body: RCARecordRequest,
    session: SessionDep,
    response: Response,
    user: ProblemUser,
    if_match: Annotated[str | None, Header()] = None,
) -> ProblemRead:
    """Kesin RCA ve kök neden (insan)."""
    expected = parse_if_match(if_match)
    if expected is None:
        expected = body.expected_version
    try:
        problem = await svc.record_rca(session, problem_id, body.rca, user, expected_version=expected)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.post("/{problem_id}/rca/accept", response_model=ProblemRead)
async def accept_rca_draft(
    problem_id: str, session: SessionDep, response: Response, user: ProblemUser
) -> ProblemRead:
    """RCA taslağını kesin RCA olarak kabul eder (insan)."""
    try:
        problem = await svc.accept_rca_draft(session, problem_id, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)


@router.post("/{problem_id}/rca/draft", response_model=AgentResultRead)
async def draft_rca(
    problem_id: str, session: SessionDep, settings: SettingsDep, llm: LLMDep, _user: ProblemUser
) -> AgentResultRead:
    """Yapay zekâ 5 Neden taslağı (`problem.record_rca` R1; yalnızca `rca_draft`)."""
    await _get_or_404(session, problem_id)
    try:
        result = await problem_agent.draft_rca(session, problem_id, llm, settings=settings)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return _agent_result(result)


@router.post("/{problem_id}/fix", response_model=AgentResultRead)
async def propose_fix(
    problem_id: str,
    body: ProposeFixRequest,
    session: SessionDep,
    settings: SettingsDep,
    _user: ProblemUser,
) -> AgentResultRead:
    """Kalıcı çözüm için yapay zekâ taslak RFC önerisi (`problem.propose_fix` R1)."""
    await _get_or_404(session, problem_id)
    try:
        result = await problem_agent.propose_fix(
            session, problem_id, **body.model_dump(), settings=settings
        )
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return _agent_result(result)


@router.post("/{problem_id}/known-error/publish", response_model=ProblemRead)
async def publish_known_error(
    problem_id: str, session: SessionDep, response: Response, user: ProblemUser
) -> ProblemRead:
    """Geçici çözümü KEDB makalesi olarak yayımlar (insan; yapay zekâ için R2 araç)."""
    try:
        problem = await svc.publish_known_error(session, problem_id, user)
    except _ERRORS as exc:
        raise _http(exc) from exc
    await session.commit()
    return await _read(session, problem, response)

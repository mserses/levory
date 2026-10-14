"""Sorun yönetimi (ITIL 4 Problem Management) servisi.

Durum makinesi::

    new ─► investigating ─► known_error ─► resolved ─► closed
     │           │               │            │
     │           └──► resolved ◄─┘            └─► investigating (yeniden açma)
     └──────── closed (her açık durumdan; çözülmemişse not zorunlu, ör. yinelenen kayıt)

- `known_error` için kök neden ya da geçici çözüm, `resolved` için kök neden zorunludur.
- Yapay zekâ aktörü (`ai:` önekli) sorunu çözemez/kapatamaz, kesin RCA yazamaz/kabul edemez,
  bilinen hatayı yayımlayamaz (`ProblemForbiddenError`). Yapay zekâ yalnızca `rca_draft` yazar.
- Her yazma sürümü artırır (iyimser kilit: `expected_version` -> `ProblemVersionConflictError`)
  ve `audit.record` ile denetime yazılır. Fonksiyonlar flush eder, commit etmez.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, knowledge
from serviszeka.auth import CurrentUser
from serviszeka.domain.enums import ProblemState
from serviszeka.models import (
    ChangeRequest,
    ConfigurationItem,
    Incident,
    Problem,
    problem_cis,
    problem_incidents,
)
from serviszeka.services.problem_schemas import RCA, ProblemCreate, ProblemUpdate

PS = ProblemState
_AI_PREFIX = "ai:"
NUMBER_PREFIX = "PRB"
_NUMBER_RE = re.compile(r"^PRB(\d+)$")

OPEN_STATES: frozenset[ProblemState] = frozenset(
    {PS.NEW, PS.INVESTIGATING, PS.KNOWN_ERROR, PS.RESOLVED}
)
# RCA taslağı yalnızca analiz sürerken yazılır
RCA_DRAFT_STATES: frozenset[ProblemState] = frozenset({PS.NEW, PS.INVESTIGATING, PS.KNOWN_ERROR})

ALLOWED_TRANSITIONS: dict[ProblemState, frozenset[ProblemState]] = {
    PS.NEW: frozenset({PS.INVESTIGATING, PS.CLOSED}),
    PS.INVESTIGATING: frozenset({PS.KNOWN_ERROR, PS.RESOLVED, PS.CLOSED}),
    PS.KNOWN_ERROR: frozenset({PS.INVESTIGATING, PS.RESOLVED, PS.CLOSED}),
    PS.RESOLVED: frozenset({PS.INVESTIGATING, PS.CLOSED}),
    PS.CLOSED: frozenset(),
}
# Yapay zekâ aktörünün asla yapamadığı geçişler
HUMAN_ONLY_STATES: frozenset[ProblemState] = frozenset({PS.RESOLVED, PS.CLOSED})


class ProblemError(Exception):
    pass


class ProblemNotFoundError(ProblemError, LookupError):
    pass


class ProblemValidationError(ProblemError, ValueError):
    pass


class InvalidProblemTransitionError(ProblemError, ValueError):
    pass


class ProblemForbiddenError(ProblemError, PermissionError):
    pass


class ProblemVersionConflictError(ProblemError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Sorun kaydı başka biri tarafından güncellendi (beklenen sürüm {expected}, "
            f"güncel {actual}); yeniden yükleyin."
        )
        self.expected = expected
        self.actual = actual


def _now() -> datetime:
    return datetime.now(UTC)


def is_ai_actor(user: CurrentUser | str) -> bool:
    name = user if isinstance(user, str) else user.username
    return name.startswith(_AI_PREFIX) or (
        not isinstance(user, str) and "ai_agent" in user.roles
    )


def _human_only(user: CurrentUser, what: str) -> None:
    if is_ai_actor(user):
        raise ProblemForbiddenError(f"Yapay zekâ aktörü {what}; insan kararı gerekir.")


async def next_number(session: AsyncSession) -> str:
    numbers = (
        await session.scalars(select(Problem.number).where(Problem.number.like(f"{NUMBER_PREFIX}%")))
    ).all()
    highest = max((int(m.group(1)) for n in numbers if (m := _NUMBER_RE.match(n or ""))), default=0)
    return f"{NUMBER_PREFIX}{highest + 1:07d}"


# --- okuma ---------------------------------------------------------------------------------------


async def get_problem(session: AsyncSession, problem_id: str) -> Problem | None:
    return await session.get(Problem, problem_id)


async def _require(
    session: AsyncSession, problem_id: str, expected_version: int | None = None
) -> Problem:
    problem = await session.get(Problem, problem_id)
    if problem is None:
        raise ProblemNotFoundError(f"Sorun bulunamadı: {problem_id}")
    db_version = await session.scalar(
        select(Problem.version).where(Problem.id == problem_id).with_for_update()
    )
    if db_version != problem.version:
        await session.refresh(problem)
    if expected_version is not None and expected_version != problem.version:
        raise ProblemVersionConflictError(expected_version, problem.version)
    return problem


async def list_problems(
    session: AsyncSession,
    *,
    state: ProblemState | str | None = None,
    q: str | None = None,
    incident_id: str | None = None,
    ci_id: str | None = None,
    open_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Problem]:
    stmt = select(Problem)
    if state is not None:
        stmt = stmt.where(Problem.state == ProblemState(state))
    if open_only:
        stmt = stmt.where(Problem.state.in_(list(OPEN_STATES)))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Problem.number).like(like), func.lower(Problem.title).like(like)))
    if incident_id:
        stmt = stmt.where(
            Problem.id.in_(
                select(problem_incidents.c.problem_id).where(
                    problem_incidents.c.incident_id == incident_id
                )
            )
        )
    if ci_id:
        stmt = stmt.where(
            Problem.id.in_(select(problem_cis.c.problem_id).where(problem_cis.c.ci_id == ci_id))
        )
    stmt = stmt.order_by(Problem.created_at.desc(), Problem.number.desc())
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


async def open_problem_incident_ids(session: AsyncSession) -> set[str]:
    """Kapanmamış bir soruna bağlı olay kimlikleri (eğilim ajanı bunları yeniden önermez)."""
    rows = await session.scalars(
        select(problem_incidents.c.incident_id)
        .join(Problem, Problem.id == problem_incidents.c.problem_id)
        .where(Problem.state != PS.CLOSED)
    )
    return set(rows.all())


# --- bağ çözümleme -------------------------------------------------------------------------------


def _unique(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ids))


async def _incidents(session: AsyncSession, ids: Iterable[str]) -> list[Incident]:
    wanted = _unique(ids)
    if not wanted:
        return []
    rows = {i.id: i for i in (await session.scalars(select(Incident).where(Incident.id.in_(wanted)))).all()}
    missing = [i for i in wanted if i not in rows]
    if missing:
        raise ProblemValidationError(f"Olay bulunamadı: {', '.join(missing)}")
    return [rows[i] for i in wanted]


async def _cis(session: AsyncSession, ids: Iterable[str]) -> list[ConfigurationItem]:
    wanted = _unique(ids)
    if not wanted:
        return []
    rows = {
        c.id: c
        for c in (
            await session.scalars(select(ConfigurationItem).where(ConfigurationItem.id.in_(wanted)))
        ).all()
    }
    missing = [i for i in wanted if i not in rows]
    if missing:
        raise ProblemValidationError(f"CI bulunamadı: {', '.join(missing)}")
    return [rows[i] for i in wanted]


async def _changes(session: AsyncSession, ids: Iterable[str]) -> list[ChangeRequest]:
    wanted = _unique(ids)
    if not wanted:
        return []
    rows = {
        c.id: c
        for c in (
            await session.scalars(select(ChangeRequest).where(ChangeRequest.id.in_(wanted)))
        ).all()
    }
    missing = [i for i in wanted if i not in rows]
    if missing:
        raise ProblemValidationError(f"Değişiklik bulunamadı: {', '.join(missing)}")
    return [rows[i] for i in wanted]


def _check_open(problem: Problem) -> None:
    if ProblemState(problem.state) == PS.CLOSED:
        raise InvalidProblemTransitionError(f"{problem.number} kapalı; değiştirilemez.")


async def _touch(
    session: AsyncSession, problem: Problem, user: CurrentUser, action: str, details: dict[str, Any]
) -> Problem:
    problem.version = (problem.version or 0) + 1
    problem.updated_at = _now()
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action=action,
        entity_type="problem",
        entity_id=problem.id,
        details={"number": problem.number, "version": problem.version, **details},
    )
    return problem


# --- yazma ---------------------------------------------------------------------------------------


async def create_problem(
    session: AsyncSession,
    data: ProblemCreate,
    user: CurrentUser,
    *,
    detected_by: str | None = None,
) -> Problem:
    incidents = await _incidents(session, data.incident_ids)
    cis = await _cis(session, data.ci_ids)
    now = _now()
    problem = Problem(
        number=await next_number(session),
        title=data.title,
        description=data.description,
        state=PS.NEW,
        priority=data.priority,
        category=data.category,
        assignment_group=data.assignment_group,
        workaround=data.workaround,
        detected_by=detected_by or user.username,
        created_by=user.username,
        incidents=incidents,
        affected_cis=sorted(cis, key=lambda c: (c.name, c.id)),
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(problem)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="problem.create",
        entity_type="problem",
        entity_id=problem.id,
        details={
            "number": problem.number,
            "detected_by": problem.detected_by,
            "priority": problem.priority.value,
            "incident_ids": [i.id for i in incidents],
            "ci_ids": [c.id for c in cis],
        },
    )
    return problem


async def update_problem(
    session: AsyncSession,
    problem_id: str,
    data: ProblemUpdate,
    user: CurrentUser,
    *,
    expected_version: int | None = None,
) -> Problem:
    values = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    if expected_version is None:
        expected_version = data.expected_version
    problem = await _require(session, problem_id, expected_version)
    _check_open(problem)
    changed: dict[str, Any] = {}
    for key, value in values.items():
        if key == "title" and value is None:
            continue
        if key == "description" and value is None:
            value = ""
        if getattr(problem, key) != value:
            setattr(problem, key, value)
            changed[key] = value
    if not changed:
        return problem
    return await _touch(session, problem, user, "problem.update", {"fields": sorted(changed)})


async def link_incidents(
    session: AsyncSession,
    problem_id: str,
    incident_ids: list[str],
    user: CurrentUser,
    *,
    expected_version: int | None = None,
) -> Problem:
    problem = await _require(session, problem_id, expected_version)
    _check_open(problem)
    current = set(problem.incident_ids)
    added = [i for i in await _incidents(session, incident_ids) if i.id not in current]
    if not added:
        return problem
    problem.incidents = sorted([*problem.incidents, *added], key=lambda i: i.number)
    return await _touch(
        session, problem, user, "problem.link_incidents", {"added": [i.id for i in added]}
    )


async def unlink_incident(
    session: AsyncSession, problem_id: str, incident_id: str, user: CurrentUser
) -> Problem:
    problem = await _require(session, problem_id)
    _check_open(problem)
    if incident_id not in problem.incident_ids:
        raise ProblemNotFoundError(f"Olay bu soruna bağlı değil: {incident_id}")
    problem.incidents = [i for i in problem.incidents if i.id != incident_id]
    return await _touch(session, problem, user, "problem.unlink_incident", {"removed": incident_id})


async def link_cis(
    session: AsyncSession, problem_id: str, ci_ids: list[str], user: CurrentUser
) -> Problem:
    problem = await _require(session, problem_id)
    _check_open(problem)
    current = set(problem.affected_ci_ids)
    added = [c for c in await _cis(session, ci_ids) if c.id not in current]
    if not added:
        return problem
    problem.affected_cis = sorted([*problem.affected_cis, *added], key=lambda c: (c.name, c.id))
    return await _touch(session, problem, user, "problem.link_cis", {"added": [c.id for c in added]})


async def link_changes(
    session: AsyncSession, problem_id: str, change_ids: list[str], user: CurrentUser
) -> Problem:
    """Kalıcı çözüm değişikliklerini (RFC) bağlar."""
    problem = await _require(session, problem_id)
    _check_open(problem)
    current = set(problem.change_ids)
    added = [c for c in await _changes(session, change_ids) if c.id not in current]
    if not added:
        return problem
    problem.changes = sorted([*problem.changes, *added], key=lambda c: c.number)
    return await _touch(
        session, problem, user, "problem.link_changes", {"added": [c.id for c in added]}
    )


async def set_rca_draft(
    session: AsyncSession,
    problem_id: str,
    rca: RCA,
    user: CurrentUser,
    *,
    source: str = "manual",
) -> Problem:
    """RCA taslağını yazar (yapay zekâ dahil); kesin RCA ve durum değişmez."""
    problem = await _require(session, problem_id)
    if ProblemState(problem.state) not in RCA_DRAFT_STATES:
        raise InvalidProblemTransitionError(
            f"{ProblemState(problem.state).value} durumundaki sorunda RCA taslağı yazılamaz."
        )
    problem.rca_draft = rca.model_dump(mode="json", exclude_none=True)
    return await _touch(
        session,
        problem,
        user,
        "problem.rca_draft",
        {"method": rca.method.value, "steps": len(rca.steps), "source": source},
    )


def _apply_rca(problem: Problem, rca: dict[str, Any]) -> None:
    root = (rca.get("root_cause") or "").strip()
    if not root:
        raise ProblemValidationError("Kesin RCA kök neden (root_cause) içermelidir.")
    problem.rca = rca
    problem.root_cause = root


async def record_rca(
    session: AsyncSession,
    problem_id: str,
    rca: RCA,
    user: CurrentUser,
    *,
    expected_version: int | None = None,
) -> Problem:
    """Kesin RCA'yı (ve kök nedeni) kaydeder; yalnızca insan."""
    _human_only(user, "kesin kök neden analizi kaydedemez")
    problem = await _require(session, problem_id, expected_version)
    _check_open(problem)
    _apply_rca(problem, rca.model_dump(mode="json", exclude_none=True))
    return await _touch(session, problem, user, "problem.rca", {"method": rca.method.value})


async def accept_rca_draft(
    session: AsyncSession, problem_id: str, user: CurrentUser
) -> Problem:
    """RCA taslağını kesin RCA olarak kabul eder; yalnızca insan."""
    _human_only(user, "RCA taslağını kabul edemez")
    problem = await _require(session, problem_id)
    _check_open(problem)
    if not problem.rca_draft:
        raise ProblemValidationError("Kabul edilecek RCA taslağı yok.")
    _apply_rca(problem, dict(problem.rca_draft))
    problem.rca_draft = None
    return await _touch(session, problem, user, "problem.rca_accept", {})


async def transition(
    session: AsyncSession,
    problem_id: str,
    to_state: ProblemState | str,
    user: CurrentUser,
    *,
    note: str | None = None,
    expected_version: int | None = None,
) -> Problem:
    target = ProblemState(to_state)
    if target in HUMAN_ONLY_STATES:
        _human_only(user, f"sorunu '{target.value}' durumuna alamaz")
    problem = await _require(session, problem_id, expected_version)
    current = ProblemState(problem.state)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidProblemTransitionError(f"{current.value} -> {target.value} geçişine izin verilmez.")
    note = (note or "").strip() or None
    if target == PS.KNOWN_ERROR and not (problem.root_cause or problem.workaround):
        raise InvalidProblemTransitionError(
            "Bilinen hata için kök neden ya da geçici çözüm belgelenmelidir."
        )
    if target == PS.RESOLVED and not problem.root_cause:
        raise InvalidProblemTransitionError("Çözüm için kesin kök neden (RCA) kaydedilmelidir.")
    if target == PS.CLOSED and current != PS.RESOLVED and not note:
        raise InvalidProblemTransitionError(
            "Çözülmemiş sorun yalnızca kapanış notuyla (ör. yinelenen kayıt) kapatılabilir."
        )
    now = _now()
    problem.state = target
    if target == PS.RESOLVED:
        problem.resolved_at = now
        if note:
            problem.resolution = note
    elif target == PS.CLOSED:
        problem.closed_at = now
        if note:
            problem.resolution = note
    elif target == PS.INVESTIGATING and current == PS.RESOLVED:
        problem.resolved_at = None  # yeniden açma
    return await _touch(
        session,
        problem,
        user,
        "problem.transition",
        {"from": current.value, "to": target.value, "note": note},
    )


async def publish_known_error(
    session: AsyncSession,
    problem_id: str,
    user: CurrentUser,
    *,
    title: str | None = None,
) -> Problem:
    """Geçici çözümü KEDB makalesi olarak yayımlar (yoksa açar, varsa günceller); yalnızca insan.

    Sorun yeni/incelemede ise `known_error` durumuna geçer.
    """
    _human_only(user, "bilinen hatayı yayımlayamaz")
    problem = await _require(session, problem_id)
    _check_open(problem)
    workaround = (problem.workaround or "").strip()
    if not workaround:
        raise ProblemValidationError("Yayımlanacak geçici çözüm (workaround) yok.")
    body_parts = [problem.description or problem.title]
    if problem.root_cause:
        body_parts.append(f"Kök neden: {problem.root_cause}")
    body_parts.append(f"Sorun kaydı: {problem.number}")
    fields = {
        "title": title or f"Bilinen hata: {problem.title}"[:300],
        "body": "\n\n".join(body_parts),
        "workaround": workaround,
        "category": problem.category,
        "source_incident_ids": problem.incident_ids,
    }
    article = None
    if problem.known_error_article_id:
        article = await knowledge.get_article(session, problem.known_error_article_id)
    if article is None:
        article = await knowledge.create_article(
            session,
            **fields,
            is_known_error=True,
            state=knowledge.STATE_DRAFT,
            actor=user.username,
        )
    else:
        await knowledge.update_article(session, article.id, fields, actor=user.username)
    await knowledge.publish_article(session, article.id, actor=user.username)
    previous = ProblemState(problem.state)
    problem.known_error_article_id = article.id
    if previous in (PS.NEW, PS.INVESTIGATING):
        problem.state = PS.KNOWN_ERROR
    return await _touch(
        session,
        problem,
        user,
        "problem.known_error.publish",
        {"article_id": article.id, "article_number": article.number, "from": previous.value},
    )

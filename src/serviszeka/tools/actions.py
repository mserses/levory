"""Eylem istekleri (ActionRequest): insan onayı, red, süre aşımı ve onay anında yürütme.

Kurallar (Faz 3 kapısı — R2/R3 hiçbir eylem insan onayı olmadan çalışmaz):
- Yapay zekâ aktörü (``ai:`` önekli ya da ``ai_agent`` rollü) hiçbir isteği onaylayamaz/reddedemez.
- Talep eden kendi isteğine karar veremez; kimlikler ``changes.same_identity`` ile (büyük/küçük
  harf ve kenar boşluğu duyarsız) karşılaştırılır. Aynı kişi iki kez onay veremez/sayılmaz.
- Karar verebilen: ``approver`` / ``admin``; R3 istek bir değişikliğe bağlıysa ``change_manager``.
- R1/R2: tek onay. R3: iki farklı onaylayan ya da bağlı değişiklik CAB onaylı (normal/acil,
  onaylar yeterli, durum scheduled/implement) iken tek onay. Tek red isteği reddeder.
- Süresi (``SZ_ACTION_REQUEST_TTL_HOURS``, varsayılan 24 saat) dolan istek ``expired`` olur ve
  artık onaylanamaz / yürütülemez.
- Hizmet talebine bağlı istek (``request_id``) sonuçlanınca (yürütüldü / reddedildi / başarısız /
  süresi doldu) ``services.requests.on_action_resolved`` karşılamayı sürdürür ya da kapatır;
  talep iptal edilirse bekleyen istekleri ``cancel_for_request`` reddeder.
- Son onayda istek, onaylayan insan aktörüyle yürütülür. Yürütme anında argümanlar yeniden
  doğrulanır, risk yeniden çözülür (``max(saklı, güncel)``): risk arttıysa (ör. R2 -> R3) yeni
  sınıfın onay kuralı aranır, yetmiyorsa istek beklemede kalır. İşleyici SAVEPOINT içinde
  çalışır; hata -> ``failed`` (kısmi yazma geri alınır).

Fonksiyonlar flush eder, commit etmez.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.auth import ROLE_APPROVER, ROLE_CHANGE_MANAGER, CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import (
    ActionRequestState,
    ApprovalDecision,
    ChangeState,
    ChangeType,
    RiskClass,
)
from serviszeka.models import ActionApproval, ActionRequest, AIDecision
from serviszeka.services import changes
from serviszeka.services.changes import same_identity
from serviszeka.services.sla import ensure_utc
from serviszeka.tools import executor
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolError, risk_rank

AS = ActionRequestState
# Bağlı değişiklik bu durumlardayken (ve CAB/ECAB onayları yeterliyse) "CAB onaylı" sayılır
CAB_APPROVED_STATES: frozenset[ChangeState] = frozenset(
    {ChangeState.SCHEDULED, ChangeState.IMPLEMENT}
)


class ActionError(Exception):
    status_code = 400


class ActionNotFoundError(ActionError, LookupError):
    status_code = 404


class ActionForbiddenError(ActionError, PermissionError):
    status_code = 403


class ActionStateError(ActionError):
    """İstek artık karar beklemiyor (reddedildi, yürütüldü, süresi doldu...)."""

    status_code = 409


class ActionExpiredError(ActionStateError):
    def __init__(self, number: str) -> None:
        super().__init__(f"{number} isteğinin süresi doldu; onaylanamaz ve yürütülemez.")


class DuplicateActionApprovalError(ActionError):
    status_code = 409

    def __init__(self) -> None:
        super().__init__("Bu istek için zaten karar verdiniz; aynı kişi iki kez sayılmaz.")


def _now() -> datetime:
    return datetime.now(UTC)


def _key(name: str) -> str:
    return name.strip().casefold()


# --- okuma ----------------------------------------------------------------------------------------


async def get_action(session: AsyncSession, action_id: str) -> ActionRequest | None:
    return await session.get(ActionRequest, action_id)


async def list_actions(
    session: AsyncSession,
    *,
    state: ActionRequestState | str | None = None,
    tool: str | None = None,
    requested_by: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ActionRequest]:
    stmt = select(ActionRequest)
    if state is not None:
        stmt = stmt.where(ActionRequest.state == ActionRequestState(state))
    if tool is not None:
        stmt = stmt.where(ActionRequest.tool == tool)
    if requested_by is not None:
        stmt = stmt.where(ActionRequest.requested_by == requested_by)
    stmt = stmt.order_by(ActionRequest.created_at.desc(), ActionRequest.number.desc())
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


async def list_approvals(session: AsyncSession, action_id: str) -> list[ActionApproval]:
    stmt = (
        select(ActionApproval)
        .where(ActionApproval.action_request_id == action_id)
        .order_by(ActionApproval.created_at, ActionApproval.id)
    )
    return list((await session.scalars(stmt)).all())


# --- yardımcılar -------------------------------------------------------------------------------------


async def _lock(session: AsyncSession, action_id: str) -> ActionRequest:
    ar = await get_action(session, action_id)
    if ar is None:
        raise ActionNotFoundError(f"Eylem isteği bulunamadı: {action_id}")
    db_version = await session.scalar(
        select(ActionRequest.version).where(ActionRequest.id == action_id).with_for_update()
    )
    if db_version != ar.version:
        await session.refresh(ar)
    return ar


def _check_decider(ar: ActionRequest, user: CurrentUser) -> None:
    if user.is_ai:
        raise ActionForbiddenError("Yapay zekâ eylem isteklerini onaylayamaz ya da reddedemez.")
    if same_identity(user.username, ar.requested_by):
        raise ActionForbiddenError("Talep eden kendi eylem isteği hakkında karar veremez.")
    if user.has_any(ROLE_APPROVER):
        return
    if (
        RiskClass(ar.risk_class) == RiskClass.R3
        and ar.change_id
        and user.has_any(ROLE_CHANGE_MANAGER)
    ):
        return
    raise ActionForbiddenError("Bu eylem isteği için onay yetkiniz yok (approver rolü gerekir).")


async def _check_parties(session: AsyncSession, ar: ActionRequest, user: CurrentUser) -> None:
    """Hizmet talebine bağlı istekte talep eden, talebi açan ve eylemin hedef kullanıcısı karar
    veremez (ör. kendini yetkili gruba ekleyen adımı onaylamak). Kimlikler büyük/küçük harf
    duyarsız ve — kimlik bağlayıcısı varsa — dizinde aynı nesne olarak karşılaştırılır."""
    from serviszeka.connectors.base import IDENTITY
    from serviszeka.models import ServiceRequest
    from serviszeka.services.requests import same_person
    from serviszeka.tools.connectors import connectors

    parties: list[str] = []
    target = (ar.args or {}).get("user")
    if isinstance(target, str):
        parties.append(target)
    if ar.request_id:
        req = await session.get(ServiceRequest, ar.request_id)
        if req is not None:
            parties.extend([req.requester, req.opened_by])
    if not parties:
        return
    provider = connectors.get(IDENTITY)
    for party in parties:
        if party.startswith("ai:"):
            continue
        if await same_person(provider, user.username, party):
            raise ActionForbiddenError(
                "Talep eden, talebi açan ya da eylemin hedefi olan kişi bu isteğe karar veremez."
            )


async def _touch(session: AsyncSession, ar: ActionRequest, now: datetime) -> None:
    ar.version += 1
    ar.updated_at = now
    await session.flush()


async def _notify_request(
    session: AsyncSession,
    ar: ActionRequest,
    *,
    now: datetime,
    settings: Settings | None = None,
    llm: Any = None,
) -> None:
    """Bağlı hizmet talebine sonucu bildirir (karşılama devam eder ya da talep kapanır)."""
    if not ar.request_id or AS(ar.state) in (AS.PENDING, AS.APPROVED):
        return
    from serviszeka.services import requests as request_service

    await request_service.on_action_resolved(session, ar, now=now, settings=settings, llm=llm)


async def _mark_expired(
    session: AsyncSession, ar: ActionRequest, now: datetime, actor: str
) -> None:
    ar.state = AS.EXPIRED
    await _touch(session, ar, now)
    await audit.record(
        session,
        actor=actor,
        action="action_request.expire",
        entity_type="action_request",
        entity_id=ar.id,
        details={"number": ar.number, "tool": ar.tool, "expires_at": ar.expires_at.isoformat()},
    )
    await _notify_request(session, ar, now=now)


async def cancel_for_request(
    session: AsyncSession,
    request_id: str,
    user: CurrentUser,
    reason: str,
    *,
    now: datetime | None = None,
) -> int:
    """Hizmet talebi iptal edilince bağlı bekleyen istekleri reddeder (sonradan yürütülemesinler).

    Bu bir onay kararı değildir (`ActionApproval` yazılmaz); denetime `action_request.cancel`
    düşer. Döndürülen: reddedilen istek sayısı.
    """
    now = ensure_utc(now) if now else _now()
    rows = list(
        (
            await session.scalars(
                select(ActionRequest).where(
                    ActionRequest.request_id == request_id, ActionRequest.state == AS.PENDING
                )
            )
        ).all()
    )
    for ar in rows:
        ar.state = AS.REJECTED
        ar.error = reason[:2000]
        await _touch(session, ar, now)
        await audit.record(
            session,
            actor=user.username,
            action="action_request.cancel",
            entity_type="action_request",
            entity_id=ar.id,
            details={"number": ar.number, "tool": ar.tool, "request_id": request_id,
                     "reason": reason},
        )
        await _close_decision(session, ar, user, applied=False, now=now)
    return len(rows)


async def _require_open(
    session: AsyncSession, ar: ActionRequest, now: datetime, actor: str
) -> None:
    if ar.state == AS.PENDING and ensure_utc(ar.expires_at) <= now:
        await _mark_expired(session, ar, now, actor)
        raise ActionExpiredError(ar.number)
    if ar.state != AS.PENDING:
        raise ActionStateError(
            f"{ar.number} artık karar beklemiyor (durum: {ActionRequestState(ar.state).value})."
        )


async def expire_due(session: AsyncSession, now: datetime | None = None) -> int:
    """Süresi dolmuş bekleyen istekleri `expired` yapar; sayısını döndürür."""
    now = now or _now()
    stmt = select(ActionRequest).where(
        ActionRequest.state == AS.PENDING, ActionRequest.expires_at <= now
    )
    rows = list((await session.scalars(stmt)).all())
    for ar in rows:
        await _mark_expired(session, ar, now, "system:actions")
    return len(rows)


async def _cab_approved(session: AsyncSession, change_id: str, settings: Settings) -> bool:
    change = await changes.get_change(session, change_id)
    if change is None or ChangeType(change.change_type) == ChangeType.STANDARD:
        return False
    if ChangeState(change.state) not in CAB_APPROVED_STATES:
        return False
    status = await changes.approval_status(session, change_id, settings=settings)
    return status.satisfied


def _valid_approvers(ar: ActionRequest, approvals: list[ActionApproval]) -> set[str]:
    return {
        a.approver_key
        for a in approvals
        if a.decision == ApprovalDecision.APPROVE
        and not same_identity(a.approver, ar.requested_by)
        and not a.approver.startswith("ai:")
    }


async def is_satisfied(
    session: AsyncSession, ar: ActionRequest, settings: Settings | None = None
) -> bool:
    settings = settings or get_settings()
    count = len(_valid_approvers(ar, await list_approvals(session, ar.id)))
    if RiskClass(ar.risk_class) != RiskClass.R3:
        return count >= 1
    if count >= 2:
        return True
    return (
        count >= 1 and bool(ar.change_id) and await _cab_approved(session, ar.change_id, settings)
    )


async def _record(
    session: AsyncSession,
    ar: ActionRequest,
    user: CurrentUser,
    decision: ApprovalDecision,
    comment: str,
    now: datetime,
) -> ActionApproval:
    existing = await list_approvals(session, ar.id)
    if any(same_identity(a.approver, user.username) for a in existing):
        raise DuplicateActionApprovalError()
    approval = ActionApproval(
        action_request_id=ar.id,
        approver=user.username,
        approver_key=_key(user.username),
        approver_roles=sorted(user.roles),
        decision=decision,
        comment=comment,
        created_at=now,
    )
    # Eşzamanlı aynı kişi: tekil (istek, approver_key) kısıtı ikinciyi durdurur
    try:
        async with session.begin_nested():
            session.add(approval)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateActionApprovalError() from exc
    await audit.record(
        session,
        actor=user.username,
        action=f"action_request.{'approve' if decision == ApprovalDecision.APPROVE else 'reject'}",
        entity_type="action_request",
        entity_id=ar.id,
        details={
            "number": ar.number,
            "tool": ar.tool,
            "risk_class": RiskClass(ar.risk_class).value,
            "approver_roles": approval.approver_roles,
            "comment": comment,
        },
    )
    return approval


# --- karar ---------------------------------------------------------------------------------------------


async def approve(
    session: AsyncSession,
    action_id: str,
    user: CurrentUser,
    comment: str = "",
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    llm: Any = None,
) -> ActionRequest:
    """Onay kaydeder; onay kuralı sağlandıysa isteği onaylayan aktörle yürütür.

    `ActionExpiredError` yükseltilirse istek `expired` olarak işaretlenmiştir: çağıran commit etmeli.
    """
    settings = settings or get_settings()
    now = ensure_utc(now) if now else _now()
    ar = await _lock(session, action_id)
    _check_decider(ar, user)
    await _check_parties(session, ar, user)
    await _require_open(session, ar, now, user.username)
    await _record(session, ar, user, ApprovalDecision.APPROVE, comment, now)
    await _touch(session, ar, now)
    if await is_satisfied(session, ar, settings):
        await _execute(session, ar, user, now=now, settings=settings, llm=llm)
        await _notify_request(session, ar, now=now, settings=settings, llm=llm)
    return ar


async def reject(
    session: AsyncSession,
    action_id: str,
    user: CurrentUser,
    comment: str = "",
    *,
    now: datetime | None = None,
) -> ActionRequest:
    now = ensure_utc(now) if now else _now()
    ar = await _lock(session, action_id)
    _check_decider(ar, user)
    await _check_parties(session, ar, user)
    await _require_open(session, ar, now, user.username)
    await _record(session, ar, user, ApprovalDecision.REJECT, comment, now)
    ar.state = AS.REJECTED
    await _touch(session, ar, now)
    await _close_decision(session, ar, user, applied=False, now=now)
    await _notify_request(session, ar, now=now)
    return ar


# --- yürütme -------------------------------------------------------------------------------------------


async def _close_decision(
    session: AsyncSession, ar: ActionRequest, user: CurrentUser, *, applied: bool, now: datetime
) -> None:
    """Yapay zekâ kararını (varsa) insan kararıyla kapatır."""
    if not ar.decision_id:
        return
    decision = await session.get(AIDecision, ar.decision_id)
    if decision is None:
        return
    decision.approved_by = user.username if applied else None
    decision.approved_at = now if applied else None
    decision.output = {
        **(decision.output or {}),
        "applied": applied,
        "action_request_state": ActionRequestState(ar.state).value,
    }
    await session.flush()


async def _fail(
    session: AsyncSession, ar: ActionRequest, user: CurrentUser, error: ToolError, now: datetime
) -> None:
    ar.state = AS.FAILED
    ar.error = error.message
    ar.executed_by = user.username
    ar.executed_at = now
    await _touch(session, ar, now)
    await audit.record(
        session,
        actor=user.username,
        action="action_request.failed",
        entity_type="action_request",
        entity_id=ar.id,
        details={"number": ar.number, "tool": ar.tool, "error": error.to_dict()},
    )
    await _close_decision(session, ar, user, applied=False, now=now)


async def _execute(
    session: AsyncSession,
    ar: ActionRequest,
    user: CurrentUser,
    *,
    now: datetime,
    settings: Settings,
    llm: Any,
) -> None:
    spec = registry.find(ar.tool)
    if spec is None:
        await _fail(session, ar, user, ToolError(f"Araç artık kayıtlı değil: {ar.tool}"), now)
        return
    try:
        args: BaseModel = executor.validate_args(spec, dict(ar.args or {}))
    except ToolError as exc:
        await _fail(session, ar, user, exc, now)
        return
    ctx = ToolContext(
        session=session, actor=user, settings=settings, now=now, llm=llm, action_request=ar
    )
    stored = RiskClass(ar.risk_class)
    try:
        current = await executor.resolve_risk(spec, ctx, args)
    except Exception as exc:  # noqa: BLE001 — risk çözülemezse yürütülmez (failed)
        await _fail(session, ar, user, executor.translate_error(exc), now)
        return
    if risk_rank(current) > risk_rank(stored):
        # Onay beklerken risk arttı: yeni sınıfın onay kuralı aranır
        ar.risk_class = current
        ar.required_approvals = executor.required_approvals(current)
        await _touch(session, ar, now)
        await audit.record(
            session,
            actor=user.username,
            action="action_request.risk_raised",
            entity_type="action_request",
            entity_id=ar.id,
            details={"number": ar.number, "from": stored.value, "to": current.value},
        )
        if not await is_satisfied(session, ar, settings):
            return
    try:
        executor.check_connectors(spec)
    except ToolError as exc:
        await _fail(session, ar, user, exc, now)
        return

    ar.state = AS.APPROVED
    await _touch(session, ar, now)
    result, error = await executor.run_handler(spec, ctx, args)
    if error is not None:
        await _fail(session, ar, user, error, now)
        return
    ar.state = AS.EXECUTED
    ar.result = result
    ar.executed_by = user.username
    ar.executed_at = now
    await _touch(session, ar, now)
    await audit.record(
        session,
        actor=user.username,
        action="action_request.execute",
        entity_type="action_request",
        entity_id=ar.id,
        details={
            "number": ar.number,
            "tool": ar.tool,
            "risk_class": RiskClass(ar.risk_class).value,
            "requested_by": ar.requested_by,
        },
    )
    await _close_decision(session, ar, user, applied=True, now=now)

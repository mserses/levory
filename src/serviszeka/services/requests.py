"""Hizmet talepleri (REQ0000001): iş onayı + karşılama motoru + insansız çözüm ölçümü.

Akış::

    submitted -> (iş onayı gerekiyorsa) awaiting_approval -> fulfilling -> fulfilled
                                         \\-> rejected        \\-> failed / rejected
    (submitted / awaiting_approval / fulfilling / failed) -> cancelled

- **İş onayı** (yönetici ya da adı verilen grup) risk onayından ayrıdır ve önce gelir. Yapay zekâ,
  talep eden ve talebi açan kişi iş onayı veremez. Yönetici dizinden (`IdentityProvider`) bulunur.
- **Karşılama** plan adımlarını sırayla `tools.execute_tool` ile **`ai:fulfilment`** aktörüyle
  çalıştırır (deterministik katalog planı: güven 1.0). Böylece R1 adımlar yalnızca politika izin
  verirse (gölge mod kapalı + güven >= eşik) çalışır, R2/R3 adımlar her zaman eylem isteği
  (ActionRequest) açar ve talep o adımda bekler. İstek insan onayıyla yürütülünce
  (`tools.actions` -> `on_action_resolved`) karşılama kaldığı yerden devam eder; istek
  reddedilir/süresi dolar/başarısız olursa talep `rejected`/`failed` olur.
- Adım idempotency anahtarı `"{talep}:{adım}:{deneme}"`: aynı adım iki kez yürütülmez.
- **İnsansız çözüm (touchless):** talep `fulfilled` ve insan onayı (iş onayı ya da adımın eylem
  isteği onayı) **ve** insan düzenlemesi (form düzenleme, yeniden deneme, el ile adım/çalıştırma)
  sayısı sıfırsa `touchless=true`; değilse `touchless_reason`. Faz 3 kapısı: standart taleplerin
  en az %50'si insansız çözülür (`touchless_metrics`).

Fonksiyonlar flush eder, commit etmez.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.auth import ROLE_ADMIN, ROLE_AGENT, ROLE_AI_AGENT, ROLE_APPROVER, CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.connectors.base import IDENTITY, ConnectorError, IdentityProvider
from serviszeka.domain.enums import (
    ActionRequestState,
    ApprovalDecision,
    BusinessApprovalState,
    CatalogApprovalPolicy,
    ServiceRequestState,
)
from serviszeka.models import ActionApproval, ActionRequest, CatalogItem, ServiceRequest
from serviszeka.services import catalog
from serviszeka.services.changes import same_identity
from serviszeka.tools.redaction import jsonable, mask_value

RS = ServiceRequestState
BA = BusinessApprovalState

FULFILMENT_ACTOR = CurrentUser("ai:fulfilment", frozenset({ROLE_AI_AGENT}))
# Katalog planı deterministiktir (LLM kararı değil): politika yine gölge modu ve risk sınırını uygular
FULFILMENT_CONFIDENCE = 1.0
OPEN_STATES = frozenset({RS.SUBMITTED, RS.AWAITING_APPROVAL, RS.FULFILLING})
TERMINAL_STATES = frozenset({RS.FULFILLED, RS.REJECTED, RS.FAILED, RS.CANCELLED})
# Kapı ölçümünde paydaya giren sonuçlar (iptal edilenler hariç)
MEASURED_STATES = frozenset({RS.FULFILLED, RS.REJECTED, RS.FAILED})
GATE_TARGET = 0.5
MAX_RESULT_CHARS = 4000

_TOUCH_LABELS = {
    "business_approval": "iş onayı",
    "action_approval": "eylem isteği insan onayı",
    "edit": "insan düzenlemesi",
    "retry": "insan yeniden denemesi",
    "manual": "el ile adım",
    "manual_run": "insan tarafından çalıştırılan adım",
}


# --- hatalar --------------------------------------------------------------------------------------


class RequestError(Exception):
    status_code = 400


class RequestNotFoundError(RequestError, LookupError):
    status_code = 404


class RequestForbiddenError(RequestError, PermissionError):
    status_code = 403


class RequestStateError(RequestError, ValueError):
    status_code = 409


class RequestVersionConflictError(RequestStateError):
    pass


class RequestValidationError(RequestError, ValueError):
    status_code = 422

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class RequestUnavailableError(RequestError, ValueError):
    status_code = 503


# --- yardımcılar -------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _identity() -> IdentityProvider:
    from serviszeka.tools.connectors import connectors

    provider = connectors.get(IDENTITY)
    if provider is None:
        raise RequestUnavailableError(
            "Kimlik bağlayıcısı yapılandırılmamış (SZ_CONNECTOR_PROVIDER); onaylayan bulunamıyor."
        )
    return provider


async def same_person(
    provider: IdentityProvider | None,
    a: str | None,
    b: str | None,
    *,
    assume_same_on_error: bool = False,
) -> bool:
    """Aynı kişi mi: kimlikler aynı (büyük/küçük harf duyarsız) ya da dizinde aynı nesne.

    `assume_same_on_error=True`: dışlama denetimlerinde (talep eden / hedef karar veremez) dizin
    okunamazsa "aynı kişi" sayılır — denetim kapalı-başarısız olur (dizin kesintisinde talep edenin
    diğer kimliği onaylayamaz). İzin denetimlerinde (yönetici mi?) varsayılan False kalır.
    """
    if not a or not b:
        return False
    if same_identity(a, b):
        return True
    if provider is None:
        return False
    try:
        ua, ub = await provider.get_user(a), await provider.get_user(b)
    except ConnectorError:
        return assume_same_on_error
    return ua is not None and ub is not None and ua.id == ub.id


def step_targets(req: ServiceRequest) -> list[str]:
    """Plan araç adımlarının hedef kullanıcıları (`user` argümanı, şablon çözülmüş)."""
    ctx = template_context(req)
    targets: list[str] = []
    for step in req.plan or []:
        if step.get("manual") or not isinstance(step.get("args"), dict):
            continue
        value = catalog.render_args(step["args"], ctx).get("user")
        if isinstance(value, str) and value.strip() and value not in targets:
            targets.append(value)
    return targets


def _excluded_parties(req: ServiceRequest) -> list[str]:
    """İş onayı veremeyecek kişiler: talep eden, talebi açan (insan) ve adımların hedefleri."""
    parties = [req.requester, *step_targets(req)]
    if not req.opened_by.startswith("ai:"):
        parties.append(req.opened_by)
    return [p for p in parties if p and not p.startswith("ai:")]


async def _next_number(session: AsyncSession) -> str:
    last = await session.scalar(
        select(func.max(ServiceRequest.number)).where(ServiceRequest.number.like("REQ%"))
    )
    n = int(last[3:]) if last and last[3:].isdigit() else 0
    return f"REQ{n + 1:07d}"


async def _touch(session: AsyncSession, req: ServiceRequest, now: datetime) -> None:
    req.version += 1
    req.updated_at = now
    await session.flush()


def _add_touch(req: ServiceRequest, kind: str, actor: str, now: datetime, detail: str = "") -> None:
    req.touches = [
        *(req.touches or []),
        {"kind": kind, "actor": actor, "at": now.isoformat(), "detail": detail},
    ]
    if kind in ("business_approval", "action_approval"):
        req.human_approvals += 1
    else:
        req.human_edits += 1


def _set_step(req: ServiceRequest, index: int, data: dict[str, Any]) -> None:
    results = [r for r in (req.step_results or []) if r.get("index") != index]
    results.append({"index": index, **data})
    req.step_results = sorted(results, key=lambda r: r["index"])


def _short(value: Any) -> Any:
    """Adım sonucu: JSON'a uygun, PII maskeli, boyut sınırlı (talep edene de görünür)."""
    data = mask_value(jsonable(value))
    if len(str(data)) > MAX_RESULT_CHARS:
        return {"truncated": True}
    return data


async def _audit(
    session: AsyncSession, req: ServiceRequest, actor: str, action: str, **details: Any
) -> None:
    await audit.record(
        session,
        actor=actor,
        action=f"service_request.{action}",
        entity_type="service_request",
        entity_id=req.id,
        details={"number": req.number, "state": RS(req.state).value, **details},
    )


def template_context(req: ServiceRequest) -> dict[str, Any]:
    return catalog.template_context(
        requester=req.requester,
        request_id=req.id,
        request_number=req.number,
        form=req.form_values or {},
    )


# --- okuma ---------------------------------------------------------------------------------------------


async def get_request(session: AsyncSession, request_id: str) -> ServiceRequest | None:
    req = await session.get(ServiceRequest, request_id)
    if req is None:
        req = await session.scalar(select(ServiceRequest).where(ServiceRequest.number == request_id))
    return req


async def list_requests(
    session: AsyncSession,
    *,
    requester: str | None = None,
    state: ServiceRequestState | str | None = None,
    item_key: str | None = None,
    standard: bool | None = None,
    touchless: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ServiceRequest]:
    stmt = select(ServiceRequest)
    if requester is not None:
        stmt = stmt.where(func.lower(ServiceRequest.requester) == requester.strip().casefold())
    if state is not None:
        stmt = stmt.where(ServiceRequest.state == ServiceRequestState(state))
    if item_key is not None:
        stmt = stmt.where(ServiceRequest.item_key == item_key)
    if standard is not None:
        stmt = stmt.where(ServiceRequest.standard.is_(standard))
    if touchless is not None:
        stmt = stmt.where(ServiceRequest.touchless.is_(touchless))
    stmt = stmt.order_by(ServiceRequest.created_at.desc(), ServiceRequest.number.desc())
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


async def linked_actions(session: AsyncSession, request_id: str) -> list[ActionRequest]:
    stmt = (
        select(ActionRequest)
        .where(ActionRequest.request_id == request_id)
        .order_by(ActionRequest.created_at, ActionRequest.number)
    )
    return list((await session.scalars(stmt)).all())


async def _lock(session: AsyncSession, request_id: str) -> ServiceRequest:
    req = await get_request(session, request_id)
    if req is None:
        raise RequestNotFoundError(f"Hizmet talebi bulunamadı: {request_id}")
    db_version = await session.scalar(
        select(ServiceRequest.version).where(ServiceRequest.id == req.id).with_for_update()
    )
    if db_version != req.version:
        await session.refresh(req)
    return req


def _check_version(req: ServiceRequest, expected: int | None) -> None:
    if expected is not None and expected != req.version:
        raise RequestVersionConflictError(
            f"{req.number} değişmiş (beklenen sürüm {expected}, güncel {req.version})."
        )


# --- açılış ve iş onayı -------------------------------------------------------------------------------


async def create_request(
    session: AsyncSession,
    item: CatalogItem,
    *,
    requester: str,
    opened_by: CurrentUser,
    form_values: dict[str, Any] | None,
    settings: Settings | None = None,
    now: datetime | None = None,
    llm: Any = None,
) -> ServiceRequest:
    """Talebi açar ve hemen ilerletir (iş onayı ya da karşılama)."""
    settings = settings or get_settings()
    now = now or _now()
    if not item.active:
        raise RequestStateError(f"Katalog öğesi etkin değil: {item.name}")
    requester = (requester or "").strip()
    if not requester or requester.startswith("ai:") or len(requester) > 200:
        raise RequestValidationError("Talep eden geçerli bir kullanıcı olmalı.")
    try:
        values = catalog.validate_form(item.form_schema or {}, form_values)
    except catalog.CatalogError as exc:
        raise RequestValidationError(str(exc), exc.errors) from exc
    req = ServiceRequest(
        number=await _next_number(session),
        catalog_item_id=item.id,
        item_key=item.key,
        item_name=item.name,
        standard=item.standard,
        plan=list(item.fulfilment_plan or []),
        requester=requester,
        opened_by=opened_by.username,
        form_values=values,
        state=RS.SUBMITTED,
        approval_policy=item.approval_policy,
        approval_state=BA.NOT_REQUIRED,
        approval_comment="",
        current_step=0,
        attempt=1,
        step_results=[],
        human_approvals=0,
        human_edits=0,
        touches=[],
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(req)
    await session.flush()
    await _audit(
        session,
        req,
        opened_by.username,
        "create",
        item=item.key,
        requester=requester,
        standard=item.standard,
        form=mask_value(values),
    )
    await _start(session, req, settings=settings, now=now, llm=llm)
    return req


async def _start(
    session: AsyncSession, req: ServiceRequest, *, settings: Settings, now: datetime, llm: Any
) -> None:
    policy = CatalogApprovalPolicy(req.approval_policy)
    if policy == CatalogApprovalPolicy.NONE:
        req.state = RS.FULFILLING
        await _touch(session, req, now)
        await run(session, req, settings=settings, now=now, llm=llm)
        return
    if policy == CatalogApprovalPolicy.MANAGER:
        provider = _identity()
        try:
            manager = await provider.get_manager(req.requester)
        except ConnectorError as exc:
            raise RequestUnavailableError(f"Yönetici dizinden okunamadı: {exc}") from exc
        if manager is None:
            req.error = "Dizinde yönetici bulunamadı; talebi yalnızca admin onaylayabilir."
        req.approver = manager.user_principal_name if manager else None
    else:
        item = await session.get(CatalogItem, req.catalog_item_id)
        req.approver = item.approval_group if item else None
    req.state = RS.AWAITING_APPROVAL
    req.approval_state = BA.PENDING
    await _touch(session, req, now)
    await _audit(session, req, "system:requests", "awaiting_approval", approver=req.approver)


async def can_decide(user: CurrentUser, req: ServiceRequest) -> bool:
    """İş onayı verebilir mi (durumdan bağımsız kimlik/rol denetimi)."""
    if user.is_ai:
        return False
    try:
        provider = _identity()
    except RequestUnavailableError:
        provider = None
    # Talep eden, açan ve adımın hedefi (ör. yeni çalışan hesabında form.user) kendi lehine onay
    # veremez; dizin okunamazsa kapalı-başarısız
    for party in _excluded_parties(req):
        if await same_person(provider, user.username, party, assume_same_on_error=True):
            return False
    if ROLE_ADMIN in user.roles:
        return True
    policy = CatalogApprovalPolicy(req.approval_policy)
    if policy == CatalogApprovalPolicy.MANAGER:
        return await same_person(provider, user.username, req.approver)
    if policy == CatalogApprovalPolicy.GROUP and provider is not None and req.approver:
        try:
            groups = await provider.list_groups(user.username)
        except ConnectorError:
            return False
        return any(g.id == req.approver for g in groups)
    return False


async def decide(
    session: AsyncSession,
    request_id: str,
    user: CurrentUser,
    decision: ApprovalDecision | str,
    comment: str = "",
    *,
    expected_version: int | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    llm: Any = None,
) -> ServiceRequest:
    """İş onayı / reddi. Onayda karşılama hemen başlar (adımlar `ai:fulfilment` ile)."""
    settings = settings or get_settings()
    now = now or _now()
    decision = ApprovalDecision(decision)
    req = await _lock(session, request_id)
    _check_version(req, expected_version)
    if user.is_ai:
        raise RequestForbiddenError("Yapay zekâ hizmet talebi onaylayamaz ya da reddedemez.")
    if RS(req.state) != RS.AWAITING_APPROVAL:
        raise RequestStateError(f"{req.number} iş onayı beklemiyor (durum: {RS(req.state).value}).")
    if not await can_decide(user, req):
        raise RequestForbiddenError(
            "Bu talep için iş onayı yetkiniz yok (talep eden/açan onaylayamaz; yönetici ya da "
            "onay grubu üyesi gerekir)."
        )
    req.approved_by = user.username
    req.approved_at = now
    req.approval_comment = comment
    if decision == ApprovalDecision.REJECT:
        req.approval_state = BA.REJECTED
        req.state = RS.REJECTED
        req.closed_at = now
        req.touchless = False
        req.touchless_reason = "İş onayı reddedildi."
        await _touch(session, req, now)
        await _audit(session, req, user.username, "reject", comment=comment)
        return req
    req.approval_state = BA.APPROVED
    _add_touch(req, "business_approval", user.username, now, comment)
    req.state = RS.FULFILLING
    req.error = None
    await _touch(session, req, now)
    await _audit(session, req, user.username, "approve", comment=comment)
    await run(session, req, settings=settings, now=now, llm=llm)
    return req


# --- karşılama motoru -----------------------------------------------------------------------------------


async def run(
    session: AsyncSession,
    req: ServiceRequest,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    llm: Any = None,
) -> ServiceRequest:
    """Plan adımlarını `current_step`'ten itibaren çalıştırır; bir adım beklerse/başarısızsa durur."""
    from serviszeka.tools import ToolError, ToolStatus, execute_tool

    settings = settings or get_settings()
    now = now or _now()
    if RS(req.state) != RS.FULFILLING or req.waiting_action_id:
        return req
    plan = req.plan or []
    ctx = template_context(req)
    while req.current_step < len(plan):
        index = req.current_step
        step = plan[index]
        title = step.get("title", "")
        if not catalog.step_enabled(step, ctx):
            _set_step(req, index, {"title": title, "tool": step.get("tool"), "status": "skipped",
                                   "at": now.isoformat()})
            req.current_step += 1
            continue
        if step.get("manual"):
            _set_step(req, index, {"title": title, "tool": None, "status": "waiting_manual",
                                   "at": now.isoformat()})
            await _touch(session, req, now)
            return req
        tool = step["tool"]
        args = catalog.render_args(step.get("args", {}), ctx)
        try:
            result = await execute_tool(
                session,
                FULFILMENT_ACTOR,
                tool,
                args,
                idempotency_key=f"{req.id}:{index}:{req.attempt}",
                reason=f"{req.number} · {req.item_name} · {title}",
                confidence=FULFILMENT_CONFIDENCE,
                links={"request_id": req.id},
                settings=settings,
                llm=llm,
                now=now,
            )
        except ToolError as exc:
            await _fail_step(session, req, index, step, exc.to_dict(), now)
            return req
        base = {"title": title, "tool": tool, "risk_class": result.risk_class.value,
                "at": now.isoformat()}
        if result.status == ToolStatus.EXECUTED:
            _set_step(req, index, {**base, "status": "executed", "actor": FULFILMENT_ACTOR.username,
                                   "result": _short(result.result or {})})
            req.current_step += 1
            await _touch(session, req, now)
            continue
        if result.status == ToolStatus.PENDING_APPROVAL:
            _set_step(req, index, {**base, "status": "pending_approval",
                                   "action_request_id": result.action_request_id})
            req.waiting_action_id = result.action_request_id
            await _touch(session, req, now)
            await _audit(session, req, FULFILMENT_ACTOR.username, "step_pending", step=index,
                         tool=tool, action_request_id=result.action_request_id)
            return req
        error = result.error or {"code": result.status.value, "message": result.message}
        await _fail_step(session, req, index, step, error, now, risk=result.risk_class.value)
        return req
    await _finish(session, req, now)
    return req


async def _fail_step(
    session: AsyncSession,
    req: ServiceRequest,
    index: int,
    step: dict[str, Any],
    error: dict[str, Any],
    now: datetime,
    *,
    risk: str | None = None,
    state: ServiceRequestState = RS.FAILED,
) -> None:
    data = {"title": step.get("title", ""), "tool": step.get("tool"), "status": "failed",
            "error": mask_value(error), "at": now.isoformat()}
    if risk:
        data["risk_class"] = risk
    _set_step(req, index, data)
    req.state = state
    req.error = f"Adım {index + 1} ({step.get('title', '')}): {error.get('message', '')}"[:2000]
    req.waiting_action_id = None
    req.closed_at = now
    req.touchless = False
    req.touchless_reason = "Karşılama tamamlanmadı."
    await _touch(session, req, now)
    await _audit(session, req, FULFILMENT_ACTOR.username, "step_failed", step=index,
                 tool=step.get("tool"), error=mask_value(error))


def touchless_reason(req: ServiceRequest) -> str | None:
    if req.human_approvals == 0 and req.human_edits == 0:
        return None
    parts = []
    for t in req.touches or []:
        label = _TOUCH_LABELS.get(t.get("kind", ""), t.get("kind", ""))
        detail = f" ({t['detail']})" if t.get("detail") else ""
        parts.append(f"{label}: {t.get('actor', '?')}{detail}")
    return "; ".join(parts) or "İnsan müdahalesi var."


async def _finish(session: AsyncSession, req: ServiceRequest, now: datetime) -> None:
    req.state = RS.FULFILLED
    req.fulfilled_at = now
    req.closed_at = now
    req.error = None
    req.waiting_action_id = None
    req.touchless = req.human_approvals == 0 and req.human_edits == 0
    req.touchless_reason = touchless_reason(req)
    await _touch(session, req, now)
    await _audit(session, req, FULFILMENT_ACTOR.username, "fulfilled", touchless=req.touchless,
                 human_approvals=req.human_approvals, human_edits=req.human_edits)


async def on_action_resolved(
    session: AsyncSession,
    ar: ActionRequest,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    llm: Any = None,
) -> None:
    """`tools.actions` bağlı eylem isteği sonuçlanınca çağırır: karşılamayı sürdürür ya da kapatır."""
    if not ar.request_id:
        return
    now = now or _now()
    req = await session.get(ServiceRequest, ar.request_id)
    if req is None or RS(req.state) != RS.FULFILLING or req.waiting_action_id != ar.id:
        return
    index = req.current_step
    step = (req.plan or [])[index] if index < len(req.plan or []) else {}
    state = ActionRequestState(ar.state)
    approvers = list(
        (
            await session.scalars(
                select(ActionApproval).where(
                    ActionApproval.action_request_id == ar.id,
                    ActionApproval.decision == ApprovalDecision.APPROVE,
                )
            )
        ).all()
    )
    for a in approvers:
        _add_touch(req, "action_approval", a.approver, now, f"{ar.number} {ar.tool}")
    req.waiting_action_id = None
    base = {"title": step.get("title", ""), "tool": ar.tool, "risk_class": ar.risk_class,
            "action_request_id": ar.id, "at": now.isoformat()}
    if state == ActionRequestState.EXECUTED:
        _set_step(req, index, {**base, "status": "executed", "actor": ar.executed_by,
                               "result": _short(ar.result or {})})
        req.current_step += 1
        await _touch(session, req, now)
        await _audit(session, req, ar.executed_by or "system:requests", "step_executed",
                     step=index, tool=ar.tool, action_request_id=ar.id)
        await run(session, req, settings=settings, now=now, llm=llm)
        return
    if state == ActionRequestState.REJECTED:
        await _fail_step(session, req, index, step,
                         {"code": "rejected", "message": f"{ar.number} reddedildi."}, now,
                         risk=ar.risk_class, state=RS.REJECTED)
        return
    if state in (ActionRequestState.FAILED, ActionRequestState.EXPIRED):
        message = ar.error or f"{ar.number} {state.value}."
        await _fail_step(session, req, index, step, {"code": state.value, "message": message},
                         now, risk=ar.risk_class)


async def resume(
    session: AsyncSession,
    request_id: str,
    actor: CurrentUser,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    llm: Any = None,
) -> ServiceRequest:
    """Bekleyen karşılamayı ilerletir (`request.run_fulfillment_step`). İnsan çağırırsa dokunuş sayılır.

    Bekleyen eylem isteği zaten sonuçlandıysa (kanca kaçtıysa) sonucu işlenir; hâlâ bekliyorsa ya da
    sıradaki adım el ile adımsa değişiklik olmaz.
    """
    now = now or _now()
    req = await _lock(session, request_id)
    if RS(req.state) != RS.FULFILLING:
        raise RequestStateError(f"{req.number} karşılama aşamasında değil (durum: {RS(req.state).value}).")
    if not actor.is_ai:
        _add_touch(req, "manual_run", actor.username, now)
    if req.waiting_action_id:
        ar = await session.get(ActionRequest, req.waiting_action_id)
        if ar is not None and ActionRequestState(ar.state) != ActionRequestState.PENDING:
            await on_action_resolved(session, ar, now=now, settings=settings, llm=llm)
        await _touch(session, req, now)
        return req
    await run(session, req, settings=settings, now=now, llm=llm)
    return req


async def complete_manual_step(
    session: AsyncSession,
    request_id: str,
    actor: CurrentUser,
    note: str = "",
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    llm: Any = None,
) -> ServiceRequest:
    """Sıradaki el ile adımı tamamlandı işaretler ve karşılamayı sürdürür (`request.complete`).

    El ile adım insan işidir: kim işaretlerse işaretlesin talep insansız sayılmaz.
    """
    now = now or _now()
    req = await _lock(session, request_id)
    if RS(req.state) != RS.FULFILLING:
        raise RequestStateError(f"{req.number} karşılama aşamasında değil (durum: {RS(req.state).value}).")
    plan = req.plan or []
    if req.current_step >= len(plan) or not plan[req.current_step].get("manual"):
        raise RequestStateError(f"{req.number} için tamamlanacak el ile adım yok.")
    index = req.current_step
    _set_step(req, index, {"title": plan[index].get("title", ""), "tool": None,
                           "status": "executed", "actor": actor.username, "note": note[:2000],
                           "at": now.isoformat()})
    _add_touch(req, "manual", actor.username, now, plan[index].get("title", ""))
    req.current_step += 1
    await _touch(session, req, now)
    await _audit(session, req, actor.username, "manual_step", step=index, note=note[:500])
    await run(session, req, settings=settings, now=now, llm=llm)
    return req


# --- insan işlemleri (düzenleme, yeniden deneme, iptal) ------------------------------------------------


EDITABLE_STATES = frozenset({RS.SUBMITTED, RS.AWAITING_APPROVAL, RS.FAILED})


async def update_form(
    session: AsyncSession,
    request_id: str,
    user: CurrentUser,
    form_values: dict[str, Any],
    *,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> ServiceRequest:
    """Ajan form değerlerini düzeltir (karşılama başlamadan ya da başarısız talepte): insan düzenlemesi.

    İş onayı verilmiş talepte değerler değiştirilemez (onay o değerler içindir).
    """
    now = now or _now()
    req = await _lock(session, request_id)
    _check_version(req, expected_version)
    if RS(req.state) not in EDITABLE_STATES:
        raise RequestStateError(f"{req.number} bu durumda düzenlenemez ({RS(req.state).value}).")
    item = await session.get(CatalogItem, req.catalog_item_id)
    try:
        values = catalog.validate_form((item.form_schema if item else {}) or {}, form_values)
    except catalog.CatalogError as exc:
        raise RequestValidationError(str(exc), exc.errors) from exc
    if BA(req.approval_state) == BA.APPROVED and values != (req.form_values or {}):
        # İş onayı bu değerlere verildi: değiştirmek onaysız hedefe/kaynağa adım çalıştırırdı
        raise RequestStateError(
            f"{req.number} için iş onayı verilmiş; form değiştirilemez. Talebi iptal edip yeni "
            "talep açın."
        )
    req.form_values = values
    _add_touch(req, "edit", user.username, now, "form")
    await _touch(session, req, now)
    await _audit(session, req, user.username, "update", form=mask_value(values))
    return req


async def retry(
    session: AsyncSession,
    request_id: str,
    user: CurrentUser,
    *,
    expected_version: int | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    llm: Any = None,
) -> ServiceRequest:
    """Başarısız talebi başarısız adımdan yeniden çalıştırır (yeni deneme numarası, insan dokunuşu)."""
    now = now or _now()
    req = await _lock(session, request_id)
    _check_version(req, expected_version)
    if RS(req.state) != RS.FAILED:
        raise RequestStateError(f"{req.number} yalnızca başarısızken yeniden denenebilir.")
    req.attempt += 1
    req.state = RS.FULFILLING
    req.error = None
    req.closed_at = None
    req.touchless = None
    req.touchless_reason = None
    _add_touch(req, "retry", user.username, now, f"deneme {req.attempt}")
    await _touch(session, req, now)
    await _audit(session, req, user.username, "retry", attempt=req.attempt)
    await run(session, req, settings=settings, now=now, llm=llm)
    return req


async def cancel(
    session: AsyncSession,
    request_id: str,
    user: CurrentUser,
    reason: str = "",
    *,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> ServiceRequest:
    """Talebi iptal eder; bekleyen bağlı eylem isteği reddedilir (sonradan onaylanıp çalışamaz)."""
    from serviszeka.tools import actions

    now = now or _now()
    req = await _lock(session, request_id)
    _check_version(req, expected_version)
    if RS(req.state) not in (OPEN_STATES | {RS.FAILED}):
        raise RequestStateError(f"{req.number} iptal edilemez (durum: {RS(req.state).value}).")
    previous = RS(req.state)
    await actions.cancel_for_request(session, req.id, user, reason or f"{req.number} iptal edildi",
                                     now=now)
    req.state = RS.CANCELLED
    req.waiting_action_id = None
    req.closed_at = now
    # Ölçüm oyununa karşı: başarısız talebi ya da başkasının talebini iptal etmek onu kapı
    # paydasından düşürmez; "kapandı, insansız değil" sayılır. Talep edenin kendi açık talebinden
    # vazgeçmesi ölçülmez. Yeniden denenen (attempt > 1) talep daha önce başarısız olarak ölçüldü:
    # yeniden deneme + iptal de onu paydadan düşüremez.
    if previous == RS.FAILED or req.attempt > 1:
        req.touchless = False
        req.touchless_reason = f"Başarısız talep iptal edildi ({user.username})."
    elif not same_identity(user.username, req.requester):
        req.touchless = False
        req.touchless_reason = f"Talep eden dışında biri iptal etti ({user.username})."
    else:
        req.touchless = None
        req.touchless_reason = None
    await _touch(session, req, now)
    await _audit(session, req, user.username, "cancel", reason=reason)
    return req


# --- zamanlayıcı: bekleyen talepler ---------------------------------------------------------------

SCHEDULER_ACTOR = "system:scheduler"


async def expire_waiting(
    session: AsyncSession, *, now: datetime | None = None, settings: Settings | None = None
) -> dict[str, int]:
    """Bekleyen talepleri ilerletir/kapatır (zamanlayıcı işi; flush eder, commit etmez).

    - İş onayı `SZ_REQUEST_APPROVAL_TTL_HOURS` içinde verilmeyen talep **reddedilir** (iş onayı
      `rejected`; `failed` değil — yeniden deneme iş onayını atlayamasın). İnsansız sayılmaz.
    - Karşılamada beklediği eylem isteği sonuçlanmış (yürütülmüş/reddedilmiş/başarısız/süresi
      dolmuş) ama kancası kaçmış talep sonucu işlenir (`on_action_resolved`).
    """
    settings = settings or get_settings()
    now = now or _now()
    cutoff = now - timedelta(hours=settings.request_approval_ttl_hours)
    expired = 0
    stale = list(
        (
            await session.scalars(
                select(ServiceRequest).where(
                    ServiceRequest.state == RS.AWAITING_APPROVAL,
                    ServiceRequest.created_at <= cutoff,
                )
            )
        ).all()
    )
    for req in stale:
        req.approval_state = BA.REJECTED
        req.state = RS.REJECTED
        req.closed_at = now
        req.touchless = False
        req.touchless_reason = "İş onayı süresi doldu."
        req.error = (
            f"İş onayı {settings.request_approval_ttl_hours} saat içinde verilmedi (süre doldu)."
        )
        await _touch(session, req, now)
        await _audit(session, req, SCHEDULER_ACTOR, "approval_expired",
                     ttl_hours=settings.request_approval_ttl_hours, approver=req.approver)
        expired += 1
    resumed = 0
    terminal = [ActionRequestState.EXECUTED, ActionRequestState.REJECTED,
                ActionRequestState.FAILED, ActionRequestState.EXPIRED]
    waiting = list(
        (
            await session.execute(
                select(ServiceRequest, ActionRequest)
                .join(ActionRequest, ActionRequest.id == ServiceRequest.waiting_action_id)
                .where(ServiceRequest.state == RS.FULFILLING, ActionRequest.state.in_(terminal))
            )
        ).all()
    )
    for _req, ar in waiting:
        await on_action_resolved(session, ar, now=now, settings=settings)
        resumed += 1
    return {"approval_expired": expired, "resumed": resumed}


# --- erişim -------------------------------------------------------------------------------------------


async def can_view(user: CurrentUser, req: ServiceRequest) -> bool:
    """Ajan/onaylayan/admin hepsini görür; diğerleri kendi taleplerini ve onaylayacaklarını."""
    if user.has_any(ROLE_AGENT, ROLE_APPROVER):
        return True
    if same_identity(user.username, req.requester) or same_identity(user.username, req.opened_by):
        return True
    if RS(req.state) == RS.AWAITING_APPROVAL:
        return await can_decide(user, req)
    return False


# --- ölçüm (Faz 3 kapısı) ----------------------------------------------------------------------------------


@dataclass
class TouchlessMetrics:
    standard_closed: int
    standard_fulfilled: int
    standard_touchless: int
    all_closed: int
    all_touchless: int
    by_item: list[dict[str, Any]]

    @property
    def standard_touchless_rate(self) -> float:
        return self.standard_touchless / self.standard_closed if self.standard_closed else 0.0

    @property
    def meets_gate(self) -> bool:
        return self.standard_closed > 0 and self.standard_touchless_rate >= GATE_TARGET

    def to_dict(self) -> dict[str, Any]:
        return {
            "standard_closed": self.standard_closed,
            "standard_fulfilled": self.standard_fulfilled,
            "standard_touchless": self.standard_touchless,
            "standard_touchless_rate": round(self.standard_touchless_rate, 4),
            "all_closed": self.all_closed,
            "all_touchless": self.all_touchless,
            "gate_target": GATE_TARGET,
            "meets_gate": self.meets_gate,
            "by_item": self.by_item,
        }


async def touchless_metrics(
    session: AsyncSession, *, since: datetime | None = None
) -> TouchlessMetrics:
    """Kapanmış (karşılanan / reddedilen / başarısız / insanca kapatılan iptal) taleplerde insansız oranı."""
    stmt = select(
        ServiceRequest.item_key,
        ServiceRequest.item_name,
        ServiceRequest.standard,
        ServiceRequest.state,
        ServiceRequest.touchless,
    ).where(
        or_(
            ServiceRequest.state.in_(list(MEASURED_STATES)),
            # İnsan tarafından kapatılan iptaller (bkz. cancel) paydada kalır
            and_(ServiceRequest.state == RS.CANCELLED, ServiceRequest.touchless.is_(False)),
        )
    )
    if since is not None:
        stmt = stmt.where(ServiceRequest.created_at >= since)
    rows = (await session.execute(stmt)).all()
    items: dict[str, dict[str, Any]] = {}
    std_closed = std_fulfilled = std_touchless = all_touchless = 0
    for key, name, standard, state, touchless in rows:
        entry = items.setdefault(
            key, {"item_key": key, "item_name": name, "standard": bool(standard), "closed": 0,
                  "fulfilled": 0, "touchless": 0}
        )
        entry["closed"] += 1
        fulfilled = RS(state) == RS.FULFILLED
        entry["fulfilled"] += int(fulfilled)
        is_touchless = bool(fulfilled and touchless)
        entry["touchless"] += int(is_touchless)
        all_touchless += int(is_touchless)
        if standard:
            std_closed += 1
            std_fulfilled += int(fulfilled)
            std_touchless += int(is_touchless)
    return TouchlessMetrics(
        standard_closed=std_closed,
        standard_fulfilled=std_fulfilled,
        standard_touchless=std_touchless,
        all_closed=len(rows),
        all_touchless=all_touchless,
        by_item=sorted(items.values(), key=lambda e: e["item_key"]),
    )


# --- self-servis doğrulaması (identity.verify_requester / reset_password koşullu riski) ---------------


@dataclass
class Verification:
    verified: bool
    reason: str
    user_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"verified": self.verified, "reason": self.reason, "user_id": self.user_id}


async def verify_self_service(
    session: AsyncSession,
    request_id: str | None,
    user: str,
    provider: IdentityProvider,
    *,
    tool: str | None = None,
) -> Verification:
    """Hedef kullanıcı, talebi **kendi oturumuyla** açan talep eden mi?

    Doğrulandı sayılması için hepsi: talep var ve karşılanıyor; talebi açan kimlik (belirteçten)
    talep edenle aynı (başkası/ajan/yapay zekâ adına açılan talep doğrulanmaz); planda `tool` adımı
    var; dizinde hedef kullanıcı ile talep eden aynı nesne ve hesap etkin. Daha güçlü doğrulama
    (MFA adım yükseltme, Verified ID) docs/faz3.md'de açık konudur.
    """
    if not request_id:
        return Verification(False, "Hizmet talebi verilmedi.")
    req = await get_request(session, request_id)
    if req is None:
        return Verification(False, "Hizmet talebi bulunamadı.")
    if RS(req.state) != RS.FULFILLING:
        return Verification(False, "Talep karşılama aşamasında değil.")
    if req.opened_by.startswith("ai:") or not same_identity(req.opened_by, req.requester):
        return Verification(False, "Talep, hesabın sahibi tarafından kendi oturumuyla açılmadı.")
    if tool and tool not in {s.get("tool") for s in (req.plan or [])}:
        return Verification(False, f"Talebin planı {tool} adımını içermiyor.")
    try:
        target = await provider.get_user(user)
        owner = await provider.get_user(req.requester)
    except ConnectorError as exc:
        return Verification(False, f"Dizin okunamadı: {exc}")
    if target is None or owner is None:
        return Verification(False, "Kullanıcı dizinde bulunamadı.")
    if target.id != owner.id:
        return Verification(False, "Hedef kullanıcı talep eden değil.")
    if not target.account_enabled:
        return Verification(False, "Hesap devre dışı.", target.id)
    return Verification(True, "Talep eden kendi oturumuyla doğrulandı.", target.id)

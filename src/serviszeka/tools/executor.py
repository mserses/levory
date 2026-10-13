"""Tek araç yürütme boru hattı: `execute_tool`.

Akış (her çağıran — MCP, iç ajanlar, testler — buradan geçer; işleyiciye başka yoldan ulaşılmaz):

1. Aktör: `ai_agent` rolü -> `ai:<istemci>` (yalnızca `ai_agent` rolü). Araç bulunur, yetki
   denetlenir (insan: araç rollerinden biri; yapay zekâ: `ai_agent` + `ai_allowed`), gereken
   bağlayıcılar yapılandırılmış mı bakılır.
2. Argümanlar doğrulanır (pydantic) ve kanonik hale getirilir; bağlı kayıtlar (olay/değişiklik)
   var mı denetlenir. Hata -> hiçbir şey yazılmadan `ToolValidationError`.
3. Idempotency (R1-R3, `idempotency_key` ile): aynı aktör + anahtar + argüman -> önceki sonuç
   (`replayed`); farklı argüman -> `IdempotencyConflictError` (409). Anahtar işlemden önce
   ayrılır: eşzamanlı aynı anahtar ikinci kez yürütmez (tekil kısıt).
4. Risk sınıfı çözülür: `max(spec.risk_class, spec.risk_fn(...))`.
5. Dağıtım:
   - R0: çalışır (yapay zekâ okuması `tool.read` denetimine yazılır).
   - R1 insan: çalışır (`tool.execute`). R1 yapay zekâ: `policy.evaluate(alan, fiil, güven, LOW)`
     -> `AIDecision` + denetim; yalnızca AUTO_APPLIED ise çalışır, REJECTED ise hiçbir şey olmaz,
     diğer sonuçlarda (öneri/onay/eskalasyon) eylem isteği açılır.
   - R2/R3: asla çalışmaz; eylem isteği (ActionRequest) açılır. Yapay zekâ çağrısı yine
     `policy.evaluate` (HIGH/CRITICAL) + `AIDecision` + denetimden geçer.
6. İşleyici bir SAVEPOINT içinde çalışır: hata olursa kısmi yazma ve bekleyen bildirimler geri
   alınır, sonuç `failed` döner (istisna yükseltilmez; denetim kaydı kalır).

Fonksiyon flush eder, commit etmez; commit (ve `notifications.send_pending`) çağıranın işidir.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, knowledge, notifications, policy
from serviszeka.auth import ROLE_AI_AGENT, CurrentUser, principal
from serviszeka.config import Settings, get_settings
from serviszeka.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotSupportedError,
)
from serviszeka.domain.enums import DecisionOutcome, RiskClass
from serviszeka.models import (
    ActionRequest,
    AIDecision,
    ChangeRequest,
    Incident,
    ServiceRequest,
    ToolInvocation,
)
from serviszeka.privacy import mask_pii
from serviszeka.services import changes, cmdb
from serviszeka.services import incidents as incident_service
from serviszeka.tools.connectors import connectors as connector_registry
from serviszeka.tools.redaction import args_hash, jsonable, redact_args
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import (
    POLICY_RISK,
    IdempotencyConflictError,
    ToolConflictError,
    ToolContext,
    ToolError,
    ToolExecutionError,
    ToolForbiddenError,
    ToolNotFoundError,
    ToolSpec,
    ToolUnavailableError,
    ToolValidationError,
    max_risk,
)

logger = logging.getLogger(__name__)

MAX_INPUT_SUMMARY = 4000
DEFAULT_MODEL = "external"


class ToolStatus(StrEnum):
    EXECUTED = "executed"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"  # politika reddetti (bilinmeyen eylem)
    FAILED = "failed"  # işleyici/servis işlemi reddetti; kısmi yazma geri alındı
    DRY_RUN = "dry_run"


@dataclass
class ToolResult:
    status: ToolStatus
    tool: str
    risk_class: RiskClass
    result: dict[str, Any] | None = None
    action_request_id: str | None = None
    decision_id: str | None = None
    replayed: bool = False
    message: str = ""
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Çağırana (MCP) dönen gövde. Bekleyen istek: `{"status": "pending_approval", ...}`."""
        data: dict[str, Any] = {
            "status": self.status.value,
            "tool": self.tool,
            "risk_class": RiskClass(self.risk_class).value,
        }
        if self.status == ToolStatus.PENDING_APPROVAL:
            data = {
                "status": self.status.value,
                "action_request_id": self.action_request_id,
                "tool": self.tool,
                "risk_class": RiskClass(self.risk_class).value,
                "message": self.message,
            }
        else:
            if self.result is not None:
                data["result"] = self.result
            if self.error is not None:
                data["error"] = self.error
            if self.message:
                data["message"] = self.message
        if self.replayed:
            data["replayed"] = True
        return data

    def _storage(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "risk_class": RiskClass(self.risk_class).value,
            "result": self.result,
            "action_request_id": self.action_request_id,
            "decision_id": self.decision_id,
            "message": self.message,
            "error": self.error,
        }

    @classmethod
    def _from_storage(cls, tool: str, data: dict[str, Any]) -> ToolResult:
        return cls(
            status=ToolStatus(data["status"]),
            tool=tool,
            risk_class=RiskClass(data["risk_class"]),
            result=data.get("result"),
            action_request_id=data.get("action_request_id"),
            decision_id=data.get("decision_id"),
            message=data.get("message") or "",
            error=data.get("error"),
            replayed=True,
        )


@dataclass
class ActionLinks:
    incident_id: str | None = None
    change_id: str | None = None
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --- aktör ve yetki -----------------------------------------------------------------------------


def as_tool_actor(user: CurrentUser) -> CurrentUser:
    """`ai_agent` rolü taşıyan kimliği `ai:<ad>` yapay zekâ aktörüne indirger (auth.principal)."""
    if ROLE_AI_AGENT in user.roles:
        return principal(user.username, user.roles)
    return user


def can_call(spec: ToolSpec, user: CurrentUser) -> bool:
    user = as_tool_actor(user)
    if user.is_ai:
        return spec.ai_allowed and ROLE_AI_AGENT in user.roles
    return user.has_any(*spec.roles)


def _authorize(spec: ToolSpec, user: CurrentUser) -> None:
    if can_call(spec, user):
        return
    if user.is_ai:
        raise ToolForbiddenError(
            f"{spec.name}: yapay zekâ aktörü bu aracı çağıramaz (ai_agent rolü gerekir)."
        )
    raise ToolForbiddenError(
        f"{spec.name} için yetkiniz yok (gereken rollerden biri: {', '.join(sorted(spec.roles))})."
    )


def check_connectors(spec: ToolSpec) -> None:
    missing = connector_registry.missing(spec.connectors)
    if missing:
        raise ToolUnavailableError(
            f"{spec.name} kullanılamıyor: bağlayıcı yapılandırılmamış ({', '.join(missing)})."
        )


# --- doğrulama ve risk -------------------------------------------------------------------------


def _errors(exc: ValidationError) -> list[dict[str, Any]]:
    return json.loads(exc.json(include_url=False, include_input=False))


def validate_args(spec: ToolSpec, args: Any) -> BaseModel:
    if not isinstance(args, dict):
        raise ToolValidationError(f"{spec.name}: argümanlar nesne olmalı.")
    try:
        return spec.input_model.model_validate(args)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e.get("loc", ())) or "-" for e in _errors(exc))
        raise ToolValidationError(
            f"{spec.name}: argümanlar geçersiz ({fields}).", errors=_errors(exc)
        ) from exc


async def resolve_risk(spec: ToolSpec, ctx: ToolContext, args: BaseModel) -> RiskClass:
    """Temel sınıf ile koşullu sınıfın büyüğü (koşullu risk temel sınıfı asla düşüremez)."""
    risk = RiskClass(spec.risk_class)
    if spec.risk_fn is not None:
        value = spec.risk_fn(ctx, args)
        if inspect.isawaitable(value):
            value = await value
        risk = max_risk(risk, RiskClass(value))
    return risk


async def _safe_risk(spec: ToolSpec, ctx: ToolContext, args: BaseModel) -> RiskClass:
    """Koşullu risk hesaplanamazsa (ör. kayıt yok) araç hatası; hiçbir şey çalışmaz."""
    try:
        return await resolve_risk(spec, ctx, args)
    except Exception as exc:  # araç hatasına çevrilip yükseltilir
        raise translate_error(exc) from exc


def _links(
    spec: ToolSpec, args: dict[str, Any], explicit: ActionLinks | dict | None
) -> ActionLinks:
    links = ActionLinks()
    for arg_name, kind in spec.links.items():
        value = args.get(arg_name)
        if isinstance(value, str) and value:
            setattr(links, f"{kind}_id", value)
    if isinstance(explicit, dict):
        explicit = ActionLinks(**explicit)
    if explicit is not None:
        for kind in ("incident", "change", "request"):
            value = getattr(explicit, f"{kind}_id")
            if value:
                setattr(links, f"{kind}_id", value)
    return links


async def _check_links(session: AsyncSession, links: ActionLinks) -> None:
    if links.incident_id and await session.get(Incident, links.incident_id) is None:
        raise ToolNotFoundError(f"Olay bulunamadı: {links.incident_id}")
    if links.change_id and await session.get(ChangeRequest, links.change_id) is None:
        raise ToolNotFoundError(f"Değişiklik bulunamadı: {links.change_id}")
    if links.request_id and await session.get(ServiceRequest, links.request_id) is None:
        raise ToolNotFoundError(f"Hizmet talebi bulunamadı: {links.request_id}")


# --- işleyici çalıştırma -------------------------------------------------------------------------

_CONFLICTS: tuple[type[BaseException], ...] = (
    incident_service.VersionConflictError,
    changes.VersionConflictError,
    changes.ApprovalError,
    changes.ScheduleConflictError,
    cmdb.VersionConflictError,
    cmdb.DuplicateRelationshipError,
    knowledge.InvalidStateTransitionError,
)


def translate_error(exc: BaseException) -> ToolError:
    """Alan servisi istisnasını araç hatasına çevirir (MCP ve /actions aynı kodları görür)."""
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, ValidationError):
        return ToolValidationError("Argümanlar geçersiz.", errors=_errors(exc))
    if isinstance(exc, _CONFLICTS):
        return ToolConflictError(str(exc))
    # Dış sistem (bağlayıcı) hataları: desteklenmeyen işlem / yetki -> kullanılamaz
    if isinstance(exc, (ConnectorNotSupportedError, ConnectorAuthError)):
        return ToolUnavailableError(str(exc))
    if isinstance(exc, ConnectorError) and not isinstance(exc, LookupError):
        return ToolExecutionError(str(exc))
    if isinstance(exc, LookupError):
        return ToolNotFoundError(str(exc))
    if isinstance(exc, PermissionError):
        return ToolForbiddenError(str(exc))
    if isinstance(
        exc, (ValueError, changes.ChangeError, cmdb.CMDBError, incident_service.IncidentError)
    ):
        return ToolExecutionError(str(exc))
    logger.exception("Araç işleyicisi beklenmeyen hata verdi")
    return ToolExecutionError(f"Beklenmeyen hata: {type(exc).__name__}")


async def run_handler(
    spec: ToolSpec, ctx: ToolContext, args: BaseModel
) -> tuple[dict[str, Any] | None, ToolError | None]:
    """İşleyiciyi SAVEPOINT içinde çalıştırır: hata -> kısmi yazma + bildirimler geri alınır."""
    session = ctx.session
    outbox = len(notifications.pending(session))
    try:
        async with session.begin_nested():
            raw = await spec.handler(ctx, args)
            await session.flush()
    except Exception as exc:  # noqa: BLE001 — işleyici hatası çağıranı durdurmaz, sonuç "failed"
        notifications.discard_after(session, outbox)
        return None, translate_error(exc)
    return jsonable(raw if raw is not None else {}), None


# --- eylem isteği ----------------------------------------------------------------------------------


async def _next_action_number(session: AsyncSession) -> str:
    last = await session.scalar(
        select(func.max(ActionRequest.number)).where(ActionRequest.number.like("ACT%"))
    )
    n = int(last[3:]) if last and last[3:].isdigit() else 0
    return f"ACT{n + 1:07d}"


def required_approvals(risk: RiskClass) -> int:
    return 2 if RiskClass(risk) == RiskClass.R3 else 1


async def create_action_request(
    session: AsyncSession,
    *,
    spec: ToolSpec,
    user: CurrentUser,
    args: dict[str, Any],
    risk: RiskClass,
    reason: str,
    confidence: float | None,
    links: ActionLinks,
    now: datetime,
    settings: Settings,
) -> ActionRequest:
    ar = ActionRequest(
        number=await _next_action_number(session),
        tool=spec.name,
        args=jsonable(args),
        args_display=redact_args(jsonable(args)),
        args_hash=args_hash(spec.name, args),
        risk_class=risk,
        required_approvals=required_approvals(risk),
        requested_by=user.username,
        reason=reason or "",
        confidence=confidence,
        incident_id=links.incident_id,
        change_id=links.change_id,
        request_id=links.request_id,
        expires_at=now + timedelta(hours=settings.action_request_ttl_hours),
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(ar)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="action_request.create",
        entity_type="action_request",
        entity_id=ar.id,
        details={
            "number": ar.number,
            "tool": spec.name,
            "risk_class": RiskClass(risk).value,
            "required_approvals": ar.required_approvals,
            "expires_at": ar.expires_at.isoformat(),
            "incident_id": ar.incident_id,
            "change_id": ar.change_id,
            "request_id": ar.request_id,
            "args": ar.args_display,
        },
    )
    return ar


# --- yapay zekâ kararı -------------------------------------------------------------------------------


async def _ai_decision(
    session: AsyncSession,
    *,
    spec: ToolSpec,
    user: CurrentUser,
    args_display: dict[str, Any],
    risk: RiskClass,
    verdict: policy.PolicyDecision,
    confidence: float,
    reason: str,
    model: str | None,
    now: datetime,
) -> AIDecision:
    decision = AIDecision(
        agent=spec.domain,
        action=spec.verb,
        input_summary=mask_pii(json.dumps(args_display, ensure_ascii=False))[:MAX_INPUT_SUMMARY],
        output={
            "tool": spec.name,
            "risk_class": RiskClass(risk).value,
            "args": args_display,
            "policy_reason": verdict.reason,
            "applied": False,
        },
        rationale=mask_pii(reason or ""),
        sources=[],
        model=(model or DEFAULT_MODEL)[:100],
        confidence=confidence,
        outcome=verdict.outcome,
        created_at=now,
    )
    session.add(decision)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="ai_decision.tool",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={
            "tool": spec.name,
            "risk_class": RiskClass(risk).value,
            "outcome": verdict.outcome.value,
            "confidence": confidence,
            "reason": verdict.reason,
        },
    )
    return decision


# --- idempotency -------------------------------------------------------------------------------------


async def _reserve_key(
    session: AsyncSession, user: CurrentUser, key: str, tool: str, digest: str
) -> ToolInvocation | ToolResult:
    """Anahtarı ayırır; daha önce kullanıldıysa önceki sonucu döndürür ya da 409 yükseltir."""
    existing = await session.scalar(
        select(ToolInvocation).where(
            ToolInvocation.actor == user.username, ToolInvocation.idempotency_key == key
        )
    )
    if existing is None:
        row = ToolInvocation(
            actor=user.username,
            idempotency_key=key,
            tool=tool,
            args_hash=digest,
            status="in_progress",
            result={},
            created_at=datetime.now(UTC),
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            return row
        except IntegrityError:
            # Eşzamanlı aynı anahtar: diğer işlem önce yazdı
            existing = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.actor == user.username, ToolInvocation.idempotency_key == key
                )
            )
            if existing is None:  # pragma: no cover - tekil kısıt başka nedenle
                raise
    if existing.args_hash != digest or existing.tool != tool:
        raise IdempotencyConflictError(
            "Bu idempotency anahtarı farklı bir araç/argümanla kullanılmış; yeni bir anahtar verin."
        )
    if existing.status == "in_progress":
        raise ToolConflictError("Aynı idempotency anahtarlı çağrı hâlâ sürüyor.")
    return ToolResult._from_storage(tool, existing.result)


# --- ana boru hattı ----------------------------------------------------------------------------------


async def execute_tool(
    session: AsyncSession,
    actor: CurrentUser,
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    dry_run: bool = False,
    idempotency_key: str | None = None,
    reason: str = "",
    confidence: float | None = None,
    model: str | None = None,
    links: ActionLinks | dict[str, Any] | None = None,
    settings: Settings | None = None,
    llm: Any = None,
    now: datetime | None = None,
) -> ToolResult:
    """Aracı risk sınıfına göre çalıştırır ya da onaya gönderir (modül belgesine bakın).

    Ön koşul hataları (bulunamadı, yetki, doğrulama, idempotency çakışması, bağlayıcı yok)
    `ToolError` olarak yükseltilir ve hiçbir şey yazılmaz. İşleyici hatası `failed` sonucudur.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    user = as_tool_actor(actor)
    spec = registry.get(tool_name)
    _authorize(spec, user)
    check_connectors(spec)
    model_args = validate_args(spec, dict(args or {}))
    canonical = jsonable(model_args.model_dump(mode="json"))
    ctx = ToolContext(session=session, actor=user, settings=settings, now=now, llm=llm)
    base = RiskClass(spec.risk_class)
    action_links = _links(spec, canonical, links)
    if base != RiskClass.R0:
        await _check_links(session, action_links)

    if dry_run:
        risk = await _safe_risk(spec, ctx, model_args)
        return _dry_run(spec, user, canonical, risk, confidence, settings)

    reservation: ToolInvocation | None = None
    if idempotency_key and base != RiskClass.R0:
        reserved = await _reserve_key(
            session, user, idempotency_key[:200], spec.name, args_hash(spec.name, canonical)
        )
        if isinstance(reserved, ToolResult):
            return reserved
        reservation = reserved

    risk = await _safe_risk(spec, ctx, model_args)
    result = await _dispatch(
        session,
        spec=spec,
        user=user,
        ctx=ctx,
        model_args=model_args,
        canonical=canonical,
        risk=risk,
        reason=reason,
        confidence=confidence,
        model=model,
        links=action_links,
        settings=settings,
        now=now,
    )
    if reservation is not None:
        reservation.status = result.status.value
        reservation.result = jsonable(result._storage())
        reservation.action_request_id = result.action_request_id
        await session.flush()
    return result


def _dry_run(
    spec: ToolSpec,
    user: CurrentUser,
    canonical: dict[str, Any],
    risk: RiskClass,
    confidence: float | None,
    settings: Settings,
) -> ToolResult:
    data: dict[str, Any] = {
        "risk_class": risk.value,
        "required_approvals": 0 if risk in (RiskClass.R0,) else required_approvals(risk),
        "args": redact_args(canonical),
    }
    if risk == RiskClass.R0 or (risk == RiskClass.R1 and not user.is_ai):
        would = "execute"
    else:
        verdict = policy.evaluate(
            agent=spec.domain,
            action=spec.verb,
            confidence=confidence if confidence is not None else 0.0,
            risk=POLICY_RISK[risk],
            settings=settings,
        )
        data["policy_outcome"] = verdict.outcome.value
        data["policy_reason"] = verdict.reason
        if verdict.outcome == DecisionOutcome.REJECTED:
            would = "rejected"
        elif risk == RiskClass.R1 and verdict.outcome == DecisionOutcome.AUTO_APPLIED:
            would = "execute"
        else:
            would = "pending_approval"
    if would == "execute":
        data["required_approvals"] = 0
    data["would"] = would
    return ToolResult(
        ToolStatus.DRY_RUN,
        spec.name,
        risk,
        result=data,
        message="Deneme çalıştırması: hiçbir şey yazılmadı.",
    )


async def _dispatch(
    session: AsyncSession,
    *,
    spec: ToolSpec,
    user: CurrentUser,
    ctx: ToolContext,
    model_args: BaseModel,
    canonical: dict[str, Any],
    risk: RiskClass,
    reason: str,
    confidence: float | None,
    model: str | None,
    links: ActionLinks,
    settings: Settings,
    now: datetime,
) -> ToolResult:
    args_display = redact_args(canonical)

    if risk == RiskClass.R0:
        result, error = await run_handler(spec, ctx, model_args)
        if user.is_ai:
            await audit.record(
                session,
                actor=user.username,
                action="tool.read",
                entity_type="tool",
                entity_id=spec.name,
                details={"args": args_display, "ok": error is None},
            )
        return _executed_or_failed(spec, risk, result, error)

    if not user.is_ai and risk == RiskClass.R1:
        return await _run_and_audit(session, spec, user, ctx, model_args, args_display, risk)

    decision: AIDecision | None = None
    if user.is_ai:
        conf = confidence if confidence is not None else 0.0
        verdict = policy.evaluate(
            agent=spec.domain,
            action=spec.verb,
            confidence=conf,
            risk=POLICY_RISK[risk],
            settings=settings,
        )
        # R2/R3 politika ne derse desin asla otomatik çalışmaz (savunma: tablo gevşetilse bile)
        if risk != RiskClass.R1 and verdict.outcome == DecisionOutcome.AUTO_APPLIED:
            verdict = policy.PolicyDecision(
                DecisionOutcome.NEEDS_APPROVAL, f"{risk.value} araç her zaman insan onayı ister."
            )
        decision = await _ai_decision(
            session,
            spec=spec,
            user=user,
            args_display=args_display,
            risk=risk,
            verdict=verdict,
            confidence=conf,
            reason=reason,
            model=model,
            now=now,
        )
        if verdict.outcome == DecisionOutcome.REJECTED:
            return ToolResult(
                ToolStatus.REJECTED,
                spec.name,
                risk,
                decision_id=decision.id,
                message=verdict.reason,
            )
        if verdict.outcome == DecisionOutcome.AUTO_APPLIED:
            res = await _run_and_audit(session, spec, user, ctx, model_args, args_display, risk)
            decision.output = {
                **decision.output,
                "applied": res.status == ToolStatus.EXECUTED,
                "error": res.error,
            }
            res.decision_id = decision.id
            await session.flush()
            return res

    ar = await create_action_request(
        session,
        spec=spec,
        user=user,
        args=canonical,
        risk=risk,
        reason=reason,
        confidence=confidence,
        links=links,
        now=now,
        settings=settings,
    )
    if decision is not None:
        decision.action_request_id = ar.id
        decision.output = {**decision.output, "action_request_id": ar.id}
        ar.decision_id = decision.id
        await session.flush()
    approvals = ar.required_approvals
    return ToolResult(
        ToolStatus.PENDING_APPROVAL,
        spec.name,
        risk,
        action_request_id=ar.id,
        decision_id=decision.id if decision else None,
        message=(
            f"{risk.value} eylem insan onayı bekliyor ({ar.number}; gereken onay: {approvals}, "
            f"son geçerlilik: {ar.expires_at.isoformat()})."
        ),
    )


async def _run_and_audit(
    session: AsyncSession,
    spec: ToolSpec,
    user: CurrentUser,
    ctx: ToolContext,
    model_args: BaseModel,
    args_display: dict[str, Any],
    risk: RiskClass,
) -> ToolResult:
    result, error = await run_handler(spec, ctx, model_args)
    await audit.record(
        session,
        actor=user.username,
        action="tool.execute" if error is None else "tool.failed",
        entity_type="tool",
        entity_id=spec.name,
        details={
            "risk_class": risk.value,
            "args": args_display,
            **({"error": error.to_dict()} if error else {}),
        },
    )
    return _executed_or_failed(spec, risk, result, error)


def _executed_or_failed(
    spec: ToolSpec, risk: RiskClass, result: dict[str, Any] | None, error: ToolError | None
) -> ToolResult:
    if error is not None:
        return ToolResult(
            ToolStatus.FAILED, spec.name, risk, error=error.to_dict(), message=error.message
        )
    return ToolResult(ToolStatus.EXECUTED, spec.name, risk, result=result)

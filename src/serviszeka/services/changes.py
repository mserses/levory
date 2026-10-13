"""ITIL 4 Değişiklik Yönetimi (Change Enablement) + CAB servis katmanı.

Durum makinesi::

    draft -> assess -> authorize -> scheduled -> implement -> review -> closed
                  \\-> scheduled (yalnızca uygun standart değişiklik; CAB atlanır)
    implement -> failed -> review;  authorize -> rejected;  draft/assess/authorize/scheduled -> cancelled

Kurallar (Faz 2 kapısı: "riskli hiçbir eylem onaysız çalışmaz"):
- Taslaktan çıkmak için uygulama planı, geri dönüş planı ve planlanan pencere zorunludur.
- normal: `cab_min_approvals` CAB onayı ve sıfır red gerekir; tek red -> rejected.
- emergency (ECAB): change_manager rolündeki tek onay yeter; dondurma penceresi uygulanmaz;
  kapatmadan önce PIR (review_notes) zorunludur.
- standard: yalnızca etkin şablondan; risk <= şablon.max_risk ve tüm CI sınıfları izinliyse CAB
  atlanır, değilse değişiklik normal'e çevrilir (denetime yazılır).
- Talep eden ve uygulayıcı (assignee) kendi değişikliğini asla onaylayamaz.
- Zamanlama: dondurma penceresine (acil hariç) ya da aynı CI'da çakışan scheduled/implement
  değişikliğe denk gelemez -> ScheduleConflictError (çakışma listesiyle).
- Uygulama yalnızca scheduled durumundan ve planlanan pencere içinde başlar; yetkilendirme
  (onaylar / standart uygunluk) ve dondurma bu anda yeniden denetlenir.
- Yapay zekâ aktörü ("ai:" önekli kullanıcı) hiçbir durum geçişi ya da onay yapamaz; yapay zekâ
  yalnızca risk skoru önerir (`propose_ai_risk`; policy.evaluate + AIDecision + audit).
- Her geçiş sürümü artırır ve denetime yazılır; iyimser kilit expected_version ile.
- Fonksiyonlar flush eder, commit etmez.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, NoReturn

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, policy, privacy, taxonomy
from serviszeka.auth import ROLE_ADMIN, ROLE_CAB, ROLE_CHANGE_MANAGER, CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import (
    ApprovalDecision,
    ChangeState,
    ChangeType,
    CIClass,
    CIEnvironment,
    DecisionOutcome,
    RiskLevel,
)
from serviszeka.models import (
    AIDecision,
    ChangeApproval,
    ChangeFreezeWindow,
    ChangeRequest,
    ConfigurationItem,
    Incident,
    StandardChangeTemplate,
    change_affected_cis,
)
from serviszeka.services import cmdb
from serviszeka.services.change_schemas import (
    ChangeCreate,
    ChangeUpdate,
    FreezeWindowCreate,
    TemplateCreate,
    TemplateUpdate,
)
from serviszeka.services.sla import ensure_utc

St = ChangeState

ALLOWED_TRANSITIONS: Mapping[ChangeState, frozenset[ChangeState]] = {
    St.DRAFT: frozenset({St.ASSESS, St.CANCELLED}),
    St.ASSESS: frozenset({St.DRAFT, St.AUTHORIZE, St.SCHEDULED, St.CANCELLED}),
    St.AUTHORIZE: frozenset({St.SCHEDULED, St.REJECTED, St.CANCELLED}),
    St.SCHEDULED: frozenset({St.IMPLEMENT, St.CANCELLED}),
    St.IMPLEMENT: frozenset({St.REVIEW, St.FAILED}),
    St.FAILED: frozenset({St.REVIEW}),
    St.REVIEW: frozenset({St.CLOSED}),
    St.CLOSED: frozenset(),
    St.REJECTED: frozenset(),
    St.CANCELLED: frozenset(),
}
TERMINAL_STATES: frozenset[ChangeState] = frozenset({St.CLOSED, St.REJECTED, St.CANCELLED})
# Takvimde ve CI çakışmasında dikkate alınan durumlar
BLOCKING_STATES: frozenset[ChangeState] = frozenset({St.SCHEDULED, St.IMPLEMENT})
CALENDAR_EXCLUDED: frozenset[ChangeState] = frozenset({St.DRAFT, St.CANCELLED, St.REJECTED})
# Yapay zekâ risk önerisinin uygulanabildiği durumlar (yetkilendirme başlamadan önce)
AI_RISK_STATES: frozenset[ChangeState] = frozenset({St.DRAFT, St.ASSESS})

AI_AGENT = "change"
AI_ACTOR = "ai:change"
_AI_PREFIX = "ai:"

_RISK_RANK: Mapping[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}
# Risk skoru bileşenleri (0-100). Ayrıntı: docs/faz2.md
_BASE_SCORE: Mapping[ChangeType, int] = {
    ChangeType.STANDARD: 5,
    ChangeType.NORMAL: 15,
    ChangeType.EMERGENCY: 30,
}
_CRITICALITY_SCORE: Mapping[int, int] = {1: 35, 2: 25, 3: 10, 4: 0}
_NUMBER_RE = re.compile(r"^CHG(\d+)$")


# --- hatalar ------------------------------------------------------------------------


class ChangeError(Exception):
    """Değişiklik servisi hatalarının tabanı."""


class ChangeNotFoundError(ChangeError, LookupError):
    """Değişiklik, şablon, dondurma penceresi ya da karar bulunamadı."""


class ChangeValidationError(ChangeError, ValueError):
    """Geçersiz veri (bilinmeyen CI/olay/şablon, taksonomi dışı grup...)."""


class InvalidTransitionError(ChangeError, ValueError):
    """İzin verilmeyen durum geçişi ya da eksik ön koşul (plan, onay, pencere...)."""


class ChangeForbiddenError(ChangeError, PermissionError):
    """Kullanıcının bu işlem için yetkisi yok (öz onay, yapay zekâ aktörü, rol)."""


class ApprovalError(ChangeError):
    """Onay bu durumda verilemez."""


class DuplicateApprovalError(ApprovalError):
    def __init__(self) -> None:
        super().__init__("Bu değişiklik için zaten karar verdiniz.")


class StaleDecisionError(ApprovalError):
    def __init__(self) -> None:
        super().__init__(
            "Değişiklik, yapay zekâ önerisinden sonra değişti; öneri bayat. "
            "Yeniden risk değerlendirmesi gerekli."
        )


class VersionConflictError(ChangeError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Sürüm çakışması: beklenen {expected}, güncel {actual}. Kaydı yenileyip tekrar deneyin."
        )
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class ScheduleConflict:
    kind: str  # "freeze_window" | "change"
    ref_id: str
    start: datetime
    end: datetime
    description: str
    ref_number: str | None = None


class ScheduleConflictError(ChangeError):
    def __init__(self, conflicts: list[ScheduleConflict]) -> None:
        super().__init__(
            "Değişiklik bu pencereye zamanlanamaz: "
            + "; ".join(c.description for c in conflicts)
        )
        self.conflicts = conflicts


# --- yardımcılar ---------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return ensure_utc(value).isoformat()
    return value


def is_ai_actor(user: CurrentUser) -> bool:
    return user.username.startswith(_AI_PREFIX)


def _forbid_ai(user: CurrentUser) -> None:
    if is_ai_actor(user):
        raise ChangeForbiddenError(
            "Yapay zekâ değişiklik durumunu değiştiremez ve onay veremez; insan kararı gerekir."
        )


def same_identity(a: str | None, b: str | None) -> bool:
    """Kullanıcı kimliği karşılaştırması: büyük/küçük harf ve kenar boşluğu farkı aynı kişidir.

    `assignee` serbest metindir; OIDC kullanıcı adları (upn/e-posta) büyük/küçük harfe duyarsızdır.
    "Mehmet" atanmışken "mehmet" belirteciyle öz onay yapılamamalı.
    """
    if a is None or b is None:
        return False
    return a.strip().casefold() == b.strip().casefold()


def is_party(user: CurrentUser, change: ChangeRequest) -> bool:
    """Kullanıcı değişikliğin talep edeni ya da uygulayıcısı mı (öz onay yasağı)."""
    return same_identity(user.username, change.requested_by) or same_identity(
        user.username, change.assignee
    )


def is_change_manager(user: CurrentUser) -> bool:
    return user.has_any(ROLE_CHANGE_MANAGER)


def level_for_score(score: int) -> RiskLevel:
    if score < 30:
        return RiskLevel.LOW
    if score < 55:
        return RiskLevel.MEDIUM
    if score < 75:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


def _implementer(change: ChangeRequest) -> str:
    return change.assignee or change.requested_by


async def next_number(session: AsyncSession) -> str:
    """CHG + 7 haneli sıradaki numara."""
    numbers = (
        await session.scalars(
            select(ChangeRequest.number).where(ChangeRequest.number.like("CHG%"))
        )
    ).all()
    highest = max((int(m.group(1)) for n in numbers if (m := _NUMBER_RE.match(n or ""))), default=0)
    return f"CHG{highest + 1:07d}"


async def get_change(session: AsyncSession, change_id: str) -> ChangeRequest | None:
    return await session.get(ChangeRequest, change_id)


async def _require_change(session: AsyncSession, change_id: str) -> ChangeRequest:
    change = await get_change(session, change_id)
    if change is None:
        raise ChangeNotFoundError(f"Değişiklik bulunamadı: {change_id}")
    return change


async def _lock(
    session: AsyncSession, change_id: str, expected_version: int | None
) -> ChangeRequest:
    """Kaydı satır kilidiyle (PostgreSQL) okur, bayat kopyayı yeniler, sürümü doğrular."""
    change = await _require_change(session, change_id)
    db_version = await session.scalar(
        select(ChangeRequest.version).where(ChangeRequest.id == change_id).with_for_update()
    )
    if db_version != change.version:
        await session.refresh(change)
    if expected_version is not None and expected_version != change.version:
        raise VersionConflictError(expected_version, change.version)
    return change


async def _resolve_cis(session: AsyncSession, ci_ids: Iterable[str]) -> list[ConfigurationItem]:
    try:
        return await cmdb.get_cis(session, ci_ids)
    except cmdb.CINotFoundError as exc:
        raise ChangeValidationError(str(exc)) from exc


async def _check_incidents(session: AsyncSession, incident_ids: list[str]) -> list[str]:
    wanted = list(dict.fromkeys(incident_ids))
    if not wanted:
        return []
    found = set(
        (await session.scalars(select(Incident.id).where(Incident.id.in_(wanted)))).all()
    )
    missing = [i for i in wanted if i not in found]
    if missing:
        raise ChangeValidationError(f"Olay bulunamadı: {', '.join(missing)}")
    return wanted


def _check_group(group: str | None) -> None:
    if group is not None and group not in taxonomy.allowed("assignment_group"):
        raise ChangeValidationError(f"Çözüm grubu (assignment_group) sınıf listesinde yok: {group}")


# --- oluşturma / düzenleme -------------------------------------------------------------


async def create_change(
    session: AsyncSession, data: ChangeCreate, user: CurrentUser
) -> ChangeRequest:
    template: StandardChangeTemplate | None = None
    if data.change_type == ChangeType.STANDARD:
        if not data.template_id:
            raise ChangeValidationError("Standart değişiklik bir şablondan (template_id) açılmalı.")
        template = await session.get(StandardChangeTemplate, data.template_id)
        if template is None or not template.active:
            raise ChangeValidationError("Standart değişiklik şablonu bulunamadı ya da etkin değil.")
    elif data.template_id:
        raise ChangeValidationError("Şablon yalnızca standart değişiklikte kullanılır.")
    _check_group(data.assignment_group)
    cis = await _resolve_cis(session, data.affected_ci_ids)
    incidents = await _check_incidents(session, data.linked_incident_ids)

    def plan(value: str, default: str) -> str:
        return value if value.strip() else default

    now = _now()
    change = ChangeRequest(
        number=await next_number(session),
        title=data.title,
        description=data.description,
        change_type=data.change_type,
        state=St.DRAFT,
        implementation_plan=plan(data.implementation_plan, template.implementation_plan)
        if template
        else data.implementation_plan,
        backout_plan=plan(data.backout_plan, template.backout_plan)
        if template
        else data.backout_plan,
        test_plan=plan(data.test_plan, template.test_plan) if template else data.test_plan,
        planned_start=data.planned_start,
        planned_end=data.planned_end,
        requested_by=user.username,
        assignment_group=data.assignment_group,
        assignee=data.assignee,
        template_id=template.id if template else None,
        linked_incident_ids=incidents,
        version=1,
        created_at=now,
        updated_at=now,
    )
    change.affected_cis = cis
    session.add(change)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="change.create",
        entity_type="change",
        entity_id=change.id,
        details={
            "number": change.number,
            "change_type": change.change_type.value,
            "template_id": change.template_id,
            "affected_ci_ids": [c.id for c in cis],
            "version": change.version,
        },
    )
    return change


async def list_changes(
    session: AsyncSession,
    *,
    state: ChangeState | str | None = None,
    change_type: ChangeType | str | None = None,
    ci_id: str | None = None,
    requested_by: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ChangeRequest]:
    stmt = select(ChangeRequest)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(ChangeRequest.number.ilike(pattern), ChangeRequest.title.ilike(pattern))
        )
    if state is not None:
        stmt = stmt.where(ChangeRequest.state == ChangeState(state))
    if change_type is not None:
        stmt = stmt.where(ChangeRequest.change_type == ChangeType(change_type))
    if requested_by is not None:
        stmt = stmt.where(ChangeRequest.requested_by == requested_by)
    if ci_id is not None:
        stmt = stmt.join(
            change_affected_cis, change_affected_cis.c.change_id == ChangeRequest.id
        ).where(change_affected_cis.c.ci_id == ci_id)
    stmt = stmt.order_by(ChangeRequest.created_at.desc(), ChangeRequest.number.desc())
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


_UPDATE_NON_NULLABLE = frozenset(
    {"title", "description", "implementation_plan", "backout_plan", "test_plan"}
)


async def update_change(
    session: AsyncSession,
    change_id: str,
    data: ChangeUpdate,
    user: CurrentUser,
    expected_version: int | None = None,
) -> ChangeRequest:
    """Taslak düzenleme (talep eden ya da change_manager). Taslak dışında değişiklik yapılamaz."""
    change = await _lock(session, change_id, expected_version)
    _forbid_ai(user)
    if not same_identity(user.username, change.requested_by) and not is_change_manager(user):
        raise ChangeForbiddenError("Bu değişikliği yalnızca talep eden ya da change_manager düzenler.")
    if change.state != St.DRAFT:
        raise InvalidTransitionError(
            f"Yalnızca taslak değişiklik düzenlenebilir (durum: {ChangeState(change.state).value})."
        )
    values = data.model_dump(exclude_unset=True)
    values.pop("expected_version", None)
    values = {k: v for k, v in values.items() if not (v is None and k in _UPDATE_NON_NULLABLE)}
    start = values.get("planned_start", change.planned_start)
    end = values.get("planned_end", change.planned_end)
    if start is not None and end is not None and ensure_utc(end) <= ensure_utc(start):
        raise ChangeValidationError("Planlanan bitiş, başlangıçtan sonra olmalıdır.")
    if "assignment_group" in values:
        _check_group(values["assignment_group"])

    diff: dict[str, list[Any]] = {}
    if "affected_ci_ids" in values:
        ci_ids = values.pop("affected_ci_ids") or []
        cis = await _resolve_cis(session, ci_ids)
        old_ids = change.affected_ci_ids
        if [c.id for c in cis] != old_ids:
            diff["affected_ci_ids"] = [old_ids, [c.id for c in cis]]
            change.affected_cis = cis
    if "linked_incident_ids" in values:
        values["linked_incident_ids"] = await _check_incidents(
            session, values["linked_incident_ids"] or []
        )
    for name, value in values.items():
        old = getattr(change, name)
        if (ensure_utc(old) if isinstance(old, datetime) else old) != value:
            diff[name] = [_jsonable(old), _jsonable(value)]
            setattr(change, name, value)
    if not diff:
        return change
    change.version += 1
    change.updated_at = _now()
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="change.update",
        entity_type="change",
        entity_id=change.id,
        details={"changes": diff, "version": change.version},
    )
    return change


# --- risk ------------------------------------------------------------------------------


@dataclass
class RiskAssessment:
    score: int
    level: RiskLevel
    highest_criticality: int | None
    affected_count: int
    business_services: list[str]
    factors: dict[str, int] = field(default_factory=dict)


async def _assess(session: AsyncSession, change: ChangeRequest) -> RiskAssessment:
    factors: dict[str, int] = {"change_type": _BASE_SCORE[ChangeType(change.change_type)]}
    roots = change.affected_cis
    highest: int | None = None
    affected_count = 0
    services: list[str] = []
    if roots:
        impact = await cmdb.impact_analysis(session, [c.id for c in roots])
        highest = impact.highest_criticality
        affected_count = len(impact.affected)
        services = [s.name for s in impact.business_services]
        if impact.truncated:
            # Analiz sınırda kesildi: görülmeyen CI'lar en kritik kabul edilir (temkinli)
            highest = 1
        factors["criticality"] = _CRITICALITY_SCORE.get(highest or 4, 0)
        if any(c.environment == CIEnvironment.PROD for c in roots):
            factors["production"] = 10
        if affected_count:
            factors["blast_radius"] = 15 if impact.truncated else min(15, 3 * affected_count)
        if services:
            factors["business_service"] = 5
    if not (change.test_plan or "").strip():
        factors["no_test_plan"] = 5
    if not (change.backout_plan or "").strip():
        factors["no_backout_plan"] = 10
    score = max(0, min(100, sum(factors.values())))
    return RiskAssessment(score, level_for_score(score), highest, affected_count, services, factors)


async def compute_risk(session: AsyncSession, change_id: str) -> RiskAssessment:
    """Deterministik risk: değişiklik türü + CMDB etki analizi (kritiklik, prod, etki alanı)."""
    return await _assess(session, await _require_change(session, change_id))


def _standard_ineligibility(
    change: ChangeRequest, risk_level: RiskLevel | None = None
) -> list[str]:
    """Standart değişikliğin CAB'ı atlayamama nedenleri (boş liste = önceden onaylı).

    `risk_level` verilirse (zamanlama/uygulama anında CMDB'den taze hesaplanan) saklı riskin
    yerine o kullanılır.
    """
    template = change.template
    if template is None or not template.active:
        return ["Standart değişiklik şablonu etkin değil."]
    reasons: list[str] = []
    level = risk_level if risk_level is not None else change.risk_level
    if level is None:
        reasons.append("Risk değerlendirilmedi.")
    elif _RISK_RANK[RiskLevel(level)] > _RISK_RANK[RiskLevel(template.max_risk)]:
        reasons.append(
            f"Risk ({RiskLevel(level).value}) şablon sınırını "
            f"({RiskLevel(template.max_risk).value}) aşıyor."
        )
    if not change.affected_cis:
        # Şablon CI sınıflarıyla sınırlıdır; CI'sız değişiklik kapsamı belirsizdir -> CAB
        reasons.append("Standart değişiklik en az bir yapılandırma öğesi (CI) içermelidir.")
    allowed = set(template.allowed_ci_classes or [])
    bad = sorted({CIClass(c.ci_class).value for c in change.affected_cis} - allowed)
    if bad:
        reasons.append(f"Şablon bu CI sınıflarına izin vermiyor: {', '.join(bad)}.")
    return reasons


async def _enforce_standard(session: AsyncSession, change: ChangeRequest, actor: str) -> None:
    """Uygunluğunu yitiren standart değişikliği normal'e çevirir (CAB zorunlu olur)."""
    if change.change_type != ChangeType.STANDARD:
        return
    reasons = _standard_ineligibility(change)
    if not reasons:
        return
    change.change_type = ChangeType.NORMAL
    await audit.record(
        session,
        actor=actor,
        action="change.converted_to_normal",
        entity_type="change",
        entity_id=change.id,
        details={"number": change.number, "reasons": reasons},
    )


async def _set_risk(
    session: AsyncSession,
    change: ChangeRequest,
    score: int,
    actor: str,
    source: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    old = [change.risk_score, _jsonable(change.risk_level)]
    change.risk_score = score
    change.risk_level = level_for_score(score)
    await audit.record(
        session,
        actor=actor,
        action="change.risk_update",
        entity_type="change",
        entity_id=change.id,
        details={
            "source": source,
            "risk": [old, [score, change.risk_level.value]],
            **(details or {}),
        },
    )
    await _enforce_standard(session, change, actor)


# --- onaylar ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalStatus:
    required: int
    approvals: int
    rejections: int
    satisfied: bool


async def list_approvals(session: AsyncSession, change_id: str) -> list[ChangeApproval]:
    stmt = (
        select(ChangeApproval)
        .where(ChangeApproval.change_id == change_id)
        .order_by(ChangeApproval.created_at, ChangeApproval.id)
    )
    return list((await session.scalars(stmt)).all())


def _status(
    change: ChangeRequest, approvals: list[ChangeApproval], settings: Settings
) -> ApprovalStatus:
    # Savunma: talep eden/uygulayıcı kaydı sayılmaz (kayıt zaten engellenir)
    valid = [
        a
        for a in approvals
        if not same_identity(a.approver, change.requested_by)
        and not same_identity(a.approver, change.assignee)
        and not a.approver.startswith(_AI_PREFIX)
    ]
    rejections = sum(1 for a in valid if a.decision == ApprovalDecision.REJECT)
    approves = [a for a in valid if a.decision == ApprovalDecision.APPROVE]
    change_type = ChangeType(change.change_type)

    def distinct(items: list[ChangeApproval]) -> int:
        # Aynı kişi (büyük/küçük harf/boşluk farkı) bir kez sayılır
        return len({a.approver.strip().casefold() for a in items})

    if change_type == ChangeType.STANDARD:
        return ApprovalStatus(0, distinct(approves), rejections, rejections == 0)
    if change_type == ChangeType.EMERGENCY:
        required = 1
        counted = distinct(
            [a for a in approves if {ROLE_CHANGE_MANAGER, ROLE_ADMIN} & set(a.approver_roles or [])]
        )
    else:
        required = settings.cab_min_approvals
        counted = distinct(approves)
    return ApprovalStatus(required, counted, rejections, counted >= required and rejections == 0)


async def approval_status(
    session: AsyncSession, change_id: str, *, settings: Settings | None = None
) -> ApprovalStatus:
    change = await _require_change(session, change_id)
    return _status(change, await list_approvals(session, change_id), settings or get_settings())


async def record_approval(
    session: AsyncSession,
    change_id: str,
    user: CurrentUser,
    decision: ApprovalDecision | str,
    comment: str = "",
    *,
    settings: Settings | None = None,
) -> ChangeApproval:
    """CAB/ECAB kararı. Tek red değişikliği `rejected` yapar; onaylar geçişi kendisi yapmaz."""
    decision = ApprovalDecision(decision)
    change = await _lock(session, change_id, None)
    _forbid_ai(user)
    if not user.has_any(ROLE_CAB, ROLE_CHANGE_MANAGER):
        raise ChangeForbiddenError("Onay için cab ya da change_manager rolü gerekir.")
    if is_party(user, change):
        raise ChangeForbiddenError(
            "Talep eden ya da uygulayıcı kendi değişikliği hakkında karar veremez."
        )
    if change.state != St.AUTHORIZE:
        raise ApprovalError(
            f"Onay yalnızca yetkilendirme aşamasında verilebilir (durum: "
            f"{ChangeState(change.state).value})."
        )
    voters = (
        await session.scalars(
            select(ChangeApproval.approver).where(ChangeApproval.change_id == change.id)
        )
    ).all()
    if any(same_identity(v, user.username) for v in voters):
        raise DuplicateApprovalError()
    approval = ChangeApproval(
        change_id=change.id,
        approver=user.username,
        approver_roles=sorted(user.roles),
        decision=decision,
        comment=comment,
        created_at=_now(),
    )
    # Eşzamanlı aynı onaylayan: varlık denetimini ikisi de geçebilir; tekil kısıt
    # (change_id, approver) ikinciyi durdurur -> 500 yerine 409 (önceki yazmalar korunur)
    try:
        async with session.begin_nested():
            session.add(approval)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateApprovalError() from exc
    await audit.record(
        session,
        actor=user.username,
        action="change.approval",
        entity_type="change",
        entity_id=change.id,
        details={
            "number": change.number,
            "decision": decision.value,
            "approver_roles": approval.approver_roles,
            "comment": comment,
        },
    )
    if decision == ApprovalDecision.REJECT:
        await _move(session, change, St.REJECTED, user.username, {"reason": "cab_reject"})
    return approval


# --- takvim / çakışmalar ---------------------------------------------------------------


def _envs(change: ChangeRequest) -> set[str]:
    return {CIEnvironment(c.environment).value for c in change.affected_cis}


async def _lock_cis(session: AsyncSession, change: ChangeRequest) -> None:
    """Etkilenen CI satırlarını (id sırasıyla, kilitlenme olmasın diye) FOR UPDATE kilitler.

    Aynı CI'a eşzamanlı iki zamanlama/uygulama, çakışma denetimini birbirini görmeden geçemez:
    ikinci işlem birincinin commit'ini bekler, sonra onu scheduled olarak görür (PostgreSQL;
    SQLite yazmaları zaten tek yazıcıyla sıralar).
    """
    ci_ids = sorted(change.affected_ci_ids)
    if not ci_ids:
        return
    await session.execute(
        select(ConfigurationItem.id)
        .where(ConfigurationItem.id.in_(ci_ids))
        .order_by(ConfigurationItem.id)
        .with_for_update()
    )


async def _conflicts(
    session: AsyncSession, change: ChangeRequest, *, include_freeze: bool = True
) -> list[ScheduleConflict]:
    if change.planned_start is None or change.planned_end is None:
        return []
    start, end = ensure_utc(change.planned_start), ensure_utc(change.planned_end)
    found: list[ScheduleConflict] = []
    if include_freeze and change.change_type != ChangeType.EMERGENCY:
        envs = _envs(change)
        stmt = (
            select(ChangeFreezeWindow)
            .where(ChangeFreezeWindow.start_at < end, ChangeFreezeWindow.end_at > start)
            .order_by(ChangeFreezeWindow.start_at)
        )
        for fw in (await session.scalars(stmt)).all():
            fw_envs = set(fw.environments or [])
            # Ortam belirtilmemiş pencere ya da CI'sız değişiklik: her yerde geçerli (temkinli)
            if fw_envs and envs and not (fw_envs & envs):
                continue
            found.append(
                ScheduleConflict(
                    kind="freeze_window",
                    ref_id=fw.id,
                    start=ensure_utc(fw.start_at),
                    end=ensure_utc(fw.end_at),
                    description=f"Dondurma penceresi: {fw.reason or 'neden belirtilmemiş'}",
                )
            )
    ci_ids = change.affected_ci_ids
    if ci_ids:
        stmt = (
            select(ChangeRequest)
            .join(change_affected_cis, change_affected_cis.c.change_id == ChangeRequest.id)
            .where(
                change_affected_cis.c.ci_id.in_(ci_ids),
                ChangeRequest.id != change.id,
                ChangeRequest.state.in_(list(BLOCKING_STATES)),
                ChangeRequest.planned_start < end,
                ChangeRequest.planned_end > start,
            )
            .distinct()
            .order_by(ChangeRequest.planned_start)
        )
        mine = set(ci_ids)
        for other in (await session.scalars(stmt)).all():
            shared = sorted(c.name for c in other.affected_cis if c.id in mine)
            found.append(
                ScheduleConflict(
                    kind="change",
                    ref_id=other.id,
                    ref_number=other.number,
                    start=ensure_utc(other.planned_start),
                    end=ensure_utc(other.planned_end),
                    description=(
                        f"{other.number} ({other.title}) aynı CI üzerinde çakışan zamanda "
                        f"planlı: {', '.join(shared)}"
                    ),
                )
            )
    return found


async def find_conflicts(session: AsyncSession, change_id: str) -> list[ScheduleConflict]:
    """Değişikliğin planlanan penceresindeki dondurma ve aynı-CI çakışmaları."""
    return await _conflicts(session, await _require_change(session, change_id))


@dataclass
class Calendar:
    changes: list[ChangeRequest]
    freeze_windows: list[ChangeFreezeWindow]


async def calendar(session: AsyncSession, start: datetime, end: datetime) -> Calendar:
    """[start, end) aralığına değen değişiklikler (taslak/iptal/red hariç) ve dondurma pencereleri."""
    start, end = ensure_utc(start), ensure_utc(end)
    if end <= start:
        raise ChangeValidationError("Takvim aralığında bitiş başlangıçtan sonra olmalıdır.")
    changes_stmt = (
        select(ChangeRequest)
        .where(
            ChangeRequest.planned_start < end,
            ChangeRequest.planned_end > start,
            ChangeRequest.state.not_in(list(CALENDAR_EXCLUDED)),
        )
        .order_by(ChangeRequest.planned_start, ChangeRequest.number)
    )
    return Calendar(
        changes=list((await session.scalars(changes_stmt)).all()),
        freeze_windows=await list_freeze_windows(session, start=start, end=end),
    )


# --- durum geçişleri -------------------------------------------------------------------


async def _move(
    session: AsyncSession,
    change: ChangeRequest,
    to_state: ChangeState,
    actor: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    old = ChangeState(change.state)
    change.state = to_state
    change.version += 1
    change.updated_at = _now()
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="change.transition",
        entity_type="change",
        entity_id=change.id,
        details={
            "number": change.number,
            "from": old.value,
            "to": to_state.value,
            "change_type": ChangeType(change.change_type).value,
            "version": change.version,
            **(details or {}),
        },
    )


def _check_path(change: ChangeRequest, current: ChangeState, to_state: ChangeState) -> None:
    """Türe özgü yol: standart CAB'a gitmez; normal/acil CAB/ECAB'ı atlayamaz."""
    standard = change.change_type == ChangeType.STANDARD
    if to_state == St.AUTHORIZE and standard:
        raise InvalidTransitionError(
            "Standart değişiklik önceden onaylıdır; CAB'a gönderilmez, doğrudan zamanlanır."
        )
    if current == St.ASSESS and to_state == St.SCHEDULED and not standard:
        raise InvalidTransitionError(
            "Normal/acil değişiklik CAB/ECAB yetkilendirmesi olmadan zamanlanamaz."
        )


def _check_role(change: ChangeRequest, to_state: ChangeState, user: CurrentUser) -> None:
    manager = is_change_manager(user)
    current = ChangeState(change.state)
    if to_state in (St.ASSESS, St.DRAFT) or (
        to_state == St.CANCELLED and current in (St.DRAFT, St.ASSESS)
    ):
        allowed = manager or same_identity(user.username, change.requested_by)
    elif to_state == St.SCHEDULED and change.change_type == ChangeType.STANDARD:
        # Önceden onaylı standart değişikliği talep eden de zamanlayabilir
        allowed = manager or same_identity(user.username, change.requested_by)
    elif to_state in (St.IMPLEMENT, St.FAILED, St.REVIEW):
        allowed = manager or same_identity(user.username, _implementer(change))
    else:  # authorize, scheduled, rejected, closed, sonraki aşamada iptal
        allowed = manager
    if not allowed:
        raise ChangeForbiddenError(
            f"Bu geçiş ({current.value} -> {to_state.value}) için yetkiniz yok."
        )


def _require_plans(change: ChangeRequest) -> None:
    missing = [
        label
        for label, ok in (
            ("uygulama planı", (change.implementation_plan or "").strip()),
            ("geri dönüş planı", (change.backout_plan or "").strip()),
            ("planlanan başlangıç", change.planned_start),
            ("planlanan bitiş", change.planned_end),
        )
        if not ok
    ]
    if missing:
        raise InvalidTransitionError(
            "Değişiklik taslaktan çıkamaz; eksik: " + ", ".join(missing) + "."
        )


async def _assert_authorized(
    session: AsyncSession, change: ChangeRequest, settings: Settings
) -> None:
    """Uygulama/zamanlama öncesi yetki kanıtı: standart uygunluk ya da yeterli onay.

    Standart değişiklikte risk CMDB'den yeniden hesaplanır: gönderimden sonra CI kritikliği,
    ortamı ya da bağımlıları ağırlaştıysa saklı (eski) düşük risk CAB'ı atlatamaz.
    """
    approvals = await list_approvals(session, change.id)
    if change.change_type == ChangeType.STANDARD:
        fresh = await _assess(session, change)
        score = max(fresh.score, change.risk_score or 0)
        reasons = _standard_ineligibility(change, level_for_score(score))
        if reasons:
            raise InvalidTransitionError(
                "Standart değişiklik önceden onaylı sayılamaz: " + " ".join(reasons)
            )
        return
    status = _status(change, approvals, settings)
    if not status.satisfied:
        kind = "ECAB (change_manager)" if change.change_type == ChangeType.EMERGENCY else "CAB"
        raise InvalidTransitionError(
            f"Yetkilendirme eksik: {kind} onayı {status.approvals}/{status.required}"
            + (f", red {status.rejections}" if status.rejections else "")
            + "."
        )


async def transition(
    session: AsyncSession,
    change_id: str,
    to_state: ChangeState | str,
    user: CurrentUser,
    *,
    expected_version: int | None = None,
    notes: str | None = None,
    review_notes: str | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> ChangeRequest:
    to_state = ChangeState(to_state)
    settings = settings or get_settings()
    now = ensure_utc(now) if now is not None else _now()
    change = await _lock(session, change_id, expected_version)
    _forbid_ai(user)
    current = ChangeState(change.state)
    if to_state not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Durum geçişi geçersiz: {current.value} -> {to_state.value}")
    _check_path(change, current, to_state)
    _check_role(change, to_state, user)
    details: dict[str, Any] = {}
    if notes:
        details["notes"] = notes

    if current == St.DRAFT and to_state == St.ASSESS:
        _require_plans(change)
        assessment = await _assess(session, change)
        # Daha önce (ör. yapay zekâ önerisiyle) yükseltilmiş risk düşürülmez
        score = max(assessment.score, change.risk_score or 0)
        details["risk"] = {"score": score, "factors": assessment.factors}
        change.risk_score = score
        change.risk_level = level_for_score(score)
        await _enforce_standard(session, change, user.username)
    elif to_state == St.SCHEDULED:
        _require_plans(change)
        await _assert_authorized(session, change, settings)
        await _lock_cis(session, change)
        conflicts = await _conflicts(session, change)
        if conflicts:
            raise ScheduleConflictError(conflicts)
    elif to_state == St.IMPLEMENT:
        await _assert_authorized(session, change, settings)
        _require_plans(change)
        start, end = ensure_utc(change.planned_start), ensure_utc(change.planned_end)
        if not start <= now < end:
            raise InvalidTransitionError(
                "Uygulama yalnızca planlanan pencere içinde başlatılabilir "
                f"({start.isoformat()} – {end.isoformat()})."
            )
        await _lock_cis(session, change)
        conflicts = await _conflicts(session, change)
        if conflicts:
            raise ScheduleConflictError(conflicts)
        change.actual_start = now
    elif current == St.IMPLEMENT:  # -> review / failed
        change.actual_end = now
        change.implementation_outcome = "failed" if to_state == St.FAILED else "succeeded"
    elif to_state == St.CLOSED:
        pir_required = (
            change.change_type == ChangeType.EMERGENCY or change.implementation_outcome == "failed"
        )
        text = (review_notes or "").strip() or None
        if pir_required and not text:
            raise InvalidTransitionError(
                "Acil ya da başarısız değişiklik, uygulama sonrası gözden geçirme (PIR) notu "
                "(review_notes) olmadan kapatılamaz."
            )
        if text:
            change.review_notes = text
        change.closed_at = now

    await _move(session, change, to_state, user.username, details)
    return change


# --- yapay zekâ risk önerisi -------------------------------------------------------------


async def propose_ai_risk(
    session: AsyncSession,
    change_id: str,
    *,
    risk_score: int,
    confidence: float,
    rationale: str,
    model: str,
    settings: Settings | None = None,
) -> AIDecision:
    """Yapay zekâ risk önerisi: policy.evaluate -> AIDecision -> audit.

    Yalnızca risk skoru/seviyesi uygulanabilir, durum asla değişmez. Riski yükselten öneri düşük
    risklidir (gölge mod kapalı ve güven yeterliyse otomatik uygulanır); riski düşüren öneri
    yüksek risklidir ve change_manager onayı ister (`approve_ai_decision`).
    """
    settings = settings or get_settings()
    change = await _lock(session, change_id, None)
    if change.state not in AI_RISK_STATES:
        raise InvalidTransitionError(
            "Yapay zekâ risk önerisi yalnızca taslak/değerlendirme aşamasında yapılabilir."
        )
    if not 0 <= risk_score <= 100:
        raise ChangeValidationError("Risk skoru 0 ile 100 arasında olmalıdır.")
    current = change.risk_score
    if current is None:
        current = (await _assess(session, change)).score
    action_risk = RiskLevel.LOW if risk_score >= current else RiskLevel.HIGH
    verdict = policy.evaluate(
        agent=AI_AGENT,
        action="assess_risk",
        confidence=confidence,
        risk=action_risk,
        settings=settings,
    )
    proposed = {"risk_score": risk_score, "risk_level": level_for_score(risk_score).value}
    decision = AIDecision(
        change_id=change.id,
        agent=AI_AGENT,
        action="assess_risk",
        input_summary=privacy.mask_pii(f"{change.number}: {change.title}")[:2000],
        output={
            "proposed": proposed,
            "current_risk_score": current,
            "change_version": change.version,
            "policy_reason": verdict.reason,
        },
        rationale=rationale,
        sources=[],
        model=model,
        confidence=confidence,
        outcome=verdict.outcome,
        created_at=_now(),
    )
    session.add(decision)
    await session.flush()
    await audit.record(
        session,
        actor=AI_ACTOR,
        action="ai_decision.change_risk",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={
            "change_id": change.id,
            "change_number": change.number,
            "outcome": verdict.outcome.value,
            "confidence": confidence,
            "model": model,
            "proposed": proposed,
            "action_risk": action_risk.value,
            "reason": verdict.reason,
        },
    )
    if verdict.outcome == DecisionOutcome.AUTO_APPLIED:
        await _set_risk(session, change, risk_score, AI_ACTOR, "ai_auto", {"decision_id": decision.id})
        change.version += 1
        change.updated_at = _now()
        decision.output = {**decision.output, "applied": True}
        await session.flush()
    return decision


async def get_decision(session: AsyncSession, decision_id: str) -> AIDecision | None:
    decision = await session.get(AIDecision, decision_id)
    return decision if decision is not None and decision.change_id is not None else None


async def list_decisions(session: AsyncSession, change_id: str) -> list[AIDecision]:
    stmt = (
        select(AIDecision)
        .where(AIDecision.change_id == change_id)
        .order_by(AIDecision.created_at.desc(), AIDecision.id.desc())
    )
    return list((await session.scalars(stmt)).all())


_APPROVABLE = frozenset({DecisionOutcome.NEEDS_APPROVAL, DecisionOutcome.SUGGESTED})


async def approve_ai_decision(
    session: AsyncSession, decision_id: str, user: CurrentUser
) -> AIDecision:
    """change_manager, yapay zekâ risk önerisini onaylar; yalnızca risk alanları uygulanır."""
    decision = await get_decision(session, decision_id)
    if decision is None:
        raise ChangeNotFoundError(f"Karar bulunamadı: {decision_id}")
    _forbid_ai(user)
    if not is_change_manager(user):
        raise ChangeForbiddenError("Değişiklik kararlarını yalnızca change_manager onaylar.")
    if decision.outcome not in _APPROVABLE:
        raise ApprovalError(
            f"Bu karar onaylanamaz (sonuç: {DecisionOutcome(decision.outcome).value})."
        )
    if decision.approved_by:
        raise ApprovalError(f"Karar zaten {decision.approved_by} tarafından onaylanmış.")
    change = await _lock(session, decision.change_id, None)
    if is_party(user, change):
        raise ChangeForbiddenError("Kendi değişikliğinizin risk önerisini onaylayamazsınız.")
    output = dict(decision.output or {})
    if change.state not in AI_RISK_STATES or change.version != output.get("change_version"):
        await _reject_stale(session, decision, change, user.username)
    score = int(output["proposed"]["risk_score"])
    await _set_risk(session, change, score, user.username, "ai_approval", {"decision_id": decision.id})
    change.version += 1
    change.updated_at = _now()
    decision.approved_by = user.username
    decision.approved_at = _now()
    decision.output = {**output, "applied": True}
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="ai_decision.approve",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={"change_id": change.id, "applied": output["proposed"]},
    )
    return decision


async def _reject_stale(
    session: AsyncSession, decision: AIDecision, change: ChangeRequest, approver: str
) -> NoReturn:
    await audit.record(
        session,
        actor=approver,
        action="ai_decision.approve_rejected",
        entity_type="ai_decision",
        entity_id=decision.id,
        details={
            "change_id": change.id,
            "reason": "stale_decision",
            "decision_version": (decision.output or {}).get("change_version"),
            "change_version": change.version,
            "change_state": ChangeState(change.state).value,
        },
    )
    raise StaleDecisionError()


# --- şablonlar ---------------------------------------------------------------------------


async def get_template(session: AsyncSession, template_id: str) -> StandardChangeTemplate:
    template = await session.get(StandardChangeTemplate, template_id)
    if template is None:
        raise ChangeNotFoundError(f"Şablon bulunamadı: {template_id}")
    return template


async def _check_template_name(
    session: AsyncSession, name: str, exclude_id: str | None = None
) -> None:
    stmt = select(StandardChangeTemplate.id).where(StandardChangeTemplate.name == name)
    if exclude_id:
        stmt = stmt.where(StandardChangeTemplate.id != exclude_id)
    if await session.scalar(stmt.limit(1)) is not None:
        raise ChangeValidationError(f"'{name}' adlı şablon zaten var.")


async def create_template(
    session: AsyncSession, data: TemplateCreate, user: CurrentUser
) -> StandardChangeTemplate:
    await _check_template_name(session, data.name)
    now = _now()
    fields = data.model_dump(mode="json")
    template = StandardChangeTemplate(
        **{**fields, "max_risk": data.max_risk}, created_by=user.username, created_at=now,
        updated_at=now,
    )
    session.add(template)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="change.template.create",
        entity_type="change_template",
        entity_id=template.id,
        details=fields,
    )
    return template


async def list_templates(
    session: AsyncSession, *, active: bool | None = None
) -> list[StandardChangeTemplate]:
    stmt = select(StandardChangeTemplate).order_by(StandardChangeTemplate.name)
    if active is not None:
        stmt = stmt.where(StandardChangeTemplate.active == active)
    return list((await session.scalars(stmt)).all())


async def update_template(
    session: AsyncSession, template_id: str, data: TemplateUpdate, user: CurrentUser
) -> StandardChangeTemplate:
    template = await get_template(session, template_id)
    values = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if "name" in values:
        await _check_template_name(session, values["name"], exclude_id=template.id)
    if "allowed_ci_classes" in values:
        values["allowed_ci_classes"] = [CIClass(c).value for c in values["allowed_ci_classes"]]
    diff: dict[str, list[Any]] = {}
    for name, value in values.items():
        old = getattr(template, name)
        if old != value:
            diff[name] = [_jsonable(old), _jsonable(value)]
            setattr(template, name, value)
    if diff:
        template.updated_at = _now()
        await session.flush()
        await audit.record(
            session,
            actor=user.username,
            action="change.template.update",
            entity_type="change_template",
            entity_id=template.id,
            details={"changes": diff},
        )
    return template


# --- dondurma pencereleri ----------------------------------------------------------------


async def create_freeze_window(
    session: AsyncSession, data: FreezeWindowCreate, user: CurrentUser
) -> ChangeFreezeWindow:
    fw = ChangeFreezeWindow(
        start_at=data.start_at,
        end_at=data.end_at,
        reason=data.reason,
        environments=[CIEnvironment(e).value for e in data.environments],
        created_by=user.username,
        created_at=_now(),
    )
    session.add(fw)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="change.freeze_window.create",
        entity_type="change_freeze_window",
        entity_id=fw.id,
        details=data.model_dump(mode="json"),
    )
    return fw


async def list_freeze_windows(
    session: AsyncSession, *, start: datetime | None = None, end: datetime | None = None
) -> list[ChangeFreezeWindow]:
    stmt = select(ChangeFreezeWindow).order_by(ChangeFreezeWindow.start_at)
    if end is not None:
        stmt = stmt.where(ChangeFreezeWindow.start_at < ensure_utc(end))
    if start is not None:
        stmt = stmt.where(ChangeFreezeWindow.end_at > ensure_utc(start))
    return list((await session.scalars(stmt)).all())


async def delete_freeze_window(session: AsyncSession, window_id: str, user: CurrentUser) -> None:
    fw = await session.get(ChangeFreezeWindow, window_id)
    if fw is None:
        raise ChangeNotFoundError(f"Dondurma penceresi bulunamadı: {window_id}")
    details = {
        "start_at": _jsonable(fw.start_at),
        "end_at": _jsonable(fw.end_at),
        "reason": fw.reason,
        "environments": fw.environments,
    }
    await session.delete(fw)
    await session.flush()
    await audit.record(
        session,
        actor=user.username,
        action="change.freeze_window.delete",
        entity_type="change_freeze_window",
        entity_id=window_id,
        details=details,
    )

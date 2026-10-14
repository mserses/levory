"""Değişiklik Yönetimi (Change Enablement) + CAB: durum makinesi, onaylar, takvim, politika.

Faz 2 kapısı: "Hiçbir riskli eylem onaysız çalışmıyor". `test_gate_*` testleri bunun kanıtıdır.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _auth import AGENT, dev_headers
from pydantic import ValidationError
from sqlalchemy import func, select

from serviszeka import audit, db
from serviszeka.api import changes as changes_api
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import (
    ApprovalDecision,
    ChangeState,
    ChangeType,
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    DecisionOutcome,
    RiskLevel,
)
from serviszeka.main import app
from serviszeka.models import AIDecision, AuditLog, ChangeRequest
from serviszeka.policy import POLICIES, evaluate
from serviszeka.services import changes, cmdb
from serviszeka.services.change_schemas import (
    ChangeCreate,
    ChangeUpdate,
    FreezeWindowCreate,
    TemplateCreate,
    TemplateUpdate,
)
from serviszeka.services.cmdb_schemas import CICreate

St = ChangeState
A = ApprovalDecision

REQ = CurrentUser("ayse", frozenset({"agent"}))
IMPL = CurrentUser("mehmet", frozenset({"agent"}))
MGR = CurrentUser("mudur", frozenset({"change_manager"}))
MGR2 = CurrentUser("mudur2", frozenset({"change_manager"}))
CAB1 = CurrentUser("kurul1", frozenset({"cab"}))
CAB2 = CurrentUser("kurul2", frozenset({"cab"}))
CAB3 = CurrentUser("kurul3", frozenset({"cab"}))
ADMIN = CurrentUser("yonetici", frozenset({"admin"}))
AI_ACTOR = CurrentUser("ai:change", frozenset({"admin"}))

S = Settings(cab_min_approvals=2, ai_shadow_mode=True)
LIVE = Settings(cab_min_approvals=2, ai_shadow_mode=False, ai_confidence_threshold=0.75)

T0 = datetime(2030, 1, 10, 22, 0, tzinfo=UTC)
INSIDE = T0 + timedelta(minutes=30)


async def _ci(session, name, ci_class=CIClass.SERVER, criticality=4, env=CIEnvironment.DEV):
    ci = await cmdb.create_ci(
        session,
        CICreate(name=name, ci_class=ci_class, criticality=criticality, environment=env),
        "mudur",
    )
    return ci.id


async def _normal(session, ci_ids=(), start=T0, hours=2, user=REQ, **kw):
    fields = {
        "title": "Veritabanı yaması",
        "change_type": ChangeType.NORMAL,
        "implementation_plan": "Yamayı uygula",
        "backout_plan": "Anlık görüntüden geri dön",
        "test_plan": "Duman testi",
        "planned_start": start,
        "planned_end": start + timedelta(hours=hours),
        "assignee": "mehmet",
        "affected_ci_ids": list(ci_ids),
    }
    fields.update(kw)
    return await changes.create_change(session, ChangeCreate(**fields), user)


async def _tr(session, change, to, user=MGR, **kw):
    kw.setdefault("settings", S)
    return await changes.transition(session, change.id, to, user, **kw)


async def _approve(session, change, *users, settings=S):
    for u in users:
        await changes.record_approval(session, change.id, u, A.APPROVE, "uygun", settings=settings)


async def _scheduled_normal(session, ci_ids=(), start=T0, **kw):
    ch = await _normal(session, ci_ids, start=start, **kw)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1, CAB2)
    return await _tr(session, ch, St.SCHEDULED)


# --- oluşturma ve düzenleme -------------------------------------------------------


async def test_create_change_numbering_and_audit(session):
    ch = await _normal(session)
    ch2 = await _normal(session)
    assert ch.number == "CHG0000001" and ch2.number == "CHG0000002"
    assert ch.state == St.DRAFT and ch.version == 1 and ch.requested_by == "ayse"
    assert ch.risk_score is None
    actions = (await session.scalars(select(AuditLog.action))).all()
    assert actions.count("change.create") == 2


def test_planned_window_must_be_tz_aware():
    with pytest.raises(ValidationError):
        ChangeCreate(title="x", change_type="normal", planned_start=datetime(2030, 1, 1))


def test_planned_window_order():
    with pytest.raises(ValidationError):
        ChangeCreate(title="x", change_type="normal", planned_start=T0, planned_end=T0)


async def test_create_validates_references(session):
    with pytest.raises(changes.ChangeValidationError):
        await _normal(session, ["yok"])
    with pytest.raises(changes.ChangeValidationError):
        await _normal(session, linked_incident_ids=["yok"])
    with pytest.raises(changes.ChangeValidationError):
        await _normal(session, assignment_group="olmayan_grup")


async def test_update_only_in_draft_with_version(session):
    ch = await _normal(session)
    ch = await changes.update_change(
        session, ch.id, ChangeUpdate(title="Yeni"), REQ, expected_version=1
    )
    assert ch.title == "Yeni" and ch.version == 2
    with pytest.raises(changes.VersionConflictError):
        await changes.update_change(session, ch.id, ChangeUpdate(title="x"), REQ, expected_version=1)
    # Başkasının değişikliğini yalnızca change_manager düzenler
    with pytest.raises(changes.ChangeForbiddenError):
        await changes.update_change(session, ch.id, ChangeUpdate(title="x"), IMPL)
    await _tr(session, ch, St.ASSESS, REQ)
    with pytest.raises(changes.InvalidTransitionError):
        await changes.update_change(session, ch.id, ChangeUpdate(title="x"), MGR)


# --- normal değişiklik -------------------------------------------------------------


@pytest.mark.parametrize(
    "missing", ["implementation_plan", "backout_plan", "planned_start", "planned_end"]
)
async def test_normal_cannot_leave_draft_without_plans(session, missing):
    kw = {missing: None if missing.startswith("planned") else ""}
    if missing == "planned_start":
        kw["planned_end"] = None
    ch = await _normal(session, **kw)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.ASSESS, REQ)
    assert (await changes.get_change(session, ch.id)).state == St.DRAFT


async def test_normal_happy_path_full_lifecycle(session):
    ci = await _ci(session, "db-01", CIClass.DATABASE, 2, CIEnvironment.PROD)
    ch = await _normal(session, [ci])
    ch = await _tr(session, ch, St.ASSESS, REQ)
    assert ch.risk_score is not None and 0 <= ch.risk_score <= 100
    assert ch.risk_level is not None
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED)  # CAB atlanamaz
    ch = await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED)  # 1 onay yetmez (varsayılan 2)
    await _approve(session, ch, CAB2)
    status = await changes.approval_status(session, ch.id, settings=S)
    assert status.required == 2 and status.approvals == 2 and status.satisfied
    ch = await _tr(session, ch, St.SCHEDULED)
    ch = await _tr(session, ch, St.IMPLEMENT, IMPL, now=INSIDE)
    assert ch.actual_start is not None
    ch = await _tr(session, ch, St.REVIEW, IMPL, now=INSIDE)
    assert ch.implementation_outcome == "succeeded"
    ch = await _tr(session, ch, St.CLOSED, review_notes="Sorunsuz")
    assert ch.state == St.CLOSED and ch.closed_at is not None
    for terminal_target in (St.DRAFT, St.REVIEW):
        with pytest.raises(changes.InvalidTransitionError):
            await _tr(session, ch, terminal_target)
    rows = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.action == "change.transition", AuditLog.entity_id == ch.id
            )
        )
    ).all()
    assert [(r.details["from"], r.details["to"]) for r in rows] == [
        ("draft", "assess"),
        ("assess", "authorize"),
        ("authorize", "scheduled"),
        ("scheduled", "implement"),
        ("implement", "review"),
        ("review", "closed"),
    ]
    assert ch.version == 7
    assert await audit.verify_chain(session)


async def test_cab_min_approvals_setting(session):
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    three = Settings(cab_min_approvals=3)
    await _approve(session, ch, CAB1, CAB2, settings=three)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED, settings=three)
    await _approve(session, ch, CAB3, settings=three)
    assert (await _tr(session, ch, St.SCHEDULED, settings=three)).state == St.SCHEDULED


async def test_single_reject_moves_to_rejected(session):
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1)
    await changes.record_approval(session, ch.id, CAB2, A.REJECT, "Geri dönüş zayıf", settings=S)
    ch = await changes.get_change(session, ch.id)
    assert ch.state == St.REJECTED
    with pytest.raises(changes.ApprovalError):
        await _approve(session, ch, CAB3)
    for target in (St.SCHEDULED, St.AUTHORIZE, St.DRAFT, St.IMPLEMENT):
        with pytest.raises(changes.InvalidTransitionError):
            await _tr(session, ch, target)


async def test_approval_rules(session):
    ch = await _normal(session)
    with pytest.raises(changes.ApprovalError):
        await _approve(session, ch, CAB1)  # henüz authorize değil
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    # Talep eden ve uygulayıcı (assignee) kendi değişikliğini onaylayamaz — rolü ne olursa olsun
    for own in (
        CurrentUser("ayse", frozenset({"cab", "change_manager"})),
        CurrentUser("mehmet", frozenset({"admin"})),
    ):
        with pytest.raises(changes.ChangeForbiddenError):
            await _approve(session, ch, own)
        with pytest.raises(changes.ChangeForbiddenError):
            await changes.record_approval(session, ch.id, own, A.REJECT, "", settings=S)
    with pytest.raises(changes.ChangeForbiddenError):
        await _approve(session, ch, CurrentUser("baska", frozenset({"agent", "approver"})))
    await _approve(session, ch, CAB1)
    with pytest.raises(changes.DuplicateApprovalError):
        await _approve(session, ch, CAB1)
    approvals = await changes.list_approvals(session, ch.id)
    assert [(a.approver, a.decision) for a in approvals] == [("kurul1", A.APPROVE)]
    assert (await changes.get_change(session, ch.id)).state == St.AUTHORIZE


async def test_concurrent_duplicate_approval_is_conflict_not_500(session, monkeypatch):
    # Yarış: iki istek varlık denetimini aynı anda geçer; tekil kısıt ikincisini durdurur.
    # Servis bunu IntegrityError (500) yerine DuplicateApprovalError (409) olarak bildirmeli ve
    # oturum kullanılabilir kalmalı (ilk onay korunur).
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1)
    original = session.scalar

    async def racing_scalar(stmt, *args, **kwargs):
        if str(stmt).startswith("SELECT change_approvals.id"):
            return None  # diğer istek henüz görünmüyor
        return await original(stmt, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", racing_scalar)
    with pytest.raises(changes.DuplicateApprovalError):
        await _approve(session, ch, CAB1)
    monkeypatch.setattr(session, "scalar", original)
    approvals = await changes.list_approvals(session, ch.id)
    assert [(a.approver, a.decision) for a in approvals] == [("kurul1", A.APPROVE)]
    assert (await changes.get_change(session, ch.id)).state == St.AUTHORIZE


async def test_requester_cannot_submit_others_change(session):
    ch = await _normal(session)
    with pytest.raises(changes.ChangeForbiddenError):
        await _tr(session, ch, St.ASSESS, IMPL)
    with pytest.raises(changes.ChangeForbiddenError):
        await _tr(session, ch, St.CANCELLED, IMPL)
    assert (await _tr(session, ch, St.CANCELLED, REQ)).state == St.CANCELLED


async def test_only_change_manager_authorizes_and_schedules(session):
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    with pytest.raises(changes.ChangeForbiddenError):
        await _tr(session, ch, St.AUTHORIZE, REQ)
    with pytest.raises(changes.ChangeForbiddenError):
        await _tr(session, ch, St.AUTHORIZE, CAB1)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1, CAB2)
    with pytest.raises(changes.ChangeForbiddenError):
        await _tr(session, ch, St.SCHEDULED, REQ)
    await _tr(session, ch, St.SCHEDULED)
    with pytest.raises(changes.ChangeForbiddenError):
        await _tr(session, ch, St.IMPLEMENT, CAB1, now=INSIDE)


async def test_transition_version_conflict(session):
    ch = await _normal(session)
    with pytest.raises(changes.VersionConflictError):
        await _tr(session, ch, St.ASSESS, REQ, expected_version=5)
    ch = await _tr(session, ch, St.ASSESS, REQ, expected_version=1)
    assert ch.version == 2


# --- uygulama penceresi -------------------------------------------------------------


async def test_implement_only_within_planned_window(session):
    ch = await _scheduled_normal(session)
    for when in (T0 - timedelta(minutes=1), T0 + timedelta(hours=2), T0 + timedelta(days=1)):
        with pytest.raises(changes.InvalidTransitionError):
            await _tr(session, ch, St.IMPLEMENT, IMPL, now=when)
    ch = await _tr(session, ch, St.IMPLEMENT, IMPL, now=T0)
    assert ch.state == St.IMPLEMENT


async def test_implement_only_from_scheduled(session):
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1, CAB2)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.IMPLEMENT, now=INSIDE)


async def test_failed_implementation_requires_pir(session):
    ch = await _scheduled_normal(session)
    await _tr(session, ch, St.IMPLEMENT, IMPL, now=INSIDE)
    ch = await _tr(session, ch, St.FAILED, IMPL, now=INSIDE, notes="Yama başarısız")
    assert ch.implementation_outcome == "failed" and ch.actual_end is not None
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.CLOSED)
    ch = await _tr(session, ch, St.REVIEW, IMPL)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.CLOSED)  # PIR notu yok
    ch = await _tr(session, ch, St.CLOSED, review_notes="Kök neden: eksik test")
    assert ch.review_notes == "Kök neden: eksik test"


# --- takvim, dondurma ve çakışmalar ---------------------------------------------------


async def _to_authorize(session, ch):
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1, CAB2)
    return ch


async def test_freeze_window_blocks_scheduling(session):
    ci = await _ci(session, "web-01", env=CIEnvironment.PROD)
    fw = await changes.create_freeze_window(
        session,
        FreezeWindowCreate(
            start_at=T0 - timedelta(days=1),
            end_at=T0 + timedelta(hours=1),
            reason="Yıl sonu kapanışı",
            environments=[CIEnvironment.PROD],
        ),
        MGR,
    )
    ch = await _to_authorize(session, await _normal(session, [ci]))
    with pytest.raises(changes.ScheduleConflictError) as exc:
        await _tr(session, ch, St.SCHEDULED)
    assert [(c.kind, c.ref_id) for c in exc.value.conflicts] == [("freeze_window", fw.id)]
    assert "Yıl sonu" in exc.value.conflicts[0].description
    assert (await changes.get_change(session, ch.id)).state == St.AUTHORIZE
    conflicts = await changes.find_conflicts(session, ch.id)
    assert len(conflicts) == 1


async def test_freeze_window_other_environment_does_not_block(session):
    ci = await _ci(session, "test-01", env=CIEnvironment.TEST)
    await changes.create_freeze_window(
        session,
        FreezeWindowCreate(
            start_at=T0, end_at=T0 + timedelta(hours=5), reason="x", environments=["prod"]
        ),
        MGR,
    )
    ch = await _to_authorize(session, await _normal(session, [ci]))
    assert (await _tr(session, ch, St.SCHEDULED)).state == St.SCHEDULED


async def test_freeze_window_without_environments_applies_everywhere(session):
    await changes.create_freeze_window(
        session, FreezeWindowCreate(start_at=T0, end_at=T0 + timedelta(hours=5), reason="x"), MGR
    )
    ch = await _to_authorize(session, await _normal(session))
    with pytest.raises(changes.ScheduleConflictError):
        await _tr(session, ch, St.SCHEDULED)


async def test_freeze_window_added_after_scheduling_blocks_implement(session):
    ch = await _scheduled_normal(session)
    await changes.create_freeze_window(
        session, FreezeWindowCreate(start_at=T0, end_at=T0 + timedelta(hours=5), reason="x"), MGR
    )
    with pytest.raises(changes.ScheduleConflictError):
        await _tr(session, ch, St.IMPLEMENT, IMPL, now=INSIDE)


async def test_overlapping_change_on_same_ci_conflicts(session):
    shared = await _ci(session, "db-01")
    other = await _ci(session, "db-02")
    first = await _scheduled_normal(session, [shared])
    overlapping = await _to_authorize(
        session, await _normal(session, [shared, other], start=T0 + timedelta(hours=1))
    )
    with pytest.raises(changes.ScheduleConflictError) as exc:
        await _tr(session, overlapping, St.SCHEDULED)
    assert [(c.kind, c.ref_id) for c in exc.value.conflicts] == [("change", first.id)]
    assert first.number in exc.value.conflicts[0].description
    # Farklı CI ya da çakışmayan pencere: serbest
    ok1 = await _to_authorize(session, await _normal(session, [other], start=T0))
    assert (await _tr(session, ok1, St.SCHEDULED)).state == St.SCHEDULED
    ok2 = await _to_authorize(session, await _normal(session, [shared], start=T0 + timedelta(hours=2)))
    assert (await _tr(session, ok2, St.SCHEDULED)).state == St.SCHEDULED


async def test_calendar(session):
    ch = await _scheduled_normal(session)
    await _normal(session, start=T0 + timedelta(days=30))  # taslak, aralık dışı
    fw = await changes.create_freeze_window(
        session, FreezeWindowCreate(start_at=T0 + timedelta(days=2), end_at=T0 + timedelta(days=3), reason="x"), MGR
    )
    cal = await changes.calendar(session, T0 - timedelta(days=1), T0 + timedelta(days=5))
    assert [c.id for c in cal.changes] == [ch.id]
    assert [f.id for f in cal.freeze_windows] == [fw.id]
    with pytest.raises(changes.ChangeValidationError):
        await changes.calendar(session, T0, T0 - timedelta(days=1))


# --- standart değişiklik ---------------------------------------------------------------


async def _template(session, **kw):
    fields = {
        "name": "Sunucu yeniden başlatma",
        "description": "Önceden onaylı yeniden başlatma",
        "max_risk": RiskLevel.LOW,
        "allowed_ci_classes": [CIClass.SERVER],
        "implementation_plan": "Servisi durdur, yeniden başlat",
        "backout_plan": "Gerekmez; sunucu açılmazsa konsoldan başlat",
        "test_plan": "Sağlık kontrolü",
    }
    fields.update(kw)
    return await changes.create_template(session, TemplateCreate(**fields), MGR)


async def _standard(session, template, ci_ids, start=T0):
    return await changes.create_change(
        session,
        ChangeCreate(
            title="Yeniden başlatma",
            change_type=ChangeType.STANDARD,
            template_id=template.id,
            planned_start=start,
            planned_end=start + timedelta(hours=1),
            affected_ci_ids=list(ci_ids),
        ),
        REQ,
    )


async def test_standard_requires_active_template(session):
    with pytest.raises(changes.ChangeValidationError):
        await changes.create_change(
            session, ChangeCreate(title="x", change_type=ChangeType.STANDARD), REQ
        )
    tpl = await _template(session)
    await changes.update_template(session, tpl.id, TemplateUpdate(active=False), MGR)
    with pytest.raises(changes.ChangeValidationError):
        await _standard(session, tpl, [])


async def test_standard_low_risk_skips_cab(session):
    tpl = await _template(session)
    ci = await _ci(session, "app-srv-dev", CIClass.SERVER, 4, CIEnvironment.DEV)
    ch = await _standard(session, tpl, [ci])
    assert ch.implementation_plan.startswith("Servisi durdur")  # şablondan
    ch = await _tr(session, ch, St.ASSESS, REQ)
    assert ch.change_type == ChangeType.STANDARD and ch.risk_level == RiskLevel.LOW
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.AUTHORIZE)  # standart CAB'a gitmez
    ch = await _tr(session, ch, St.SCHEDULED, REQ)  # talep eden zamanlayabilir (önceden onaylı)
    assert ch.state == St.SCHEDULED
    assert await changes.list_approvals(session, ch.id) == []
    ch = await _tr(session, ch, St.IMPLEMENT, REQ, now=INSIDE)
    assert ch.state == St.IMPLEMENT


async def test_standard_over_max_risk_becomes_normal(session):
    tpl = await _template(session)
    ci = await _ci(session, "erp-db", CIClass.SERVER, 1, CIEnvironment.PROD)
    ch = await _tr(session, await _standard(session, tpl, [ci]), St.ASSESS, REQ)
    assert ch.change_type == ChangeType.NORMAL
    assert ch.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED, REQ)
    await _tr(session, ch, St.AUTHORIZE)  # artık CAB gerekli
    actions = (await session.scalars(select(AuditLog.action))).all()
    assert "change.converted_to_normal" in actions


async def test_standard_disallowed_ci_class_becomes_normal(session):
    tpl = await _template(session)
    ci = await _ci(session, "db-dev", CIClass.DATABASE, 4, CIEnvironment.DEV)
    ch = await _tr(session, await _standard(session, tpl, [ci]), St.ASSESS, REQ)
    assert ch.change_type == ChangeType.NORMAL


async def test_standard_template_deactivated_after_submit_needs_cab(session):
    tpl = await _template(session)
    ci = await _ci(session, "srv", CIClass.SERVER, 4)
    ch = await _tr(session, await _standard(session, tpl, [ci]), St.ASSESS, REQ)
    await changes.update_template(session, tpl.id, TemplateUpdate(active=False), MGR)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED, REQ)


# --- acil değişiklik (ECAB) ------------------------------------------------------------


async def _emergency(session, ci_ids=(), **kw):
    return await _normal(session, ci_ids, change_type=ChangeType.EMERGENCY, **kw)


async def test_emergency_needs_change_manager_approval(session):
    ch = await _emergency(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, CAB1, CAB2)  # CAB üyeleri ECAB onayı yerine geçmez
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED)
    await _approve(session, ch, MGR2)
    ch = await _tr(session, ch, St.SCHEDULED)
    assert ch.state == St.SCHEDULED


async def test_emergency_single_change_manager_approval_suffices(session):
    ch = await _emergency(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, MGR2, settings=Settings(cab_min_approvals=5))
    assert (await _tr(session, ch, St.SCHEDULED, settings=Settings(cab_min_approvals=5))).state == (
        St.SCHEDULED
    )


async def test_emergency_ignores_freeze_and_requires_pir(session):
    await changes.create_freeze_window(
        session, FreezeWindowCreate(start_at=T0, end_at=T0 + timedelta(hours=5), reason="x"), MGR
    )
    ch = await _emergency(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, MGR2)
    await _tr(session, ch, St.SCHEDULED)
    await _tr(session, ch, St.IMPLEMENT, IMPL, now=INSIDE)
    await _tr(session, ch, St.REVIEW, IMPL, now=INSIDE)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.CLOSED)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.CLOSED, review_notes="   ")
    ch = await _tr(session, ch, St.CLOSED, review_notes="PIR tamamlandı")
    assert ch.state == St.CLOSED


async def test_emergency_still_blocked_by_ci_overlap(session):
    ci = await _ci(session, "db")
    await _scheduled_normal(session, [ci])
    ch = await _emergency(session, [ci])
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await _approve(session, ch, MGR2)
    with pytest.raises(changes.ScheduleConflictError):
        await _tr(session, ch, St.SCHEDULED)


# --- risk değerlendirmesi ---------------------------------------------------------------


async def test_risk_uses_impact_analysis(session):
    db_ci = await _ci(session, "db", CIClass.DATABASE, 3, CIEnvironment.PROD)
    svc = await _ci(session, "ERP", CIClass.SERVICE, 1, CIEnvironment.PROD)
    await cmdb.add_relationship(session, svc, db_ci, CIRelationshipType.DEPENDS_ON, "mudur")
    low = await _normal(session, [await _ci(session, "dev-srv")])
    high = await _normal(session, [db_ci])
    low_r = await changes.compute_risk(session, low.id)
    high_r = await changes.compute_risk(session, high.id)
    assert high_r.score > low_r.score
    assert high_r.highest_criticality == 1 and high_r.business_services == ["ERP"]
    assert high_r.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert low_r.level == RiskLevel.LOW
    assert 0 <= low_r.score <= 100 and 0 <= high_r.score <= 100


# --- Faz 2 kapısı: riskli hiçbir eylem onaysız çalışmaz -----------------------------------


@pytest.mark.parametrize("action", ["execute", "approve"])
@pytest.mark.parametrize("risk", list(RiskLevel))
@pytest.mark.parametrize("shadow", [True, False])
def test_gate_policy_never_auto_executes_or_approves(action, risk, shadow):
    assert POLICIES[("change", action)].max_auto_risk is None
    settings = Settings(ai_shadow_mode=shadow, ai_confidence_threshold=0.0)
    d = evaluate(agent="change", action=action, confidence=1.0, risk=risk, settings=settings)
    assert d.outcome == DecisionOutcome.NEEDS_APPROVAL


def test_gate_assess_risk_policy():
    assert ("change", "assess_risk") in POLICIES
    d = evaluate(agent="change", action="assess_risk", confidence=0.9, settings=LIVE)
    assert d.outcome == DecisionOutcome.AUTO_APPLIED
    d = evaluate(agent="change", action="assess_risk", confidence=0.9, settings=S)
    assert d.outcome == DecisionOutcome.SUGGESTED


async def test_gate_ai_actor_cannot_transition_or_approve(session):
    ch = await _normal(session)
    with pytest.raises(changes.ChangeForbiddenError):
        await _tr(session, ch, St.ASSESS, AI_ACTOR)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    with pytest.raises(changes.ChangeForbiddenError):
        await _approve(session, ch, AI_ACTOR)
    await _approve(session, ch, CAB1, CAB2)
    for target in (St.SCHEDULED, St.CANCELLED):
        with pytest.raises(changes.ChangeForbiddenError):
            await _tr(session, ch, target, AI_ACTOR)


@pytest.mark.parametrize("approvals", [(), ("kurul1",)])
async def test_gate_implement_rechecks_authorization(session, approvals):
    """Durum bir şekilde (ör. doğrudan veritabanı) 'scheduled' yapılsa bile onaysız uygulanamaz."""
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    for name in approvals:
        await _approve(session, ch, CurrentUser(name, frozenset({"cab"})))
    row = await session.get(ChangeRequest, ch.id)
    row.state = St.SCHEDULED  # kuralları atlayan yazma
    await session.flush()
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.IMPLEMENT, IMPL, now=INSIDE)


async def test_gate_ineligible_standard_cannot_implement_without_cab(session):
    tpl = await _template(session)
    ci = await _ci(session, "srv", CIClass.SERVER, 4)
    ch = await _standard(session, tpl, [ci])
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.SCHEDULED, REQ)
    # Şablon devre dışı bırakıldı: önceden onay geçersiz, CAB'sız uygulanamaz
    await changes.update_template(session, tpl.id, TemplateUpdate(active=False), MGR)
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.IMPLEMENT, REQ, now=INSIDE)


async def _ai_risk(session, change, score, settings, confidence=0.9):
    return await changes.propose_ai_risk(
        session,
        change.id,
        risk_score=score,
        confidence=confidence,
        rationale="Benzer değişiklikler geçmişte kesintiye yol açtı",
        model="fake",
        settings=settings,
    )


async def _decisions(session, change_id):
    stmt = select(AIDecision).where(AIDecision.change_id == change_id)
    return (await session.scalars(stmt)).all()


async def test_gate_ai_risk_shadow_mode_only_suggests(session):
    ch = await _tr(session, await _normal(session), St.ASSESS, REQ)
    before = (ch.risk_score, ch.version)
    d = await _ai_risk(session, ch, 95, S)
    assert d.outcome == DecisionOutcome.SUGGESTED and d.change_id == ch.id
    ch = await changes.get_change(session, ch.id)
    assert (ch.risk_score, ch.version) == before
    actions = (await session.scalars(select(AuditLog.action))).all()
    assert "ai_decision.change_risk" in actions


async def test_gate_ai_risk_raise_auto_applies_risk_only(session):
    ch = await _tr(session, await _normal(session), St.ASSESS, REQ)
    d = await _ai_risk(session, ch, 95, LIVE)
    assert d.outcome == DecisionOutcome.AUTO_APPLIED
    ch = await changes.get_change(session, ch.id)
    assert ch.risk_score == 95 and ch.risk_level == RiskLevel.CRITICAL
    assert ch.state == St.ASSESS  # durum asla değişmez


async def test_gate_ai_risk_lowering_needs_human_approval(session):
    ci = await _ci(session, "db", CIClass.DATABASE, 1, CIEnvironment.PROD)
    ch = await _tr(session, await _normal(session, [ci]), St.ASSESS, REQ)
    original = ch.risk_score
    d = await _ai_risk(session, ch, 5, LIVE)
    assert d.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert (await changes.get_change(session, ch.id)).risk_score == original
    with pytest.raises(changes.ChangeForbiddenError):
        await changes.approve_ai_decision(session, d.id, REQ)  # yalnızca change_manager
    d = await changes.approve_ai_decision(session, d.id, MGR)
    assert d.approved_by == "mudur"
    ch = await changes.get_change(session, ch.id)
    assert ch.risk_score == 5 and ch.risk_level == RiskLevel.LOW and ch.state == St.ASSESS
    with pytest.raises(changes.ApprovalError):
        await changes.approve_ai_decision(session, d.id, MGR)


async def test_gate_ai_risk_low_confidence_escalated(session):
    ch = await _tr(session, await _normal(session), St.ASSESS, REQ)
    d = await _ai_risk(session, ch, 95, LIVE, confidence=0.2)
    assert d.outcome == DecisionOutcome.ESCALATED
    assert (await changes.get_change(session, ch.id)).risk_score != 95


async def test_gate_ai_risk_raise_converts_standard_to_normal(session):
    tpl = await _template(session)
    ch = await _standard(session, tpl, [await _ci(session, "srv")])
    ch = await _tr(session, ch, St.ASSESS, REQ)
    await _ai_risk(session, ch, 80, LIVE)
    ch = await changes.get_change(session, ch.id)
    assert ch.change_type == ChangeType.NORMAL  # artık CAB atlanamaz
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED, REQ)


async def test_gate_ai_risk_not_allowed_after_authorization_started(session):
    ch = await _scheduled_normal(session)
    with pytest.raises(changes.InvalidTransitionError):
        await _ai_risk(session, ch, 99, LIVE)
    assert await _decisions(session, ch.id) == []


async def test_gate_stale_ai_risk_decision_not_applied(session):
    ch = await _tr(session, await _normal(session), St.ASSESS, REQ)
    d = await _ai_risk(session, ch, 1, LIVE)
    await _tr(session, ch, St.AUTHORIZE)  # karar sonrası değişiklik ilerledi
    with pytest.raises(changes.StaleDecisionError):
        await changes.approve_ai_decision(session, d.id, MGR)


# --- API ----------------------------------------------------------------------------------

H_REQ = AGENT  # ayse, agent
H_IMPL = dev_headers("mehmet", "agent")
H_MGR = dev_headers("mudur", "change_manager")
H_CAB1 = dev_headers("kurul1", "cab")
H_CAB2 = dev_headers("kurul2", "cab")
H_REQUESTER = dev_headers("zeynep", "requester")
H_APPROVER = dev_headers("sef.ali", "approver")


@pytest.fixture
async def client(session):
    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[changes_api.get_change_settings] = lambda: S
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _window(start: datetime, hours: int = 2) -> dict:
    return {
        "planned_start": start.isoformat(),
        "planned_end": (start + timedelta(hours=hours)).isoformat(),
    }


async def _api_create(client, start, **kw):
    body = {
        "title": "Yama",
        "change_type": "normal",
        "implementation_plan": "uygula",
        "backout_plan": "geri al",
        "assignee": "mehmet",
        **_window(start),
    }
    body.update(kw)
    r = await client.post("/changes", json=body, headers=H_REQ)
    assert r.status_code == 201, r.text
    return r.json()


async def _api_tr(client, change_id, to, headers=H_MGR, **kw):
    return await client.post(
        f"/changes/{change_id}/transition", json={"to_state": to, **kw}, headers=headers
    )


async def test_api_role_guards(client):
    assert (await client.get("/changes", headers=H_REQUESTER)).status_code == 403
    assert (await client.get("/changes", headers=H_APPROVER)).status_code == 403
    for h in (H_REQ, H_MGR, H_CAB1):
        assert (await client.get("/changes", headers=h)).status_code == 200
    assert (
        await client.post("/changes", json={"title": "x", "change_type": "normal"}, headers=H_CAB1)
    ).status_code == 403
    tpl = {"name": "t", "max_risk": "low", "allowed_ci_classes": ["server"]}
    assert (await client.post("/changes/templates", json=tpl, headers=H_REQ)).status_code == 403
    assert (await client.post("/changes/templates", json=tpl, headers=H_MGR)).status_code == 201
    fw = {**_window(T0), "reason": "x"}
    fw = {"start_at": fw["planned_start"], "end_at": fw["planned_end"], "reason": "x"}
    assert (await client.post("/changes/freeze-windows", json=fw, headers=H_REQ)).status_code == 403
    r = await client.post("/changes/freeze-windows", json=fw, headers=H_MGR)
    assert r.status_code == 201
    assert len((await client.get("/changes/freeze-windows", headers=H_REQ)).json()) == 1
    fw_id = r.json()["id"]
    assert (await client.delete(f"/changes/freeze-windows/{fw_id}", headers=H_MGR)).status_code == 204
    assert (await client.get("/changes/freeze-windows", headers=H_REQ)).json() == []
    r = await client.post(
        "/changes", json={"title": "x", "change_type": "normal", "planned_start": "2030-01-01T10:00:00"},
        headers=H_REQ,
    )
    assert r.status_code == 422  # saat dilimsiz


async def test_api_full_flow(client):
    now = datetime.now(UTC)
    ch = await _api_create(client, now - timedelta(minutes=5))
    assert ch["number"] == "CHG0000001" and ch["state"] == "draft"
    cid = ch["id"]
    r = await client.get(f"/changes/{cid}", headers=H_CAB1)
    assert r.status_code == 200 and r.headers["etag"] == '"1"'
    assert (await client.get("/changes/yok", headers=H_REQ)).status_code == 404

    r = await client.patch(
        f"/changes/{cid}", json={"title": "Yama v2"}, headers={**H_REQ, "If-Match": '"1"'}
    )
    assert r.status_code == 200 and r.json()["version"] == 2
    r = await client.patch(
        f"/changes/{cid}", json={"title": "x"}, headers={**H_REQ, "If-Match": '"1"'}
    )
    assert r.status_code == 409

    r = await client.post(
        f"/changes/{cid}/transition",
        json={"to_state": "assess"},
        headers={**H_REQ, "If-Match": '"1"'},
    )
    assert r.status_code == 409
    r = await _api_tr(client, cid, "assess", H_REQ, expected_version=2)
    assert r.status_code == 200, r.text
    assert r.json()["risk_score"] is not None and r.headers["etag"] == '"3"'
    assert (await _api_tr(client, cid, "scheduled")).status_code == 422
    assert (await _api_tr(client, cid, "authorize", H_REQ)).status_code == 403
    assert (await _api_tr(client, cid, "authorize")).status_code == 200

    approve = {"decision": "approve", "comment": "uygun"}
    r = await client.post(f"/changes/{cid}/approvals", json=approve, headers=H_REQ)
    assert r.status_code == 403  # agent, CAB değil (ve talep eden)
    r = await client.post(f"/changes/{cid}/approvals", json=approve, headers=H_CAB1)
    assert r.status_code == 201 and r.json()["approver"] == "kurul1"
    r = await client.post(f"/changes/{cid}/approvals", json=approve, headers=H_CAB1)
    assert r.status_code == 409
    r = await client.post(
        f"/changes/{cid}/approvals", json={**approve, "approver": "baska"}, headers=H_CAB2
    )
    assert r.status_code == 403  # başkası adına onay yok
    assert (await client.post(f"/changes/{cid}/approvals", json=approve, headers=H_CAB2)).status_code == 201
    r = await client.get(f"/changes/{cid}/approvals", headers=H_REQ)
    data = r.json()
    assert data["status"] == {"required": 2, "approvals": 2, "rejections": 0, "satisfied": True}
    assert [a["approver"] for a in data["items"]] == ["kurul1", "kurul2"]

    r = await client.get(f"/changes/{cid}/conflicts", headers=H_REQ)
    assert r.status_code == 200 and r.json() == []
    assert (await _api_tr(client, cid, "scheduled")).status_code == 200
    r = await _api_tr(client, cid, "implement", H_IMPL)
    assert r.status_code == 200, r.text
    assert (await _api_tr(client, cid, "review", H_IMPL)).status_code == 200
    r = await _api_tr(client, cid, "closed", review_notes="Tamam")
    assert r.status_code == 200 and r.json()["state"] == "closed"

    r = await client.get("/changes", params={"state": "closed"}, headers=H_CAB1)
    assert [c["number"] for c in r.json()] == ["CHG0000001"]


async def test_api_schedule_conflict_returns_409_with_conflicts(client):
    r = await client.post(
        "/changes/freeze-windows",
        json={
            "start_at": T0.isoformat(),
            "end_at": (T0 + timedelta(hours=3)).isoformat(),
            "reason": "Kapanış",
        },
        headers=H_MGR,
    )
    fw_id = r.json()["id"]
    cid = (await _api_create(client, T0))["id"]
    await _api_tr(client, cid, "assess", H_REQ)
    await _api_tr(client, cid, "authorize")
    for h in (H_CAB1, H_CAB2):
        await client.post(f"/changes/{cid}/approvals", json={"decision": "approve"}, headers=h)
    r = await client.get(f"/changes/{cid}/conflicts", headers=H_REQ)
    assert [c["ref_id"] for c in r.json()] == [fw_id]
    r = await _api_tr(client, cid, "scheduled")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["message"] and [c["kind"] for c in detail["conflicts"]] == ["freeze_window"]

    r = await client.get(
        "/changes/calendar",
        params={"from": (T0 - timedelta(days=1)).isoformat(), "to": (T0 + timedelta(days=1)).isoformat()},
        headers=H_CAB1,
    )
    assert r.status_code == 200, r.text
    cal = r.json()
    assert [c["id"] for c in cal["changes"]] == [cid] and len(cal["freeze_windows"]) == 1


async def test_api_reject_and_ai_decisions(client, session):
    cid = (await _api_create(client, T0))["id"]
    await _api_tr(client, cid, "assess", H_REQ)
    d = await changes.propose_ai_risk(
        session, cid, risk_score=99, confidence=0.9, rationale="r", model="fake", settings=S
    )
    await session.commit()
    r = await client.get(f"/changes/{cid}/decisions", headers=H_REQ)
    assert [x["id"] for x in r.json()] == [d.id]
    assert r.json()[0]["change_id"] == cid
    assert (await client.post(f"/changes/decisions/{d.id}/approve", headers=H_REQ)).status_code == 403
    r = await client.post(f"/changes/decisions/{d.id}/approve", headers=H_MGR)
    assert r.status_code == 200 and r.json()["approved_by"] == "mudur"
    assert (await client.get(f"/changes/{cid}", headers=H_REQ)).json()["risk_score"] == 99

    await _api_tr(client, cid, "authorize")
    r = await client.post(
        f"/changes/{cid}/approvals", json={"decision": "reject", "comment": "Hayır"}, headers=H_CAB1
    )
    assert r.status_code == 201
    assert (await client.get(f"/changes/{cid}", headers=H_REQ)).json()["state"] == "rejected"


async def test_api_cmdb_impact_for_change(client, session):
    ci = await _ci(session, "db", CIClass.DATABASE, 2, CIEnvironment.PROD)
    await session.commit()
    cid = (await _api_create(client, T0, affected_ci_ids=[ci]))["id"]
    r = await client.get(f"/changes/{cid}", headers=H_REQ)
    assert r.json()["affected_ci_ids"] == [ci]
    r = await client.get(f"/changes/{cid}/impact", headers=H_CAB1)
    assert r.status_code == 200 and r.json()["highest_criticality"] == 2


async def test_audit_count_matches_transitions(session):
    ch = await _scheduled_normal(session)
    n = await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "change.transition")
    )
    assert n == 3 and ch.version == 4

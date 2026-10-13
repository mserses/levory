"""Faz 2 kapısı — çekişmeli inceleme regresyonları.

Her test, incelemede bulunan ve düzeltilmeden önce başarısız olan bir yolu kanıtlar:
- standart değişiklik, gönderimden sonra CMDB'de ağırlaşan CI'larla CAB'ı atlayamaz,
- CI'sız standart değişiklik önceden onaylı sayılmaz,
- uygulayıcı/talep eden, kullanıcı adının büyük/küçük harf ya da boşluk farkıyla kendi
  değişikliğine oy veremez,
- zamanlama/uygulama, etkilenen CI satırlarını kilitler (eşzamanlı iki zamanlama aynı CI'da
  çakışmayı kaçıramaz),
- etki analizi büyük grafikte sınırlıdır ve kesilen analiz riski düşük göstermez,
- olay karar kutusu (approver) değişiklik risk kararlarını göstermez/onaylatmaz.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _auth import dev_headers
from sqlalchemy import event

from serviszeka import db
from serviszeka.agents import change_risk
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
from serviszeka.llm import FakeLLM
from serviszeka.main import app
from serviszeka.models import ConfigurationItem
from serviszeka.services import changes, cmdb
from serviszeka.services.change_schemas import ChangeCreate, TemplateCreate
from serviszeka.services.cmdb_schemas import CICreate, CIUpdate

St = ChangeState
REQ = CurrentUser("ayse", frozenset({"agent"}))
MGR = CurrentUser("mudur", frozenset({"change_manager"}))
CAB1 = CurrentUser("kurul1", frozenset({"cab"}))
CAB2 = CurrentUser("kurul2", frozenset({"cab"}))
S = Settings(cab_min_approvals=2, ai_shadow_mode=True)
T0 = datetime(2030, 1, 10, 22, 0, tzinfo=UTC)
INSIDE = T0 + timedelta(minutes=30)


async def _ci(session, name, ci_class=CIClass.SERVER, criticality=4, env=CIEnvironment.DEV):
    ci = await cmdb.create_ci(
        session,
        CICreate(name=name, ci_class=ci_class, criticality=criticality, environment=env),
        "mudur",
    )
    return ci.id


async def _template(session, **kw):
    fields = {
        "name": "Uygulama sunucusu yeniden başlatma",
        "max_risk": RiskLevel.LOW,
        "allowed_ci_classes": [CIClass.SERVER],
        "implementation_plan": "Servisi durdur, yeniden başlat",
        "backout_plan": "Gerekmez; sunucu açılmazsa konsoldan başlat",
        "test_plan": "Sağlık kontrolü",
    }
    fields.update(kw)
    return await changes.create_template(session, TemplateCreate(**fields), MGR)


async def _standard(session, template, ci_ids):
    return await changes.create_change(
        session,
        ChangeCreate(
            title="Yeniden başlatma",
            change_type=ChangeType.STANDARD,
            template_id=template.id,
            planned_start=T0,
            planned_end=T0 + timedelta(hours=1),
            affected_ci_ids=list(ci_ids),
        ),
        REQ,
    )


async def _normal(session, ci_ids=(), **kw):
    fields = {
        "title": "Veritabanı yaması",
        "change_type": ChangeType.NORMAL,
        "implementation_plan": "Yamayı uygula",
        "backout_plan": "Anlık görüntüden geri dön",
        "test_plan": "Duman testi",
        "planned_start": T0,
        "planned_end": T0 + timedelta(hours=2),
        "assignee": "mehmet",
        "affected_ci_ids": list(ci_ids),
    }
    fields.update(kw)
    return await changes.create_change(session, ChangeCreate(**fields), REQ)


async def _tr(session, change, to, user=MGR, **kw):
    kw.setdefault("settings", S)
    return await changes.transition(session, change.id, to, user, **kw)


# --- 1) standart değişiklik: gönderimden sonra CMDB'de ağırlaşan CI ---------------------------


async def test_gate_standard_cannot_schedule_after_ci_becomes_critical(session):
    tpl = await _template(session)
    ci = await _ci(session, "app-srv")
    ch = await _tr(session, await _standard(session, tpl, [ci]), St.ASSESS, REQ)
    assert ch.change_type == ChangeType.STANDARD and ch.risk_level == RiskLevel.LOW
    # CMDB'de CI prod + kritiklik 1 oldu: şablonun "low" sınırı artık sağlanmıyor
    await cmdb.update_ci(
        session, ci, CIUpdate(criticality=1, environment=CIEnvironment.PROD), "mudur"
    )
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED, REQ)
    assert ch.state == St.ASSESS


async def test_gate_standard_cannot_implement_after_ci_becomes_critical(session):
    tpl = await _template(session)
    ci = await _ci(session, "app-srv")
    ch = await _tr(session, await _standard(session, tpl, [ci]), St.ASSESS, REQ)
    ch = await _tr(session, ch, St.SCHEDULED, REQ)
    await cmdb.update_ci(
        session, ci, CIUpdate(criticality=1, environment=CIEnvironment.PROD), "mudur"
    )
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.IMPLEMENT, REQ, now=INSIDE)
    assert ch.state == St.SCHEDULED


async def test_gate_standard_cannot_implement_after_new_critical_dependent(session):
    tpl = await _template(session)
    ci = await _ci(session, "app-srv")
    ch = await _tr(session, await _standard(session, tpl, [ci]), St.ASSESS, REQ)
    ch = await _tr(session, ch, St.SCHEDULED, REQ)
    # Zamanlamadan sonra kritik bir prod iş hizmeti bu sunucuya bağımlı hale geldi
    svc = await _ci(session, "odeme", CIClass.SERVICE, 1, CIEnvironment.PROD)
    await cmdb.add_relationship(session, svc, ci, CIRelationshipType.RUNS_ON, "mudur")
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.IMPLEMENT, REQ, now=INSIDE)


async def test_control_unchanged_standard_still_implements(session):
    """Kontrol: CMDB değişmezse standart akış aynen çalışır."""
    tpl = await _template(session)
    ch = await _tr(session, await _standard(session, tpl, [await _ci(session, "srv")]), St.ASSESS, REQ)
    ch = await _tr(session, ch, St.SCHEDULED, REQ)
    ch = await _tr(session, ch, St.IMPLEMENT, REQ, now=INSIDE)
    assert ch.state == St.IMPLEMENT


# --- 2) CI'sız standart değişiklik -----------------------------------------------------------


async def test_gate_standard_without_cis_is_not_preapproved(session):
    tpl = await _template(session)
    ch = await _tr(session, await _standard(session, tpl, []), St.ASSESS, REQ)
    assert ch.change_type == ChangeType.NORMAL  # CAB'a gitmeli
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED, REQ)


# --- 3) öz onay: kullanıcı adı büyük/küçük harf ve boşluk farkı ----------------------------


@pytest.mark.parametrize("assignee", ["Mehmet", " mehmet ", "MEHMET"])
async def test_gate_assignee_case_variant_cannot_self_approve(session, assignee):
    ch = await _normal(session, assignee=assignee)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    impl_cab = CurrentUser("mehmet", frozenset({"cab"}))
    with pytest.raises(changes.ChangeForbiddenError):
        await changes.record_approval(session, ch.id, impl_cab, ApprovalDecision.APPROVE, settings=S)


async def test_gate_requester_case_variant_cannot_self_approve(session):
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    with pytest.raises(changes.ChangeForbiddenError):
        await changes.record_approval(
            session, ch.id, CurrentUser("AYSE", frozenset({"cab"})), ApprovalDecision.APPROVE,
            settings=S,
        )


async def test_assignee_is_stored_trimmed(session):
    ch = await _normal(session, assignee="  mehmet  ")
    assert ch.assignee == "mehmet"


# --- 4) zamanlama yarışı: CI satır kilidi ------------------------------------------------------


def _ci_locks(session) -> list:
    locked: list = []

    def _on_execute(state):
        stmt = state.statement
        if getattr(stmt, "_for_update_arg", None) is None:
            return
        tables = {getattr(f, "name", None) for f in stmt.get_final_froms()}
        if ConfigurationItem.__tablename__ in tables:
            locked.append(stmt)

    event.listen(session.sync_session, "do_orm_execute", _on_execute)
    return locked


@pytest.mark.parametrize("to", [St.SCHEDULED, St.IMPLEMENT])
async def test_gate_schedule_and_implement_lock_affected_ci_rows(session, to):
    """PostgreSQL'de iki oturum aynı CI'a eşzamanlı zamanlama yaparsa ikisi de çakışma
    denetimini birbirini görmeden geçebilir. Çakışma denetiminden önce etkilenen CI satırları
    FOR UPDATE ile kilitlenmeli (ikinci işlem birincinin commit'ini bekler, sonra çakışmayı görür).
    """
    ci = await _ci(session, "db", CIClass.DATABASE, 2, CIEnvironment.PROD)
    ch = await _normal(session, [ci])
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    for u in (CAB1, CAB2):
        await changes.record_approval(session, ch.id, u, ApprovalDecision.APPROVE, settings=S)
    if to == St.IMPLEMENT:
        await _tr(session, ch, St.SCHEDULED)
    locked = _ci_locks(session)
    await _tr(session, ch, to, now=INSIDE)
    assert locked, "etkilenen CI satırları kilitlenmedi"


# --- 5) etki analizi sınırı ------------------------------------------------------------------


async def _star(session, n: int, criticality=4, env=CIEnvironment.DEV) -> str:
    root = await _ci(session, "kok", CIClass.DATABASE, 4)
    for i in range(n):
        leaf = await _ci(session, f"app-{i:03d}", CIClass.APPLICATION, criticality, env)
        await cmdb.add_relationship(session, leaf, root, CIRelationshipType.DEPENDS_ON, "mudur")
    return root


async def test_impact_analysis_caps_number_of_nodes(session, monkeypatch):
    monkeypatch.setattr(cmdb, "MAX_IMPACT_NODES", 5)
    root = await _star(session, 12)
    impact = await cmdb.impact_analysis(session, [root], max_depth=20)
    assert len(impact.affected) == 5
    assert impact.truncated is True
    small = await cmdb.impact_analysis(session, [await _ci(session, "tek")])
    assert small.truncated is False


async def test_impact_api_reports_truncation(session, monkeypatch):
    monkeypatch.setattr(cmdb, "MAX_IMPACT_NODES", 3)
    root = await _star(session, 6)
    await session.commit()

    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get(
                f"/cmdb/cis/{root}/impact", headers=dev_headers("mudur", "change_manager")
            )
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert r.json()["truncated"] is True and len(r.json()["affected"]) == 3


async def test_gate_truncated_impact_counts_as_highest_risk(session, monkeypatch):
    """Kesilen analiz görülmeyen kritik CI'ları kaçırabilir: risk temkinli (en kötü) hesaplanır,
    standart değişiklik CAB'ı atlayamaz."""
    monkeypatch.setattr(cmdb, "MAX_IMPACT_NODES", 3)
    tpl = await _template(session, allowed_ci_classes=[CIClass.DATABASE])
    root = await _star(session, 6)
    ch = await _tr(session, await _standard(session, tpl, [root]), St.ASSESS, REQ)
    assert ch.change_type == ChangeType.NORMAL
    assert ch.risk_level != RiskLevel.LOW


async def test_risk_agent_truncated_impact_is_conservative(session, monkeypatch):
    monkeypatch.setattr(cmdb, "MAX_IMPACT_NODES", 3)
    root = await _star(session, 6)
    ch = await _normal(session, [root])
    det = await change_risk.deterministic_risk(session, ch, now=T0)
    assert det["inputs"]["impact_truncated"] is True
    assert det["factors"]["criticality"] == change_risk.CRITICALITY_POINTS[1]


# --- 6) olay karar kutusu değişiklik kararlarını sızdırmaz -------------------------------------


async def test_incident_inbox_excludes_change_decisions(session):
    ch = await _normal(session, [await _ci(session, "srv")])
    await session.commit()
    llm = FakeLLM(
        [
            {
                "narrative": "Gizli değişiklik anlatısı",
                "checklist": [],
                "adjustment": -10,
                "justification": "düşük",
                "confidence": 1.0,
            }
        ]
    )
    decision = await change_risk.assess(session, ch, llm, S)
    await session.commit()
    assert decision.outcome in (DecisionOutcome.SUGGESTED, DecisionOutcome.NEEDS_APPROVAL)

    async def _get_session() -> AsyncIterator:
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[changes_api.get_change_settings] = lambda: S
    approver = dev_headers("onayci", "approver")  # değişiklikleri göremeyen rol
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            assert (await c.get(f"/changes/{ch.id}", headers=approver)).status_code == 403
            inbox = await c.get("/incidents/decisions", headers=approver)
            assert inbox.status_code == 200
            assert decision.id not in {d["id"] for d in inbox.json()}
            r = await c.post(f"/incidents/decisions/{decision.id}/approve", headers=approver)
            assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_gate_same_approver_case_variant_cannot_vote_twice(session):
    """Aynı CAB üyesi farklı büyük/küçük harfli kullanıcı adıyla iki onay sayılmamalı."""
    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await changes.record_approval(session, ch.id, CAB1, ApprovalDecision.APPROVE, settings=S)
    with pytest.raises(changes.DuplicateApprovalError):
        await changes.record_approval(
            session, ch.id, CurrentUser(" KURUL1", frozenset({"cab"})), ApprovalDecision.APPROVE,
            settings=S,
        )
    with pytest.raises(changes.InvalidTransitionError):
        await _tr(session, ch, St.SCHEDULED)


async def test_gate_status_dedupes_case_variant_approvals(session):
    """Savunma: kayıt yine de varsa (eski veri, doğrudan yazma) aynı kişi bir kez sayılır."""
    from serviszeka.models import ChangeApproval

    ch = await _normal(session)
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    for name in ("kurul1", "Kurul1"):
        session.add(
            ChangeApproval(
                change_id=ch.id, approver=name, approver_roles=["cab"],
                decision=ApprovalDecision.APPROVE, comment="", created_at=T0,
            )
        )
    await session.flush()
    status = await changes.approval_status(session, ch.id, settings=S)
    assert status.approvals == 1 and not status.satisfied


# --- kanıt: onaylar plan/pencere/CI/tür düzenlemesinden sonra yaşayamaz ---------------------


@pytest.mark.parametrize(
    "patch",
    [
        {"implementation_plan": "Başka plan"},
        {"backout_plan": "Başka geri dönüş"},
        {"planned_start": T0 + timedelta(days=1), "planned_end": T0 + timedelta(days=1, hours=1)},
        {"affected_ci_ids": []},
        {"assignee": "kurul1"},
    ],
)
async def test_gate_approved_change_cannot_be_edited_or_sent_back_to_draft(session, patch):
    """Onaylar yalnızca authorize'da verilir; authorize ve sonrası düzenlenemez ve taslağa
    dönemez. Bu yüzden bir onay, verildiği plan/pencere/CI/tür dışında bir içerik için geçerli
    sayılamaz (düzenleme için iptal + yeni değişiklik gerekir)."""
    from serviszeka.services.change_schemas import ChangeUpdate

    ci = await _ci(session, "db", CIClass.DATABASE, 2, CIEnvironment.PROD)
    ch = await _normal(session, [ci])
    await _tr(session, ch, St.ASSESS, REQ)
    await _tr(session, ch, St.AUTHORIZE)
    await changes.record_approval(session, ch.id, CAB1, ApprovalDecision.APPROVE, settings=S)
    for state in (St.AUTHORIZE, St.SCHEDULED):
        if state == St.SCHEDULED:
            await changes.record_approval(session, ch.id, CAB2, ApprovalDecision.APPROVE, settings=S)
            await _tr(session, ch, St.SCHEDULED)
        for user in (REQ, MGR):
            with pytest.raises(changes.InvalidTransitionError):
                await changes.update_change(session, ch.id, ChangeUpdate(**patch), user)
            with pytest.raises(changes.InvalidTransitionError):
                await _tr(session, ch, St.DRAFT, user)
    assert ch.affected_ci_ids == [ci] and ch.change_type == ChangeType.NORMAL

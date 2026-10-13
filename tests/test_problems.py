"""Faz 3 — Sorun yönetimi (ITIL 4 Problem Management): model, servis, ajan, araçlar, /problems API.

Kurallar: yapay zekâ sorunu yalnızca önerir (problem.create R1), RCA'yı yalnızca taslağa yazar
(rca_draft), kalıcı çözüm için yalnızca taslak RFC açar; sorunu asla çözmez/kapatmaz. Kapatma ve
geçici çözümün yayımlanması (R2) her zaman insan onayı ister.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from _auth import AGENT as H_AGENT
from _auth import APPROVER as H_APPROVER
from _auth import dev_headers
from _tooling import AGENT, AI, APPROVER, LIVE, SHADOW
from sqlalchemy import func, select

from serviszeka import db
from serviszeka.agents import problem as problem_agent
from serviszeka.api import problems as problems_api
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import (
    ChangeState,
    CIClass,
    CIEnvironment,
    DecisionOutcome,
    ProblemState,
    RiskClass,
)
from serviszeka.llm import FakeLLM
from serviszeka.llm.fake import DeterministicFakeLLM
from serviszeka.main import app
from serviszeka.models import (
    ActionRequest,
    AIDecision,
    AuditLog,
    ChangeRequest,
    Incident,
    KnowledgeArticle,
    Problem,
)
from serviszeka.policy import POLICIES
from serviszeka.services import cmdb
from serviszeka.services import incidents as incident_service
from serviszeka.services import problems as svc
from serviszeka.services.cmdb_schemas import CICreate
from serviszeka.services.problem_schemas import RCA, ProblemCreate, ProblemUpdate
from serviszeka.services.schemas import IncidentCreate
from serviszeka.tools import ToolStatus, actions, execute_tool, registry

PS = ProblemState
LOOSE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.0)

FIVE_WHY = {
    "method": "5_why",
    "steps": [
        {"question": "VPN neden kopuyor?", "answer": "Ağ geçidi oturumları düşürüyor."},
        {"question": "Oturumlar neden düşüyor?", "answer": "Oturum tablosu doluyor."},
        {"question": "Tablo neden doluyor?", "answer": "Zaman aşımı 24 saat ayarlı."},
    ],
    "root_cause": "VPN ağ geçidinde oturum zaman aşımı yanlış yapılandırılmış.",
}


def _rca_llm(confidence: float = 0.9, **over) -> FakeLLM:
    return FakeLLM([{**FIVE_WHY, "confidence": confidence, **over}])


async def _ci(session, name="vpn-gw") -> str:
    ci = await cmdb.create_ci(
        session,
        CICreate(name=name, ci_class=CIClass.NETWORK_DEVICE, environment=CIEnvironment.PROD),
        "mudur",
    )
    return ci.id


async def _incident(
    session, title: str, *, category: str | None = "ag", ci_id: str | None = None,
    description: str = "", created_at: datetime | None = None,
) -> Incident:  # fmt: skip
    inc = await incident_service.create_incident(
        session,
        IncidentCreate(title=title, description=description, caller="ali", category=category),
        "ayse",
    )
    if ci_id:
        await incident_service.set_affected_cis(session, inc.id, [ci_id], "ayse")
    if created_at is not None:
        inc.created_at = created_at
    await session.flush()
    return inc


async def _problem(session, **kw) -> Problem:
    data = {"title": "VPN kopmaları", "description": "Tekrarlayan VPN kopmaları"} | kw
    return await svc.create_problem(session, ProblemCreate(**data), AGENT)


async def _vpn_cluster(session, n: int = 3) -> tuple[str, list[Incident]]:
    ci_id = await _ci(session)
    titles = ["VPN bağlantısı kopuyor", "VPN sık sık düşüyor", "Evden VPN erişimi yok", "VPN yavaş"]
    incs = [await _incident(session, titles[i % 4], ci_id=ci_id) for i in range(n)]
    await session.commit()
    return ci_id, incs


# --- model + servis ---------------------------------------------------------------------------


async def test_create_problem_numbers_links_and_audits(session):
    ci_id, incs = await _vpn_cluster(session, 2)
    p1 = await _problem(session, incident_ids=[i.id for i in incs], ci_ids=[ci_id])
    p2 = await _problem(session, title="Yazıcı sorunları")
    await session.commit()
    assert (p1.number, p2.number) == ("PRB0000001", "PRB0000002")
    assert p1.state == PS.NEW and p1.version == 1 and p1.created_by == "ayse"
    assert sorted(p1.incident_ids) == sorted(i.id for i in incs)
    assert p1.affected_ci_ids == [ci_id] and p1.change_ids == []
    acts = (await session.scalars(select(AuditLog.action))).all()
    assert acts.count("problem.create") == 2


async def test_create_problem_rejects_unknown_links(session):
    with pytest.raises(svc.ProblemValidationError):
        await _problem(session, incident_ids=["yok"])
    with pytest.raises(svc.ProblemValidationError):
        await _problem(session, ci_ids=["yok"])
    with pytest.raises(ValueError):
        ProblemCreate(title="x", category="olmayan_kategori")


async def test_state_machine_requires_evidence(session):
    p = await _problem(session)
    with pytest.raises(svc.InvalidProblemTransitionError):
        await svc.transition(session, p.id, PS.RESOLVED, AGENT)
    await svc.transition(session, p.id, PS.INVESTIGATING, AGENT)
    # Bilinen hata: geçici çözüm ya da kök neden olmadan olmaz
    with pytest.raises(svc.InvalidProblemTransitionError):
        await svc.transition(session, p.id, PS.KNOWN_ERROR, AGENT)
    await svc.update_problem(session, p.id, ProblemUpdate(workaround="VPN istemcisini yeniden başlatın"), AGENT)
    await svc.transition(session, p.id, PS.KNOWN_ERROR, AGENT)
    # Çözüm kök neden ister
    with pytest.raises(svc.InvalidProblemTransitionError):
        await svc.transition(session, p.id, PS.RESOLVED, AGENT)
    await svc.record_rca(session, p.id, RCA.model_validate(FIVE_WHY), AGENT)
    p = await svc.transition(session, p.id, PS.RESOLVED, AGENT)
    assert p.resolved_at is not None and p.root_cause == FIVE_WHY["root_cause"]
    p = await svc.transition(session, p.id, PS.CLOSED, AGENT, note="Kalıcı çözüm doğrulandı")
    assert p.state == PS.CLOSED and p.closed_at is not None
    with pytest.raises(svc.InvalidProblemTransitionError):
        await svc.transition(session, p.id, PS.INVESTIGATING, AGENT)
    with pytest.raises(svc.InvalidProblemTransitionError):
        await svc.update_problem(session, p.id, ProblemUpdate(title="yeni"), AGENT)


async def test_close_unresolved_requires_note(session):
    p = await _problem(session)
    with pytest.raises(svc.InvalidProblemTransitionError):
        await svc.transition(session, p.id, PS.CLOSED, AGENT)
    p = await svc.transition(session, p.id, PS.CLOSED, AGENT, note="PRB0000009 ile yinelenen kayıt")
    assert p.resolution == "PRB0000009 ile yinelenen kayıt"


@pytest.mark.parametrize("target", [PS.RESOLVED, PS.CLOSED])
async def test_ai_actor_can_never_resolve_or_close(session, target):
    p = await _problem(session)
    await svc.record_rca(session, p.id, RCA.model_validate(FIVE_WHY), AGENT)
    await svc.transition(session, p.id, PS.INVESTIGATING, AGENT)
    with pytest.raises(svc.ProblemForbiddenError):
        await svc.transition(session, p.id, target, AI, note="not")
    assert (await svc.get_problem(session, p.id)).state == PS.INVESTIGATING


async def test_final_rca_is_human_only_and_draft_accept(session):
    p = await _problem(session)
    rca = RCA.model_validate(FIVE_WHY)
    with pytest.raises(svc.ProblemForbiddenError):
        await svc.record_rca(session, p.id, rca, AI)
    with pytest.raises(svc.ProblemForbiddenError):
        await svc.accept_rca_draft(session, p.id, AI)
    with pytest.raises(svc.ProblemValidationError):
        await svc.accept_rca_draft(session, p.id, AGENT)  # taslak yok
    p = await svc.set_rca_draft(session, p.id, rca, AI)
    assert p.rca_draft["root_cause"] == FIVE_WHY["root_cause"] and p.rca is None
    p = await svc.accept_rca_draft(session, p.id, AGENT)
    assert p.rca["method"] == "5_why" and p.rca_draft is None
    assert p.root_cause == FIVE_WHY["root_cause"]


async def test_rca_schema_validation():
    with pytest.raises(ValueError):
        RCA.model_validate({"method": "5_why", "steps": [{"answer": "sorusuz adım"}]})
    with pytest.raises(ValueError):
        RCA.model_validate({"method": "timeline", "steps": [{"answer": "zamansız olay"}]})
    RCA.model_validate(
        {"method": "timeline", "steps": [{"at": "2026-10-01T10:00:00+03:00", "answer": "Kesinti"}]}
    )


async def test_update_version_conflict(session):
    p = await _problem(session)
    await svc.update_problem(session, p.id, ProblemUpdate(title="Yeni başlık"), AGENT, expected_version=1)
    with pytest.raises(svc.ProblemVersionConflictError):
        await svc.update_problem(session, p.id, ProblemUpdate(title="Eski"), AGENT, expected_version=1)


async def test_link_and_unlink(session):
    ci_id, incs = await _vpn_cluster(session, 2)
    p = await _problem(session)
    p = await svc.link_incidents(session, p.id, [incs[0].id, incs[0].id], AGENT)
    p = await svc.link_incidents(session, p.id, [incs[1].id], AGENT)
    assert sorted(p.incident_ids) == sorted(i.id for i in incs) and p.version == 3
    p = await svc.link_cis(session, p.id, [ci_id], AGENT)
    p = await svc.unlink_incident(session, p.id, incs[0].id, AGENT)
    assert p.incident_ids == [incs[1].id] and p.affected_ci_ids == [ci_id]
    rows = await svc.list_problems(session, incident_id=incs[1].id)
    assert [r.id for r in rows] == [p.id]
    assert [r.id for r in await svc.list_problems(session, q="kopma")] == [p.id]
    assert await svc.list_problems(session, state=PS.CLOSED) == []


async def test_publish_known_error_creates_published_kedb_article(session):
    _, incs = await _vpn_cluster(session, 1)
    p = await _problem(session, incident_ids=[incs[0].id], workaround="İstemciyi yeniden başlatın")
    with pytest.raises(svc.ProblemForbiddenError):
        await svc.publish_known_error(session, p.id, AI)
    p = await svc.publish_known_error(session, p.id, APPROVER)
    art = await session.get(KnowledgeArticle, p.known_error_article_id)
    assert art.is_known_error and art.state == "published"
    assert art.workaround == "İstemciyi yeniden başlatın" and art.source_incident_ids == [incs[0].id]
    assert p.state == PS.KNOWN_ERROR
    # İkinci yayım aynı makaleyi günceller
    await svc.update_problem(session, p.id, ProblemUpdate(workaround="Yeni geçici çözüm"), AGENT)
    p = await svc.publish_known_error(session, p.id, APPROVER)
    assert p.known_error_article_id == art.id
    assert (await session.get(KnowledgeArticle, art.id)).workaround == "Yeni geçici çözüm"
    assert await session.scalar(select(func.count()).select_from(KnowledgeArticle)) == 1


async def test_publish_known_error_requires_workaround(session):
    p = await _problem(session)
    with pytest.raises(svc.ProblemValidationError):
        await svc.publish_known_error(session, p.id, APPROVER)


# --- ajan: eğilim tespiti ---------------------------------------------------------------------


async def test_detect_trends_ci_category_cluster(session):
    ci_id, incs = await _vpn_cluster(session, 3)
    other = await _ci(session, "dosya-srv")
    await _incident(session, "Dosya paylaşımı açılmıyor", category="altyapi_bulut", ci_id=other)
    await _incident(session, "Dosya sunucusu yavaş", category="altyapi_bulut", ci_id=other)
    clusters = await problem_agent.detect_trends(session, 30)
    ci_clusters = [c for c in clusters if c.kind == "ci_category"]
    assert len(ci_clusters) == 1  # dosya-srv yalnızca 2 olay: eşiğin altında
    c = ci_clusters[0]
    assert c.ci_id == ci_id and c.category == "ag" and c.size == 3
    assert sorted(c.incident_ids) == sorted(i.id for i in incs)
    assert 0 < c.confidence <= 1


async def test_detect_trends_respects_window_and_category(session):
    ci_id = await _ci(session)
    old = datetime.now(UTC) - timedelta(days=60)
    await _incident(session, "VPN kopuyor", ci_id=ci_id, created_at=old)
    await _incident(session, "VPN kopuyor yine", ci_id=ci_id)
    await _incident(session, "Teams açılmıyor", category="eposta_isbirligi", ci_id=ci_id)
    await _incident(session, "VPN gitti", ci_id=ci_id)
    assert [c for c in await problem_agent.detect_trends(session, 30) if c.kind == "ci_category"] == []
    assert len([c for c in await problem_agent.detect_trends(session, 90) if c.kind == "ci_category"]) == 1


async def test_detect_trends_near_duplicate_titles(session):
    a = await _incident(session, "Outlook e-posta gönderilemiyor", category=None)
    b = await _incident(session, "Outlook e-postası gönderilemiyor!", category=None)
    c = await _incident(session, "outlook: e-posta gönderilemiyor (acil)", category=None)
    await _incident(session, "Yazıcı kağıt sıkıştı", category=None)
    await _incident(session, "Muhasebe raporu açılmıyor", category=None)
    clusters = [c for c in await problem_agent.detect_trends(session, 30) if c.kind == "similar_title"]
    assert len(clusters) == 1
    assert sorted(clusters[0].incident_ids) == sorted([a.id, b.id, c.id])
    assert clusters[0].similarity >= problem_agent.TITLE_SIMILARITY


async def test_title_similarity_uses_knowledge_tokenizer():
    assert problem_agent.title_similarity("VPN bağlantısı kopuyor", "vpn BAĞLANTISI kopuyor!") == 1.0
    assert problem_agent.title_similarity("Yazıcı çalışmıyor", "VPN kopuyor") == 0.0
    assert problem_agent.title_similarity("", "VPN") == 0.0


async def test_detect_trends_skips_incidents_of_open_problems(session):
    _, incs = await _vpn_cluster(session, 3)
    await _problem(session, incident_ids=[incs[0].id])
    assert await problem_agent.detect_trends(session, 30) == []  # 2 olay kaldı: eşiğin altında


async def test_detect_trends_excludes_service_requests(session):
    ci_id = await _ci(session)
    for title in ("VPN erişim talebi", "VPN erişim talebi 2", "VPN erişim talebi 3"):
        inc = await _incident(session, title, ci_id=ci_id)
        inc.kind = "service_request"
    await session.flush()
    assert await problem_agent.detect_trends(session, 30) == []


# --- ajan: sorun önerisi (problem.create R1) --------------------------------------------------


async def test_propose_in_shadow_mode_only_opens_action_request(session):
    await _vpn_cluster(session, 3)
    results = await problem_agent.propose_problems(session, window_days=30, settings=SHADOW)
    await session.commit()
    assert [r.status for r in results] == [ToolStatus.PENDING_APPROVAL]
    assert await session.scalar(select(func.count()).select_from(Problem)) == 0
    dec = await session.get(AIDecision, results[0].decision_id)
    assert (dec.agent, dec.action, dec.outcome) == ("problem", "create", DecisionOutcome.SUGGESTED)
    ar = await session.get(ActionRequest, results[0].action_request_id)
    assert ar.tool == "problem.create" and ar.requested_by == "ai:problem" and ar.state == "pending"
    # Aynı küme yeniden önerilmez (idempotency + bekleyen istek)
    again = await problem_agent.propose_problems(session, window_days=30, settings=SHADOW)
    assert again == [] or all(r.replayed for r in again)
    assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 1
    # İnsan onaylar -> sorun onaylayanın kimliğiyle açılır, tespit eden ajan kalır
    ar = await actions.approve(session, ar.id, APPROVER, settings=SHADOW)
    assert ar.state == "executed", ar.error
    p = (await session.scalars(select(Problem))).one()
    assert p.created_by == "sef.ali" and p.detected_by == "ai:problem" and len(p.incident_ids) == 3


async def test_propose_live_auto_creates_problem_never_beyond_new(session):
    ci_id, _ = await _vpn_cluster(session, 3)
    results = await problem_agent.propose_problems(session, window_days=30, settings=LOOSE)
    assert [r.status for r in results] == [ToolStatus.EXECUTED]
    p = (await session.scalars(select(Problem))).one()
    assert p.state == PS.NEW and p.detected_by == "ai:problem" and p.affected_ci_ids == [ci_id]
    assert p.category == "ag"
    dec = await session.get(AIDecision, results[0].decision_id)
    assert dec.outcome == DecisionOutcome.AUTO_APPLIED and dec.output["applied"] is True


# --- ajan: RCA taslağı (LLM) ------------------------------------------------------------------


async def test_draft_rca_masks_incident_text_and_writes_draft_only(session):
    ci_id = await _ci(session)
    inc = await _incident(
        session, "VPN kopuyor", ci_id=ci_id,
        description="Kullanıcı ali@ornek.com, tel 0532 123 45 67, evden bağlanamıyor",
    )  # fmt: skip
    p = await _problem(session, incident_ids=[inc.id])
    await session.commit()
    llm = _rca_llm()
    llm.model_name = "test-model"
    r = await problem_agent.draft_rca(session, p.id, llm, settings=LIVE)
    assert r.status == ToolStatus.EXECUTED, r
    p = await svc.get_problem(session, p.id)
    assert p.rca_draft["root_cause"] == FIVE_WHY["root_cause"] and len(p.rca_draft["steps"]) == 3
    assert p.rca is None and p.root_cause is None and p.state == PS.NEW
    prompt = llm.calls[0]["user"]
    assert inc.number in prompt and "ali@ornek.com" not in prompt and "0532" not in prompt
    dec = await session.get(AIDecision, r.decision_id)
    assert (dec.agent, dec.action, dec.model) == ("problem", "record_rca", "test-model")


async def test_draft_rca_shadow_mode_needs_approval(session):
    _, incs = await _vpn_cluster(session, 1)
    p = await _problem(session, incident_ids=[incs[0].id])
    r = await problem_agent.draft_rca(session, p.id, _rca_llm(), settings=SHADOW)
    assert r.status == ToolStatus.PENDING_APPROVAL
    assert (await svc.get_problem(session, p.id)).rca_draft is None
    ar = await actions.approve(session, r.action_request_id, APPROVER, settings=SHADOW)
    assert ar.state == "executed", ar.error
    p = await svc.get_problem(session, p.id)
    assert p.rca_draft is not None and p.rca is None


async def test_draft_rca_low_confidence_escalates(session):
    p = await _problem(session)
    r = await problem_agent.draft_rca(session, p.id, _rca_llm(confidence=0.2), settings=LIVE)
    assert r.status == ToolStatus.PENDING_APPROVAL
    dec = await session.get(AIDecision, r.decision_id)
    assert dec.outcome == DecisionOutcome.ESCALATED


async def test_draft_rca_invalid_llm_output_writes_nothing(session):
    p = await _problem(session)
    with pytest.raises(problem_agent.RCADraftError):
        await problem_agent.draft_rca(session, p.id, FakeLLM([{"method": "5_why"}]), settings=LIVE)
    assert (await svc.get_problem(session, p.id)).rca_draft is None
    assert await session.scalar(select(func.count()).select_from(AIDecision)) == 0


async def test_deterministic_fake_llm_answers_rca_prompt(session):
    _, incs = await _vpn_cluster(session, 2)
    p = await _problem(session, incident_ids=[i.id for i in incs])
    r = await problem_agent.draft_rca(session, p.id, DeterministicFakeLLM(), settings=LIVE)
    assert r.status == ToolStatus.EXECUTED, r
    draft = (await svc.get_problem(session, p.id)).rca_draft
    assert draft["method"] == "5_why" and len(draft["steps"]) == 5 and draft["root_cause"]


# --- ajan: kalıcı çözüm (taslak RFC) ----------------------------------------------------------


async def test_propose_fix_creates_linked_draft_rfc(session):
    ci_id, incs = await _vpn_cluster(session, 2)
    p = await _problem(session, incident_ids=[i.id for i in incs], ci_ids=[ci_id])
    await svc.record_rca(session, p.id, RCA.model_validate(FIVE_WHY), AGENT)
    r = await problem_agent.propose_fix(session, p.id, settings=LIVE)
    assert r.status == ToolStatus.EXECUTED, r
    change = await session.get(ChangeRequest, r.result["change"]["id"])
    assert change.state == ChangeState.DRAFT and change.requested_by == "ai:problem"
    assert sorted(change.linked_incident_ids) == sorted(i.id for i in incs)
    assert change.affected_ci_ids == [ci_id] and p.number in change.title
    assert FIVE_WHY["root_cause"] in change.description
    p = await svc.get_problem(session, p.id)
    assert p.change_ids == [change.id] and p.state == PS.NEW


async def test_propose_fix_without_root_cause_is_low_confidence(session):
    p = await _problem(session)
    r = await problem_agent.propose_fix(session, p.id, settings=LIVE)
    assert r.status == ToolStatus.PENDING_APPROVAL
    assert await session.scalar(select(func.count()).select_from(ChangeRequest)) == 0


# --- araçlar -------------------------------------------------------------------------------------

PROBLEM_TOOLS = {
    "problem.search": RiskClass.R0,
    "problem.detect_trends": RiskClass.R0,
    "problem.create": RiskClass.R1,
    "problem.link_incidents": RiskClass.R1,
    "problem.record_rca": RiskClass.R1,
    "problem.propose_fix": RiskClass.R1,
    "problem.close": RiskClass.R2,
    "known_error.publish_workaround": RiskClass.R2,
}


def test_problem_tools_registered_with_risk_and_policy():
    for name, risk in PROBLEM_TOOLS.items():
        spec = registry.get(name)
        assert spec.risk_class == risk, name
        if risk != RiskClass.R0:
            assert (spec.domain, spec.verb) in POLICIES, name


async def test_problem_tools_read_for_ai(session):
    await _vpn_cluster(session, 3)
    p = await _problem(session, title="VPN kopmaları")
    r = await execute_tool(session, AI, "problem.search", {"q": "VPN"})
    assert r.status == ToolStatus.EXECUTED and [i["id"] for i in r.result["items"]] == [p.id]
    r = await execute_tool(session, AI, "problem.detect_trends", {"window_days": 30})
    assert r.status == ToolStatus.EXECUTED and r.result["clusters"][0]["size"] == 3


async def test_problem_close_tool_requires_approval_and_runs_as_approver(session):
    p = await _problem(session)
    r = await execute_tool(
        session, AI, "problem.close", {"problem_id": p.id, "resolution": "Yinelenen kayıt"},
        confidence=1.0, settings=LOOSE,
    )  # fmt: skip
    assert r.status == ToolStatus.PENDING_APPROVAL
    assert (await svc.get_problem(session, p.id)).state == PS.NEW
    ar = await actions.approve(session, r.action_request_id, APPROVER, settings=LOOSE)
    assert ar.state == "executed", ar.error
    p = await svc.get_problem(session, p.id)
    assert p.state == PS.CLOSED
    actors = (
        await session.scalars(select(AuditLog.actor).where(AuditLog.action == "problem.transition"))
    ).all()
    assert actors == ["sef.ali"]


async def test_publish_workaround_tool_requires_approval(session):
    p = await _problem(session, workaround="İstemciyi yeniden başlatın")
    r = await execute_tool(
        session, AGENT, "known_error.publish_workaround", {"problem_id": p.id}
    )
    assert r.status == ToolStatus.PENDING_APPROVAL  # insan çağırsa da R2
    assert await session.scalar(select(func.count()).select_from(KnowledgeArticle)) == 0
    ar = await actions.approve(session, r.action_request_id, APPROVER)
    assert ar.state == "executed", ar.error
    assert (await svc.get_problem(session, p.id)).known_error_article_id


async def test_problem_tools_unknown_problem_is_404_before_request(session):
    from serviszeka.tools import ToolNotFoundError

    with pytest.raises(ToolNotFoundError):
        await execute_tool(session, AI, "problem.close", {"problem_id": "yok", "resolution": "x"})
    assert await session.scalar(select(func.count()).select_from(ActionRequest)) == 0


async def test_link_incidents_and_record_rca_tools_for_human(session):
    _, incs = await _vpn_cluster(session, 1)
    p = await _problem(session)
    r = await execute_tool(
        session, AGENT, "problem.link_incidents", {"problem_id": p.id, "incident_ids": [incs[0].id]}
    )
    assert r.status == ToolStatus.EXECUTED and r.result["incident_ids"] == [incs[0].id]
    r = await execute_tool(session, AGENT, "problem.record_rca", {"problem_id": p.id, "rca": FIVE_WHY})
    assert r.status == ToolStatus.EXECUTED
    p = await svc.get_problem(session, p.id)
    # Araç yalnızca taslağa yazar; kesin RCA insan kabulüyle
    assert p.rca_draft["method"] == "5_why" and p.rca is None


async def test_record_rca_tool_without_content_drafts_with_llm(session):
    _, incs = await _vpn_cluster(session, 1)
    p = await _problem(session, incident_ids=[incs[0].id])
    llm = _rca_llm()
    r = await execute_tool(session, AGENT, "problem.record_rca", {"problem_id": p.id}, llm=llm)
    assert r.status == ToolStatus.EXECUTED, r
    assert len(llm.calls) == 1
    assert (await svc.get_problem(session, p.id)).rca_draft["root_cause"] == FIVE_WHY["root_cause"]


# --- API -----------------------------------------------------------------------------------------


@pytest.fixture
async def client(session) -> AsyncIterator[httpx.AsyncClient]:
    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[problems_api.get_problem_settings] = lambda: LIVE
    app.dependency_overrides[problems_api.get_problem_llm] = lambda: _rca_llm()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


REQUESTER = dev_headers("son.kullanici", "requester")
AI_TOKEN = dev_headers("copilot", "ai_agent")


async def test_api_requester_and_ai_forbidden(client, session):
    p = await _problem(session)
    await session.commit()
    for headers in (REQUESTER, AI_TOKEN):
        assert (await client.get("/problems", headers=headers)).status_code == 403
        assert (await client.get(f"/problems/{p.id}", headers=headers)).status_code == 403
        r = await client.post("/problems", json={"title": "x"}, headers=headers)
        assert r.status_code == 403


async def test_api_crud_and_transitions(client, session):
    _, incs = await _vpn_cluster(session, 2)
    r = await client.post(
        "/problems",
        json={"title": "VPN kopmaları", "priority": "P2", "incident_ids": [incs[0].id]},
        headers=H_AGENT,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["id"]
    assert body["number"] == "PRB0000001" and body["state"] == "new" and r.headers["ETag"] == '"1"'
    r = await client.post(f"/problems/{pid}/incidents", json={"incident_ids": [incs[1].id]}, headers=H_APPROVER)
    assert r.status_code == 200 and len(r.json()["incident_ids"]) == 2
    r = await client.patch(f"/problems/{pid}", json={"workaround": "Yeniden başlat"}, headers={**H_AGENT, "If-Match": '"1"'})
    assert r.status_code == 409
    r = await client.patch(f"/problems/{pid}", json={"workaround": "Yeniden başlat"}, headers=H_AGENT)
    assert r.status_code == 200 and r.json()["workaround"] == "Yeniden başlat"
    r = await client.post(f"/problems/{pid}/transition", json={"to_state": "resolved"}, headers=H_AGENT)
    assert r.status_code == 422
    r = await client.post(f"/problems/{pid}/transition", json={"to_state": "investigating"}, headers=H_AGENT)
    assert r.status_code == 200 and r.json()["state"] == "investigating"
    r = await client.get("/problems", params={"state": "investigating", "q": "VPN"}, headers=H_APPROVER)
    assert [p["id"] for p in r.json()] == [pid]
    r = await client.get("/problems", params={"incident_id": incs[1].id}, headers=H_AGENT)
    assert [p["id"] for p in r.json()] == [pid]
    assert (await client.get("/problems/yok", headers=H_AGENT)).status_code == 404
    r = await client.delete(f"/problems/{pid}/incidents/{incs[0].id}", headers=H_AGENT)
    assert r.status_code == 200 and r.json()["incident_ids"] == [incs[1].id]


async def test_api_rca_draft_accept_and_known_error(client, session):
    _, incs = await _vpn_cluster(session, 1)
    p = await _problem(session, incident_ids=[incs[0].id], workaround="İstemciyi yeniden başlat")
    await session.commit()
    r = await client.post(f"/problems/{p.id}/rca/draft", headers=H_AGENT)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "executed"
    r = await client.get(f"/problems/{p.id}", headers=H_AGENT)
    assert r.json()["rca_draft"]["root_cause"] == FIVE_WHY["root_cause"] and r.json()["rca"] is None
    r = await client.post(f"/problems/{p.id}/rca/accept", headers=H_APPROVER)
    assert r.status_code == 200 and r.json()["root_cause"] == FIVE_WHY["root_cause"]
    r = await client.put(f"/problems/{p.id}/rca", json={"rca": {"method": "5_why", "steps": []}}, headers=H_AGENT)
    assert r.status_code == 422
    r = await client.post(f"/problems/{p.id}/known-error/publish", headers=H_APPROVER)
    assert r.status_code == 200 and r.json()["state"] == "known_error"
    art = await session.get(KnowledgeArticle, r.json()["known_error_article_id"])
    assert art.state == "published"


async def test_api_trends_detect_propose_and_fix(client, session):
    await _vpn_cluster(session, 3)
    r = await client.post("/problems/trends", json={"window_days": 30}, headers=H_AGENT)
    assert r.status_code == 200, r.text
    clusters = r.json()["clusters"]
    assert clusters[0]["kind"] == "ci_category" and clusters[0]["size"] == 3
    assert clusters[0]["incident_numbers"]
    assert await session.scalar(select(func.count()).select_from(Problem)) == 0  # salt okuma
    r = await client.post("/problems/trends/propose", json={"window_days": 30}, headers=H_AGENT)
    assert r.status_code == 200, r.text
    assert [x["status"] for x in r.json()["results"]] == ["executed"]
    pid = r.json()["results"][0]["result"]["id"]
    r = await client.post(f"/problems/{pid}/fix", json={"backout_plan": "Eski ayara dön"}, headers=H_AGENT)
    assert r.status_code == 200, r.text
    # Kök neden yokken güven düşük: insan onayına düşer
    assert r.json()["status"] == "pending_approval" and r.json()["action_request_id"]


async def test_api_routes_have_auth(client):
    for method, path in (("GET", "/problems"), ("POST", "/problems/trends")):
        r = await client.request(method, path)
        assert r.status_code == 401


async def test_api_ai_draft_never_resolves(client, session, monkeypatch):
    p = await _problem(session)
    await session.commit()
    app.dependency_overrides[problems_api.get_problem_settings] = lambda: LOOSE
    await client.post(f"/problems/{p.id}/rca/draft", headers=H_AGENT)
    await client.post(f"/problems/{p.id}/fix", json={}, headers=H_AGENT)
    r = await client.get(f"/problems/{p.id}", headers=H_AGENT)
    assert r.json()["state"] == "new" and r.json()["rca"] is None


def test_requester_only_user_is_not_problem_user():
    user = CurrentUser("x", frozenset({"requester"}))
    assert not user.has_any("agent", "approver")

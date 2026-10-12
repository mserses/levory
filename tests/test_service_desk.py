"""Hizmet Masası ajanı testleri (FakeLLM ile, ağ çağrısı yok)."""

import pytest
from sqlalchemy import select

from serviszeka import audit, knowledge
from serviszeka.agents import service_desk
from serviszeka.config import Settings
from serviszeka.domain.enums import DecisionOutcome, IncidentStatus, Priority
from serviszeka.llm import FakeLLM, LLMError
from serviszeka.models import AIDecision, AuditLog
from serviszeka.services import incidents as svc
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate

LIVE = Settings(ai_shadow_mode=False, anthropic_api_key=None)
SHADOW = Settings(ai_shadow_mode=True, anthropic_api_key=None)


async def _setup(session):
    vpn = await knowledge.create_article(
        session,
        title="VPN bağlantı sorunu",
        body="VPN istemcisi bağlanmıyorsa sertifikayı yenileyin ve FortiClient'ı yeniden başlatın.",
        category="network",
        is_known_error=True,
        workaround="Sertifika yenileme",
    )
    await knowledge.create_article(
        session, title="Yazıcı kağıt sıkışması", body="Tepsiyi açıp kağıdı çıkarın."
    )
    inc = await svc.create_incident(
        session,
        IncidentCreate(
            title="VPN bağlanamıyorum",
            description="Evden VPN bağlantısı kurulamıyor. TCKN 10000000146, "
            "e-posta ayse.yilmaz@ornek.com.tr, tel 0532 123 45 67",
            caller="ayse",
        ),
        actor="ayse",
    )
    return vpn, inc


def _response(**kw):
    data = {
        "kind": "incident",
        "category": "network",
        "service": "VPN",
        "impact": "medium",
        "urgency": "high",
        "assignment_group": "Ağ Ekibi",
        "suggested_resolution": "Sertifikayı yenileyin.",
        "kb_article_ids": [],
        "confidence": 0.92,
        "rationale": "VPN anahtar kelimesi ve bilinen hata kaydı.",
    }
    data.update(kw)
    return data


async def test_auto_apply_when_shadow_off(session):
    vpn, inc = await _setup(session)
    llm = FakeLLM([_response(kb_article_ids=[vpn.id])])
    decision = await service_desk.triage(session, inc, llm, settings=LIVE)

    assert decision.outcome == DecisionOutcome.AUTO_APPLIED
    assert decision.sources == [vpn.id]
    assert decision.model == LIVE.llm_fast_model
    assert llm.calls[0]["model"] == LIVE.llm_fast_model
    assert decision.output["applied"] is True
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.category == "network" and refreshed.service == "VPN"
    assert refreshed.assignment_group == "Ağ Ekibi"
    assert refreshed.priority == Priority.P2  # medium x high
    assert refreshed.ai_confidence == pytest.approx(0.92)
    assert refreshed.version == 2
    # Çözüm metni asla otomatik yazılmaz
    assert refreshed.resolution is None and refreshed.status == IncidentStatus.NEW

    logs = (await session.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
    upd = [log for log in logs if log.action == "incident.update"]
    assert upd and upd[-1].actor == "ai:service_desk"
    assert logs[-1].action == "ai_decision.triage" and logs[-1].entity_id == decision.id
    assert await audit.verify_chain(session)


async def test_shadow_mode_suggests_and_leaves_incident(session):
    vpn, inc = await _setup(session)
    before = (inc.category, inc.priority, inc.version, inc.assignment_group)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(kb_article_ids=[vpn.number])]), settings=SHADOW
    )
    assert decision.outcome == DecisionOutcome.SUGGESTED
    assert decision.sources == [vpn.id]  # numara ile atıf da kabul edilir
    refreshed = await svc.get_incident(session, inc.id)
    assert (
        refreshed.category,
        refreshed.priority,
        refreshed.version,
        refreshed.assignment_group,
    ) == before
    assert refreshed.ai_confidence is None


async def test_low_confidence_escalates(session):
    _, inc = await _setup(session)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(confidence=0.4)]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.ESCALATED
    assert (await svc.get_incident(session, inc.id)).version == 1


async def test_hallucinated_kb_id_dropped_and_confidence_lowered(session):
    vpn, inc = await _setup(session)
    decision = await service_desk.triage(
        session,
        inc,
        FakeLLM([_response(kb_article_ids=[vpn.id, "KB9999999"], confidence=0.9)]),
        settings=LIVE,
    )
    assert decision.sources == [vpn.id]
    assert decision.output["dropped_kb_ids"] == ["KB9999999"]
    assert decision.output["llm_confidence"] == pytest.approx(0.9)
    assert decision.confidence == pytest.approx(0.7)
    assert decision.outcome == DecisionOutcome.ESCALATED  # 0.7 < 0.75


async def test_invalid_output_escalates_with_zero_confidence(session):
    _, inc = await _setup(session)
    decision = await service_desk.triage(
        session, inc, FakeLLM([{"kind": "uçan daire", "confidence": 3}]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.ESCALATED
    assert decision.confidence == 0.0
    assert decision.output["error"] == "invalid_output"


async def test_llm_error_escalates(session):
    _, inc = await _setup(session)

    def boom(system, user):
        raise LLMError("zaman aşımı")

    decision = await service_desk.triage(session, inc, FakeLLM(boom), settings=LIVE)
    assert decision.outcome == DecisionOutcome.ESCALATED and decision.confidence == 0.0


async def test_pii_masked_in_llm_input_and_decision(session):
    _, inc = await _setup(session)
    llm = FakeLLM([_response()])
    decision = await service_desk.triage(session, inc, llm, settings=SHADOW)
    sent = llm.calls[0]["user"]
    for raw in ("10000000146", "ayse.yilmaz@ornek.com.tr", "0532 123 45 67"):
        assert raw not in sent
        assert raw not in decision.input_summary
    assert "[TCKN]" in sent and "[EPOSTA]" in sent and "[TELEFON]" in sent
    assert "[TCKN]" in decision.input_summary
    # Bilgi bankası sonuçları isteme eklendi
    assert "VPN bağlantı sorunu" in sent


async def test_p1_requires_approval_then_approve(session):
    _, inc = await _setup(session)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(impact="high", urgency="high")]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P5

    approved = await service_desk.approve_decision(session, decision.id, "sef.ali")
    assert approved.approved_by == "sef.ali" and approved.approved_at is not None
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.priority == Priority.P1 and refreshed.category == "network"
    last = await session.scalar(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1))
    assert last.action == "ai_decision.approve" and last.actor == "sef.ali"

    with pytest.raises(service_desk.DecisionError):
        await service_desk.approve_decision(session, decision.id, "baska")


async def test_cannot_approve_escalated_or_auto_applied(session):
    _, inc = await _setup(session)
    esc = await service_desk.triage(
        session, inc, FakeLLM([_response(confidence=0.1)]), settings=LIVE
    )
    with pytest.raises(service_desk.DecisionError):
        await service_desk.approve_decision(session, esc.id, "x")
    with pytest.raises(service_desk.DecisionError):
        await service_desk.approve_decision(session, "yok", "x")


async def test_concurrent_human_edit_downgrades_to_suggested(session):
    _, inc = await _setup(session)

    async def edit_then_answer(system, user):
        await svc.update_incident(session, inc.id, IncidentUpdate(category="insan"), "insan")
        return _response()

    decision = await service_desk.triage(session, inc, FakeLLM(edit_then_answer), settings=LIVE)
    assert decision.outcome == DecisionOutcome.SUGGESTED
    assert (await svc.get_incident(session, inc.id)).category == "insan"


async def test_decisions_listed(session):
    _, inc = await _setup(session)
    await service_desk.triage(session, inc, FakeLLM([_response()]), settings=SHADOW)
    rows = await service_desk.list_decisions(session, inc.id)
    assert len(rows) == 1 and isinstance(rows[0], AIDecision)

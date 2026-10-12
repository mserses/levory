"""Hizmet Masası ajanı testleri (FakeLLM ile, ağ çağrısı yok)."""

import pytest
from sqlalchemy import select

from serviszeka import audit, knowledge, notifications
from serviszeka.agents import service_desk
from serviszeka.config import Settings
from serviszeka.domain.enums import DecisionOutcome, IncidentStatus, Priority
from serviszeka.llm import FakeLLM, LLMError
from serviszeka.models import AIDecision, AuditLog
from serviszeka.services import incidents as svc
from serviszeka.services.schemas import IncidentCreate, IncidentUpdate

LIVE = Settings(ai_shadow_mode=False, anthropic_api_key=None)
SHADOW = Settings(ai_shadow_mode=True, anthropic_api_key=None)


class FakeNotifier:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def notify(self, event, payload):
        self.calls.append((event, payload))
        if self.fail:
            raise RuntimeError("webhook kapalı")


async def _triage_and_notify(session, inc, llm, *, settings, notifier):
    """Triage + commit + outbox gönderimi (API akışıyla aynı sıra)."""
    decision = await service_desk.triage(session, inc, llm, settings=settings)
    assert notifier.calls == []  # commit öncesi nöbetçiye gitmez
    await session.commit()
    await notifications.send_pending(session, notifier)
    return decision


async def _setup(session):
    vpn = await knowledge.create_article(
        session,
        title="VPN bağlantı sorunu",
        body="VPN istemcisi bağlanmıyorsa sertifikayı yenileyin ve FortiClient'ı yeniden başlatın.",
        category="ag",
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
        "category": "ag",
        "service": "vpn",
        "impact": "medium",
        "urgency": "medium",
        "assignment_group": "network_ops",
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
    assert refreshed.category == "ag" and refreshed.service == "vpn"
    assert refreshed.assignment_group == "network_ops"
    assert refreshed.priority == Priority.P3  # medium x medium: P5 -> P3 bant içinde
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
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="high", urgency="high")]),
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P5
    assert len(notifier.calls) == 1

    approved = await service_desk.approve_decision(session, decision.id, "sef.ali")
    assert approved.approved_by == "sef.ali" and approved.approved_at is not None
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.priority == Priority.P1 and refreshed.category == "ag"
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


# --- öncelik kuralları ---------------------------------------------------------


async def _incident(session, impact="low", urgency="low"):
    return await svc.create_incident(
        session,
        IncidentCreate(title="Ağ yavaş", caller="ali", impact=impact, urgency=urgency),
        actor="ali",
    )


async def _audit_actions(session):
    logs = (await session.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
    return [log.action for log in logs]


async def test_raise_to_p2_applies_classification_but_waits_for_priority(session):
    inc = await _incident(session)  # P5
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="medium", urgency="high")]),  # P2
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert "P2" in decision.output["policy_reason"]
    refreshed = await svc.get_incident(session, inc.id)
    # Öncelik dışı alanlar politika gereği uygulandı, öncelik onay bekliyor
    assert refreshed.category == "ag" and refreshed.assignment_group == "network_ops"
    assert refreshed.priority == Priority.P5
    assert decision.output["applied"] is False
    assert set(decision.output["applied_fields"]) == {
        "kind",
        "category",
        "service",
        "assignment_group",
    }
    assert set(decision.output["pending_fields"]) == {"impact", "urgency"}
    # Tüm öneri karar üzerinde duruyor
    assert decision.output["proposed"]["urgency"] == "high"

    # Nöbetçi anında uyarıldı
    assert len(notifier.calls) == 1
    event, payload = notifier.calls[0]
    assert event == "incident.priority_raise_pending"
    assert payload["incident_number"] == inc.number
    assert payload["current_priority"] == "P5" and payload["proposed_priority"] == "P2"
    assert payload["decision_id"] == decision.id
    assert "notification.sent" in await _audit_actions(session)

    await service_desk.approve_decision(session, decision.id, "sef.ali")
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P2
    assert await audit.verify_chain(session)


async def test_raise_to_p1_in_shadow_mode_needs_approval_and_notifies(session):
    inc = await _incident(session)
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="high", urgency="high")]),
        settings=SHADOW,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.version == 1 and refreshed.category is None
    assert [e for e, _ in notifier.calls] == ["incident.priority_raise_pending"]


async def test_low_confidence_p1_raise_escalates_and_still_notifies(session):
    inc = await _incident(session)
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="high", urgency="high", confidence=0.3)]),
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.ESCALATED
    assert (await svc.get_incident(session, inc.id)).version == 1
    assert len(notifier.calls) == 1


async def test_downgrade_within_band_needs_approval(session):
    inc = await _incident(session, impact="medium", urgency="medium")  # P3
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="low", urgency="medium")]),  # P4
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    refreshed = await svc.get_incident(session, inc.id)
    assert refreshed.priority == Priority.P3 and refreshed.category == "ag"
    assert notifier.calls == []  # düşürme nöbetçiye bildirilmez

    await service_desk.approve_decision(session, decision.id, "sef.ali")
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P4


async def test_downgrade_from_p2_needs_approval(session):
    inc = await _incident(session, impact="medium", urgency="high")  # P2
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="medium", urgency="medium")]),  # P3
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P2
    assert notifier.calls == []


async def test_raise_within_band_auto_applied_without_notification(session):
    inc = await _incident(session, impact="low", urgency="medium")  # P4
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="medium", urgency="medium")]),  # P3
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.AUTO_APPLIED
    assert decision.output["applied"] is True
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P3
    assert notifier.calls == []


async def test_notification_failure_does_not_break_triage(session):
    inc = await _incident(session)
    notifier = FakeNotifier(fail=True)
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="high", urgency="high")]),
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    assert len(notifier.calls) == 1
    logs = (await session.scalars(select(AuditLog).order_by(AuditLog.seq))).all()
    failed = [log for log in logs if log.action == "notification.failed"]
    assert failed and failed[0].entity_id == decision.id
    assert "webhook kapalı" in failed[0].details["error"]
    assert await audit.verify_chain(session)


async def test_default_notifier_used_when_not_given(session, caplog):
    inc = await _incident(session)
    decision = await service_desk.triage(
        session, inc, FakeLLM([_response(impact="high", urgency="high")]), settings=LIVE
    )
    assert decision.outcome == DecisionOutcome.NEEDS_APPROVAL
    await session.commit()
    # Webhook tanımlı değilse varsayılan bildirici loglar
    await notifications.send_pending(session, notifications.get_notifier(LIVE))
    assert "notification.sent" in await _audit_actions(session)


async def test_no_oncall_page_when_priority_fields_unchanged(session):
    # Uzak sistem önceliği ezmiş (P3) ama etki/aciliyet zaten yüksek/yüksek: AI aynı
    # etki/aciliyeti önerirse onay bekleyen bir yükseltme yoktur, nöbetçi uyarılmamalı
    inc = await _incident(session, impact="high", urgency="high")
    inc.priority = Priority.P3
    await session.flush()
    notifier = FakeNotifier()
    decision = await _triage_and_notify(
        session,
        inc,
        FakeLLM([_response(impact="high", urgency="high")]),
        settings=LIVE,
        notifier=notifier,
    )
    assert decision.outcome == DecisionOutcome.AUTO_APPLIED
    assert decision.output["pending_fields"] == []
    assert (await svc.get_incident(session, inc.id)).priority == Priority.P3
    assert notifier.calls == []


async def test_off_taxonomy_values_dropped_and_confidence_lowered(session):
    _, inc = await _setup(session)
    llm = FakeLLM([_response(category="Network", assignment_group="Ağ Ekibi")])
    decision = await service_desk.triage(session, inc, llm, settings=LIVE)
    assert decision.output["off_taxonomy"] == {
        "category": "Network",
        "assignment_group": "Ağ Ekibi",
    }
    assert decision.output["category"] is None and decision.output["assignment_group"] is None
    assert decision.confidence == pytest.approx(0.92 - 2 * service_desk.OFF_TAXONOMY_PENALTY)
    # Eşik altına düştüğü için uygulanmaz, insana gider
    assert decision.outcome == DecisionOutcome.ESCALATED


async def test_system_prompt_lists_taxonomy(session):
    _, inc = await _setup(session)
    llm = FakeLLM([_response()])
    await service_desk.triage(session, inc, llm, settings=LIVE)
    system = llm.calls[0]["system"]
    assert "security_ops" in system and "guvenlik" in system and "{taxonomy}" not in system

"""Politika motoru testleri."""

import pytest

from serviszeka.config import Settings
from serviszeka.domain.enums import DecisionOutcome, RiskLevel
from serviszeka.policy import POLICIES, evaluate

LIVE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.75)
SHADOW = Settings(ai_shadow_mode=True, ai_confidence_threshold=0.75)


def test_unknown_action_rejected():
    d = evaluate(agent="service_desk", action="delete_db", confidence=0.99, settings=LIVE)
    assert d.outcome == DecisionOutcome.REJECTED
    assert d.reason


def test_unknown_beats_low_confidence():
    d = evaluate(agent="x", action="y", confidence=0.1, settings=LIVE)
    assert d.outcome == DecisionOutcome.REJECTED


def test_low_confidence_escalated():
    d = evaluate(agent="service_desk", action="classify", confidence=0.5, settings=LIVE)
    assert d.outcome == DecisionOutcome.ESCALATED


def test_threshold_boundary_is_inclusive():
    d = evaluate(agent="service_desk", action="classify", confidence=0.75, settings=LIVE)
    assert d.outcome == DecisionOutcome.AUTO_APPLIED


@pytest.mark.parametrize("risk", [RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_high_risk_needs_approval(risk):
    d = evaluate(
        agent="service_desk", action="classify", confidence=0.99, risk=risk, settings=LIVE
    )
    assert d.outcome == DecisionOutcome.NEEDS_APPROVAL


@pytest.mark.parametrize("action", [("change", "execute"), ("incident", "resolve")])
def test_never_auto_actions(action):
    agent, act = action
    d = evaluate(agent=agent, action=act, confidence=1.0, settings=LIVE)
    assert d.outcome == DecisionOutcome.NEEDS_APPROVAL


def test_risk_above_action_max_needs_approval():
    d = evaluate(
        agent="service_desk",
        action="set_priority",
        confidence=0.9,
        risk=RiskLevel.MEDIUM,
        settings=LIVE,
    )
    assert d.outcome == DecisionOutcome.NEEDS_APPROVAL


def test_shadow_mode_suggests():
    d = evaluate(agent="service_desk", action="classify", confidence=0.9, settings=SHADOW)
    assert d.outcome == DecisionOutcome.SUGGESTED


@pytest.mark.parametrize(
    "agent,action",
    [
        ("service_desk", "classify"),
        ("service_desk", "suggest_resolution"),
        ("service_desk", "set_priority"),
        ("knowledge", "draft_article"),
    ],
)
def test_auto_applied_when_live(agent, action):
    d = evaluate(agent=agent, action=action, confidence=0.9, settings=LIVE)
    assert d.outcome == DecisionOutcome.AUTO_APPLIED


def test_required_actions_present():
    for key in [
        ("service_desk", "classify"),
        ("service_desk", "suggest_resolution"),
        ("service_desk", "set_priority"),
        ("knowledge", "draft_article"),
        ("incident", "resolve"),
        ("change", "execute"),
    ]:
        assert key in POLICIES
    assert POLICIES[("change", "execute")].max_auto_risk is None


def test_default_settings_used(monkeypatch):
    monkeypatch.setenv("SZ_AI_SHADOW_MODE", "true")
    from serviszeka.config import get_settings

    get_settings.cache_clear()
    try:
        d = evaluate(agent="knowledge", action="draft_article", confidence=0.95)
        assert d.outcome == DecisionOutcome.SUGGESTED
    finally:
        get_settings.cache_clear()

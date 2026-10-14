"""Politika motoru testleri."""

import pytest

from serviszeka.config import Settings
from serviszeka.domain.enums import DecisionOutcome, Priority, RiskLevel
from serviszeka.policy import POLICIES, evaluate, is_urgent_raise, priority_change_risk

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


# --- öncelik değişikliği kuralları ------------------------------------------


@pytest.mark.parametrize(
    "current,proposed",
    [("P5", "P3"), ("P4", "P3"), ("P5", "P4"), ("P3", "P3"), ("P5", "P5")],
)
def test_priority_change_within_p3_p5_is_low_risk(current, proposed):
    risk = priority_change_risk(Priority(current), Priority(proposed))
    assert risk == RiskLevel.LOW
    d = evaluate(
        agent="service_desk", action="set_priority", confidence=0.9, risk=risk, settings=LIVE
    )
    assert d.outcome == DecisionOutcome.AUTO_APPLIED


@pytest.mark.parametrize(
    "current,proposed",
    [
        ("P5", "P2"),  # P2'ye yükseltme
        ("P3", "P1"),  # P1'e yükseltme
        ("P2", "P1"),
        ("P2", "P3"),  # düşürme
        ("P3", "P4"),  # bant içinde düşürme
        ("P1", "P5"),
        ("P2", "P2"),  # mevcut öncelik bant dışında
        ("P1", "P1"),
    ],
)
def test_priority_change_outside_rules_needs_approval(current, proposed):
    risk = priority_change_risk(Priority(current), Priority(proposed))
    for settings in (LIVE, SHADOW):
        d = evaluate(
            agent="service_desk",
            action="set_priority",
            confidence=0.99,
            risk=risk,
            settings=settings,
        )
        assert d.outcome == DecisionOutcome.NEEDS_APPROVAL


@pytest.mark.parametrize(
    "current,proposed,expected",
    [
        ("P5", "P2", True),
        ("P3", "P1", True),
        ("P2", "P1", True),
        ("P2", "P2", False),
        ("P1", "P1", False),
        ("P5", "P3", False),
        ("P1", "P3", False),
    ],
)
def test_is_urgent_raise(current, proposed, expected):
    assert is_urgent_raise(Priority(current), Priority(proposed)) is expected

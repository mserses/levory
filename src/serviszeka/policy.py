"""Politika motoru: yapay zekânın neyi kendi başına yapabileceğine karar verir."""

import math
from dataclasses import dataclass

from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import DecisionOutcome, Priority, RiskLevel

_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


@dataclass(frozen=True)
class ActionPolicy:
    # Otomatik uygulamaya izin verilen en yüksek risk; None = asla otomatik değil.
    max_auto_risk: RiskLevel | None
    description: str = ""


# İzin listesi: (ajan, eylem) -> politika. Listede olmayan eylem reddedilir.
POLICIES: dict[tuple[str, str], ActionPolicy] = {
    ("service_desk", "classify"): ActionPolicy(RiskLevel.MEDIUM, "Kategori/hizmet sınıflandırma"),
    ("service_desk", "suggest_resolution"): ActionPolicy(RiskLevel.MEDIUM, "Çözüm önerisi"),
    # Risk priority_change_risk ile belirlenir: yalnızca P3..P5 içi, düşürme olmayan değişiklik
    ("service_desk", "set_priority"): ActionPolicy(
        RiskLevel.LOW, "Etki/aciliyet ve öncelik (yalnızca P3..P5 içinde, düşürme hariç)"
    ),
    ("knowledge", "draft_article"): ActionPolicy(RiskLevel.LOW, "Bilgi makalesi taslağı"),
    ("incident", "resolve"): ActionPolicy(None, "Olayı çözüldü olarak kapatma"),
    ("change", "execute"): ActionPolicy(None, "Değişikliği uygulama (asla otomatik)"),
}


# Yapay zekânın kendi başına değiştirebileceği öncelik bandı
AUTO_PRIORITY_BAND: frozenset[Priority] = frozenset({Priority.P3, Priority.P4, Priority.P5})
# Bu önceliklere yükseltme anında nöbetçiye bildirilir
URGENT_PRIORITIES: frozenset[Priority] = frozenset({Priority.P1, Priority.P2})


def _rank(priority: Priority) -> int:
    # P1 -> 1 (en yüksek öncelik), P5 -> 5
    return int(Priority(priority).value[1:])


def is_downgrade(current: Priority, proposed: Priority) -> bool:
    return _rank(proposed) > _rank(current)


def is_urgent_raise(current: Priority, proposed: Priority) -> bool:
    """Öncelik P1/P2'ye yükseltiliyor mu (onay beklerken nöbetçi uyarılmalı)."""
    return Priority(proposed) in URGENT_PRIORITIES and _rank(proposed) < _rank(current)


def priority_change_risk(current: Priority, proposed: Priority) -> RiskLevel:
    """Öncelik değişikliğinin riski.

    Yalnızca mevcut ve önerilen öncelik P3..P5 içindeyse ve düşürme değilse LOW;
    aksi halde HIGH (P1/P2'ye yükseltme ve her düşürme insan onayı ister).
    """
    current, proposed = Priority(current), Priority(proposed)
    if (
        current in AUTO_PRIORITY_BAND
        and proposed in AUTO_PRIORITY_BAND
        and not is_downgrade(current, proposed)
    ):
        return RiskLevel.LOW
    return RiskLevel.HIGH


@dataclass
class PolicyDecision:
    outcome: DecisionOutcome
    reason: str


def get_policy(agent: str, action: str) -> ActionPolicy | None:
    return POLICIES.get((agent, action))


def evaluate(
    *,
    agent: str,
    action: str,
    confidence: float,
    risk: RiskLevel = RiskLevel.LOW,
    settings: Settings | None = None,
) -> PolicyDecision:
    settings = settings or get_settings()
    risk = RiskLevel(risk)
    policy = get_policy(agent, action)

    if policy is None:
        return PolicyDecision(
            DecisionOutcome.REJECTED,
            f"Bilinmeyen eylem: {agent}.{action} izin listesinde yok.",
        )
    # NaN/sonsuz/aralık dışı güven skoru eşik kontrolünü atlatamaz
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return PolicyDecision(
            DecisionOutcome.ESCALATED,
            f"Geçersiz güven skoru ({confidence}); insana yönlendirildi.",
        )
    if confidence < settings.ai_confidence_threshold:
        return PolicyDecision(
            DecisionOutcome.ESCALATED,
            f"Güven skoru ({confidence:.2f}) eşiğin ({settings.ai_confidence_threshold:.2f}) "
            "altında; insana yönlendirildi.",
        )
    if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return PolicyDecision(
            DecisionOutcome.NEEDS_APPROVAL,
            f"Risk seviyesi {risk.value}; insan onayı gerekli.",
        )
    if policy.max_auto_risk is None or _RISK_ORDER[risk] > _RISK_ORDER[policy.max_auto_risk]:
        return PolicyDecision(
            DecisionOutcome.NEEDS_APPROVAL,
            f"{agent}.{action} bu risk seviyesinde ({risk.value}) otomatik uygulanamaz; "
            "insan onayı gerekli.",
        )
    if settings.ai_shadow_mode:
        return PolicyDecision(
            DecisionOutcome.SUGGESTED,
            "Gölge mod açık; karar öneri olarak bırakıldı.",
        )
    return PolicyDecision(
        DecisionOutcome.AUTO_APPLIED,
        f"{agent}.{action} politika gereği otomatik uygulandı.",
    )

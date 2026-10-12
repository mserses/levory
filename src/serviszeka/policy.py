"""Politika motoru: yapay zekânın neyi kendi başına yapabileceğine karar verir."""

from dataclasses import dataclass

from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import DecisionOutcome, RiskLevel

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
    ("service_desk", "set_priority"): ActionPolicy(RiskLevel.LOW, "Etki/aciliyet ve öncelik"),
    ("knowledge", "draft_article"): ActionPolicy(RiskLevel.LOW, "Bilgi makalesi taslağı"),
    ("incident", "resolve"): ActionPolicy(None, "Olayı çözüldü olarak kapatma"),
    ("change", "execute"): ActionPolicy(None, "Değişikliği uygulama (asla otomatik)"),
}


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

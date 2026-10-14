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
    # Faz 2: yapay zekâ yalnızca risk skoru/seviyesi önerir. Riski yükselten öneri LOW risktir
    # (gölge mod kapalı + güven yeterliyse uygulanır); düşüren öneri HIGH -> insan onayı.
    # Durum geçişi asla bu eylemle yapılmaz (services/changes.propose_ai_risk).
    ("change", "assess_risk"): ActionPolicy(
        RiskLevel.LOW, "Değişiklik risk skoru/seviyesi önerisi (yalnızca yükseltme otomatik)"
    ),
    ("change", "approve"): ActionPolicy(None, "Değişikliği CAB adına onaylama (asla otomatik)"),
    # Değişiklik risk ajanı (agents/change_risk): change.assess_risk ile aynı kural
    ("change_risk", "assess_risk"): ActionPolicy(
        RiskLevel.LOW, "Ajan risk değerlendirmesi (yalnızca yükseltme otomatik)"
    ),
    # --- Faz 3 araç kataloğu (tools/): anahtar (alan, fiil) = araç adı `alan.fiil` ---------
    # R1 araçlar en fazla LOW riskte otomatik; R2/R3 araçlar asla otomatik değildir (insan onayı,
    # ActionRequest). Koşullu riskli R1 araç (ör. incident.set_priority P1/P2) R2'ye çıktığında
    # HIGH riskle değerlendirilir ve onaya düşer. Kapı: tests/test_gate_no_unapproved_risk.py.
    ("incident", "create"): ActionPolicy(RiskLevel.LOW, "Olay açma (P1/P2 olay R2)"),
    ("incident", "classify"): ActionPolicy(RiskLevel.LOW, "Tür/kategori/hizmet/grup"),
    ("incident", "set_priority"): ActionPolicy(
        RiskLevel.LOW, "Etki/aciliyet (yalnızca P3..P5 içinde yükseltme; diğerleri R2)"
    ),
    ("incident", "assign"): ActionPolicy(RiskLevel.LOW, "Çözüm grubu/uzman ataması"),
    ("incident", "link_ci"): ActionPolicy(RiskLevel.LOW, "Olaya CI bağlama"),
    ("incident", "set_on_hold"): ActionPolicy(
        RiskLevel.LOW, "Beklemeye alma (SLA saatini durduran nedenler R2)"
    ),
    ("change", "draft_rfc"): ActionPolicy(RiskLevel.LOW, "Değişiklik taslağı (RFC) açma"),
    ("change", "submit_for_cab"): ActionPolicy(None, "Değişikliği CAB'a gönderme (asla otomatik)"),
    ("change", "record_pir"): ActionPolicy(None, "PIR kaydı ve kapatma (asla otomatik)"),
    ("kb", "draft_article"): ActionPolicy(RiskLevel.LOW, "Bilgi makalesi taslağı"),
    ("kb", "update_draft"): ActionPolicy(RiskLevel.LOW, "Taslak makale düzenleme"),
    ("kb", "publish"): ActionPolicy(None, "Makale yayımlama (asla otomatik)"),
    ("kb", "retire"): ActionPolicy(None, "Makale arşivleme (asla otomatik)"),
    ("known_error", "create"): ActionPolicy(RiskLevel.LOW, "Bilinen hata (KEDB) taslağı"),
    ("notify", "oncall_page"): ActionPolicy(RiskLevel.LOW, "Nöbetçiyi uyarma"),
    # --- Faz 3 hizmet talepleri ve bağlayıcı araçları (Entra ID / M365 / Teams) ---------------
    # Katalog planı adımları `ai:fulfilment` aktörüyle aynı politikadan geçer (services/requests).
    ("request", "create"): ActionPolicy(RiskLevel.LOW, "Hizmet talebi açma (adımlar ayrıca)"),
    ("request", "run_fulfillment_step"): ActionPolicy(
        RiskLevel.LOW, "Bekleyen karşılamayı ilerletme (adımlar ayrıca değerlendirilir)"
    ),
    ("request", "complete"): ActionPolicy(RiskLevel.LOW, "El ile adımı tamamlandı işaretleme"),
    ("identity", "reset_password"): ActionPolicy(
        RiskLevel.LOW, "Parola sıfırlama (yalnızca doğrulanmış self-servis; diğerleri R2)"
    ),
    ("identity", "unlock_account"): ActionPolicy(RiskLevel.LOW, "Hesap kilidi açma"),
    ("identity", "add_to_group"): ActionPolicy(
        RiskLevel.LOW, "Standart gruba ekleme (izin listesi dışı/ayrıcalıklı grup R3)"
    ),
    ("identity", "reset_mfa"): ActionPolicy(None, "MFA sıfırlama (asla otomatik)"),
    ("identity", "remove_from_group"): ActionPolicy(None, "Gruptan çıkarma (asla otomatik)"),
    ("identity", "revoke_sessions"): ActionPolicy(None, "Oturumları iptal (asla otomatik)"),
    ("identity", "disable_user"): ActionPolicy(None, "Hesabı devre dışı bırakma (asla otomatik)"),
    ("mailbox", "grant_shared_access"): ActionPolicy(
        None, "Paylaşılan posta kutusu izni (asla otomatik)"
    ),
    ("license", "assign"): ActionPolicy(RiskLevel.LOW, "Microsoft 365 lisansı atama"),
    ("notify", "send_to_user"): ActionPolicy(RiskLevel.LOW, "Kullanıcıya bildirim"),
    ("notify", "post_to_channel"): ActionPolicy(RiskLevel.LOW, "Teams kanalına mesaj (maskeli)"),
    # Sorun yönetimi (agents/problem, tools/builtin/problem): yapay zekâ önerir, taslak yazar;
    # kapatma ve geçici çözümün yayımlanması asla otomatik değildir
    ("problem", "create"): ActionPolicy(RiskLevel.LOW, "Eğilimden sorun kaydı açma"),
    ("problem", "link_incidents"): ActionPolicy(RiskLevel.LOW, "Soruna olay bağlama"),
    ("problem", "record_rca"): ActionPolicy(RiskLevel.LOW, "RCA taslağı (yalnızca rca_draft)"),
    ("problem", "propose_fix"): ActionPolicy(RiskLevel.LOW, "Kalıcı çözüm için taslak RFC"),
    ("problem", "close"): ActionPolicy(None, "Sorunu kapatma (asla otomatik)"),
    ("known_error", "publish_workaround"): ActionPolicy(
        None, "Geçici çözümü KEDB'de yayımlama (asla otomatik)"
    ),
    # Faz 4 raporlama (R0 okuma araçları; tools/builtin/kpi.py): yazma yok, kayıt için girdi
    ("kpi", "report"): ActionPolicy(RiskLevel.LOW, "KPI raporu (yalnızca okuma)"),
    ("sla", "report"): ActionPolicy(RiskLevel.LOW, "SLA uyum raporu (yalnızca okuma)"),
    ("sla", "forecast"): ActionPolicy(RiskLevel.LOW, "SLA ihlal tahmini (yalnızca okuma)"),
    # Faz 4 izleme / olay yönetimi (tools/builtin/event.py, agents/events.py `ai:events`): okuma ve
    # üstlenme serbest; olay açma R1 (P1/P2 olay R2 -> onay); susturma asla otomatik değil
    ("event", "list_alerts"): ActionPolicy(RiskLevel.LOW, "İzleme uyarıları (yalnızca okuma)"),
    ("event", "correlate"): ActionPolicy(RiskLevel.LOW, "Uyarı bağıntısı (yalnızca okuma)"),
    ("event", "acknowledge"): ActionPolicy(RiskLevel.LOW, "Uyarı grubunu üstlenme"),
    ("event", "create_incident"): ActionPolicy(
        RiskLevel.LOW, "Uyarı grubundan olay açma/not ekleme (P1/P2 olay R2; çözme yok)"
    ),
    ("event", "silence"): ActionPolicy(None, "Uyarı grubunu susturma (asla otomatik)"),
    # Faz 4 sürekli iyileştirme (tools/builtin/csi.py, agents/improvement.py `ai:csi`): yapay zekâ
    # yalnızca fikir açar ve ölçer; onay / başlatma / kapatma / red araç değildir (yalnızca insan)
    ("csi", "list"): ActionPolicy(RiskLevel.LOW, "İyileştirme kaydı (yalnızca okuma)"),
    ("csi", "detect_signals"): ActionPolicy(RiskLevel.LOW, "İyileştirme sinyalleri (okuma)"),
    ("csi", "create_improvement"): ActionPolicy(RiskLevel.LOW, "İyileştirme fikri açma (idea)"),
    ("csi", "measure_improvement"): ActionPolicy(
        RiskLevel.LOW, "Uygulanan iyileştirmenin KPI ölçümü (onay/kapatma yok)"
    ),
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

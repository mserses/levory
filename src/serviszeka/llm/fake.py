"""Geliştirme kipi için deterministik, anahtar gerektirmeyen LLM (`SZ_LLM_PROVIDER=fake`).

Yalnızca `SZ_ENVIRONMENT=development` iken kullanılabilir (bkz. `check_provider`).
Anahtar kelime kurallarıyla sınıflandırır (triage); değişiklik risk istemine
(`agents/change_risk`) deterministik skordan türetilen anlatı ve kontrol listesi döner; aynı girdi her zaman aynı çıktıyı verir. Ajan
akışı (maskeleme, KB kaynak doğrulama, politika, denetim) gerçek istemciyle aynen çalışır.
"""

from __future__ import annotations

import json
from typing import Any

from serviszeka.config import Settings
from serviszeka.privacy import mask_pii

FAKE_MODEL = "fake-deterministic"

_KB_SECTION = "Bilgi bankası sonuçları"
# agents/change_risk._user_prompt bu başlıkla deterministik skoru verir
_RISK_SECTION = "Deterministik risk değerlendirmesi:"

# (anahtar kelimeler, kategori, hizmet, grup) — ilk eşleşen kazanır
_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("oltalama", "phishing", "zararlı", "virüs", "fidye"), "guvenlik", "kimlik", "security_ops"),
    (("vpn",), "ag", "vpn", "network_ops"),
    (("wi-fi", "wifi", "kablosuz"), "ag", "kablosuz_ag", "network_ops"),
    (("parola", "şifre", "mfa", "hesap kilit"), "erisim_kimlik", "kimlik", "service_desk_l1"),
    (("e-posta", "eposta", "outlook", "posta kutusu"), "eposta_isbirligi", "eposta", "collaboration"),
    (("teams",), "eposta_isbirligi", "teams", "collaboration"),
    (("yazıcı", "yazdır"), "yazici", "yazdirma", "end_user_support"),
    (("sap", "erp"), "is_uygulamasi", "erp", "business_apps"),
    (("crm",), "is_uygulamasi", "crm", "business_apps"),
    (("veritabanı", "rapor"), "veritabani", "veri_platformu", "dba"),
    (("sunucu", "depolama", "bulut"), "altyapi_bulut", "dosya_paylasimi", "infra_cloud"),
)
_DEFAULT = ("yazilim", "is_istasyonu", "end_user_support")

_HIGH_IMPACT = ("tüm şirket", "herkes", "tüm kullanıcı", "tüm ofis", "kurumun tamamı", "tüm birim")
_MEDIUM_IMPACT = ("ekip", "birim", "departman", "birkaç kullanıcı")
_HIGH_URGENCY = ("çöktü", "acil", "duruyor", "durdu", "kesinti", "erişilemiyor", "çalışmıyor")
_MEDIUM_URGENCY = ("yavaş", "zaman zaman", "sorun")


def _lower_tr(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def _record_text(user: str) -> str:
    return user.split(_KB_SECTION, 1)[0]


def _kb_ids(user: str) -> list[str]:
    if _KB_SECTION not in user:
        return []
    tail = user.split(_KB_SECTION, 1)[1]
    start = tail.find("[")
    if start == -1:
        return []
    try:
        items, _ = json.JSONDecoder().raw_decode(tail, start)
    except json.JSONDecodeError:
        return []
    return [str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")][:1]


def classify(user: str) -> dict[str, Any]:
    """Kayıt metnine göre deterministik sınıflandırma (ajan yanıt şemasıyla)."""
    text = _lower_tr(_record_text(user))
    category, service, group = next(
        ((c, s, g) for words, c, s, g in _RULES if any(w in text for w in words)), _DEFAULT
    )
    if any(w in text for w in _HIGH_IMPACT):
        impact = "high"
    elif any(w in text for w in _MEDIUM_IMPACT):
        impact = "medium"
    else:
        impact = "low"
    if any(w in text for w in _HIGH_URGENCY):
        urgency = "high"
    elif any(w in text for w in _MEDIUM_URGENCY):
        urgency = "medium"
    else:
        urgency = "low"
    if "nasıl" in text:
        kind = "question"
    elif any(w in text for w in ("talep", "istiyorum", "erişim izni", "kurulum")):
        kind = "service_request"
    else:
        kind = "incident"
    kb_ids = _kb_ids(user)
    return {
        "kind": kind,
        "category": category,
        "service": service,
        "impact": impact,
        "urgency": urgency,
        "assignment_group": group,
        "suggested_resolution": (
            "İlgili bilgi bankası makalesindeki adımları uygulayın." if kb_ids else None
        ),
        "kb_article_ids": kb_ids,
        "confidence": 0.9,
        "rationale": (
            f"[fake] Anahtar kelime kuralları: kategori {category}, etki {impact}, "
            f"aciliyet {urgency}."
        ),
    }


_RISK_CHECKS: tuple[tuple[str, str], ...] = (
    ("backout_plan", "Geri dönüş planını adım adım tamamlayın ve prova edin."),
    ("production", "Bakım penceresini ilgili kullanıcılara önceden duyurun."),
    ("criticality", "Kritik CI'lar için yedeğin alındığını ve geri yüklenebildiğini doğrulayın."),
    ("business_services", "Etkilenen iş hizmetlerinin sahiplerinden onay alın."),
    ("failure_history", "Aynı CI'lardaki önceki başarısız değişikliklerin nedenlerini inceleyin."),
    ("recent_incidents", "Son olayların kök nedeni çözülmeden değişikliği uygulamayın."),
)
_FACTOR_LABELS = {
    "change_type": "değişiklik türü",
    "criticality": "CI kritikliği",
    "production": "üretim ortamı",
    "business_services": "etkilenen iş hizmetleri",
    "failure_history": "geçmiş başarısızlıklar",
    "recent_incidents": "son olaylar",
    "backout_plan": "geri dönüş planı",
}


def _risk_payload(user: str) -> dict[str, Any]:
    tail = user.split(_RISK_SECTION, 1)[1]
    start = tail.find("{")
    if start == -1:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(tail, start)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def assess_change_risk(user: str) -> dict[str, Any]:
    """Değişiklik risk ajanı şemasıyla deterministik anlatı (düzeltme her zaman 0)."""
    det = _risk_payload(user)
    factors = {k: v for k, v in (det.get("factors") or {}).items() if isinstance(v, int)}
    top = sorted((k for k, v in factors.items() if v > 0), key=lambda k: -factors[k])[:3]
    drivers = ", ".join(_FACTOR_LABELS.get(k, k) for k in top) or "belirgin bir etken yok"
    services = (det.get("inputs") or {}).get("business_services") or []
    narrative = (
        f"[fake] Deterministik risk skoru {det.get('score', '?')} ({det.get('level', '?')}). "
        f"Skoru en çok etkileyenler: {drivers}."
    )
    if services:
        narrative += f" Etkilenen iş hizmetleri: {', '.join(map(str, services))}."
    checklist = [text for key, text in _RISK_CHECKS if factors.get(key, 0) > 0]
    checklist.append("Uygulama sonrası duman testini çalıştırıp sonucu kaydedin.")
    return {
        "narrative": narrative,
        "checklist": checklist,
        "adjustment": 0,
        "justification": "",
        "confidence": 0.85,
    }


class DeterministicFakeLLM:
    """`SZ_LLM_PROVIDER=fake` istemcisi: ağ çağrısı yapmaz, istemi yine maskeler."""

    model_name = FAKE_MODEL

    async def complete_json(
        self, *, system: str, user: str, model: str | None = None
    ) -> dict:
        masked = mask_pii(user)
        if _RISK_SECTION in masked:
            return assess_change_risk(masked)
        return classify(masked)


def check_provider(settings: Settings) -> None:
    """fake sağlayıcı yalnızca geliştirme ortamında kabul edilir."""
    if settings.llm_provider == "fake" and settings.environment != "development":
        from serviszeka.llm.client import LLMError

        raise LLMError(
            "SZ_LLM_PROVIDER=fake yalnızca SZ_ENVIRONMENT=development iken kullanılabilir "
            f"(şu an: {settings.environment})."
        )

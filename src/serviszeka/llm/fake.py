"""Geliştirme kipi için deterministik, anahtar gerektirmeyen LLM (`SZ_LLM_PROVIDER=fake`).

Yalnızca `SZ_ENVIRONMENT=development` iken kullanılabilir (bkz. `check_provider`).
Anahtar kelime kurallarıyla sınıflandırır; aynı girdi her zaman aynı çıktıyı verir. Ajan
akışı (maskeleme, KB kaynak doğrulama, politika, denetim) gerçek istemciyle aynen çalışır.
"""

from __future__ import annotations

import json
from typing import Any

from serviszeka.config import Settings
from serviszeka.privacy import mask_pii

FAKE_MODEL = "fake-deterministic"

_KB_SECTION = "Bilgi bankası sonuçları"

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


class DeterministicFakeLLM:
    """`SZ_LLM_PROVIDER=fake` istemcisi: ağ çağrısı yapmaz, istemi yine maskeler."""

    model_name = FAKE_MODEL

    async def complete_json(
        self, *, system: str, user: str, model: str | None = None
    ) -> dict:
        return classify(mask_pii(user))


def check_provider(settings: Settings) -> None:
    """fake sağlayıcı yalnızca geliştirme ortamında kabul edilir."""
    if settings.llm_provider == "fake" and settings.environment != "development":
        from serviszeka.llm.client import LLMError

        raise LLMError(
            "SZ_LLM_PROVIDER=fake yalnızca SZ_ENVIRONMENT=development iken kullanılabilir "
            f"(şu an: {settings.environment})."
        )

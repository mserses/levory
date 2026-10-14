"""KURULUM.md ortam değişkeni tablosunu `serviszeka.config.Settings`'ten üretir.

Kullanım (serviszeka/ kökünde):

    uv run python docs/_gen_settings.py            # tabloyu stdout'a yazar
    uv run python docs/_gen_settings.py --write    # docs/KURULUM.md içindeki bloğu günceller
    uv run python docs/_gen_settings.py --check    # blok güncel değilse çıkış kodu 1

Tür, varsayılan ve sınırlar `config.py`'den okunur; alan (bölüm) ve kısa açıklama aşağıdaki
`AREAS` / `DESCRIPTIONS` sözlüklerindedir. `config.py`'ye yeni bir ayar eklendiğinde buraya da
açıklamasını ekleyip `--write` çalıştırın; `tests/test_docs_settings.py` eksik ya da bayat tabloyu
yakalar.
"""

from __future__ import annotations

import argparse
import sys
import types
import typing
from pathlib import Path
from typing import Any, Literal

import annotated_types
from pydantic import BaseModel, SecretStr
from pydantic_core import PydanticUndefined

from serviszeka.config import Settings

DOC = Path(__file__).resolve().parent / "KURULUM.md"
BEGIN = "<!-- SZ_SETTINGS:BEGIN (docs/_gen_settings.py --write ile üretilir; elle düzenlemeyin) -->"
END = "<!-- SZ_SETTINGS:END -->"

# (bölüm başlığı, alan adları) — sıra tablo sırasıdır
AREAS: list[tuple[str, tuple[str, ...]]] = [
    ("Ortam ve kimlik doğrulama", (
        "environment", "auth_mode", "auth_issuer", "auth_audience", "auth_jwks_url",
        "auth_roles_claim", "auth_username_claim", "auth_leeway_seconds", "auth_http_timeout",
        "cors_origins",
    )),
    ("Veritabanı", ("database_url", "auto_create_schema")),
    ("LLM ve yapay zekâ yönetişimi", (
        "llm_provider", "anthropic_api_key", "llm_reasoning_model", "llm_fast_model",
        "ai_confidence_threshold", "ai_shadow_mode",
    )),
    ("Onaylar ve süreler", (
        "cab_min_approvals", "action_request_ttl_hours", "request_approval_ttl_hours",
    )),
    ("Bağlayıcı seçimi ve grup politikası", (
        "connector_provider", "identity_provider", "notifier_provider",
        "identity_standard_groups", "identity_privileged_groups",
    )),
    ("Microsoft Entra ID / Graph / Teams", (
        "graph_tenant_id", "graph_client_id", "graph_client_secret", "graph_base_url",
        "graph_login_url", "graph_timeout", "graph_sender_mailbox", "teams_webhook_urls",
    )),
    ("Keycloak (kimlik bağlayıcısı)", (
        "keycloak_server_url", "keycloak_realm", "keycloak_client_id", "keycloak_client_secret",
        "keycloak_manager_attribute", "keycloak_secondary_email_attribute",
    )),
    ("SMTP", (
        "smtp_host", "smtp_port", "smtp_sender", "smtp_username", "smtp_password",
        "smtp_starttls", "smtp_ssl", "smtp_allow_plaintext",
    )),
    ("Slack", (
        "slack_bot_token", "slack_default_channel", "slack_oncall_channel", "slack_webhook_url",
    )),
    ("ITSM köprüsü (ServiceNow / Jira SM)", (
        "servicenow_instance_url", "servicenow_username", "servicenow_password",
        "jira_base_url", "jira_email", "jira_api_token", "jira_service_desk_id",
        "jira_request_type_id", "sync_conflict_policy",
    )),
    ("Bildirim kuyruğu ve zamanlayıcı", (
        "oncall_webhook_url", "notification_max_attempts", "notification_backoff_seconds",
        "outbox_retention_days", "outbox_dead_retention_days", "scheduler_enabled",
        "scheduler_tick_seconds",
    )),
    ("İzleme webhook'ları ve olay bağıntısı", (
        "events_webhook_secret", "events_max_body_bytes", "events_replay_window_seconds",
        "events_ci_mapping", "events_correlation_window_minutes", "events_flap_threshold",
        "events_flap_window_minutes", "events_incident_min_severity",
        "events_max_incidents_per_hour",
    )),
    ("Sürekli iyileştirme (CSI)", ("csi_window_days", "csi_measure_days", "csi_cooldown_days")),
]

# Tür SecretStr olmasa da gizli tutulması gereken değerler (tabloda "evet" yazılır)
PLAIN_SECRETS = frozenset({
    "database_url", "anthropic_api_key", "servicenow_password", "jira_api_token",
    "oncall_webhook_url", "teams_webhook_urls",
})

DESCRIPTIONS: dict[str, str] = {
    "environment": "Üretimde `production` bırakın. `development` dışındaki her ortamda dev "
    "kimliği, sahte LLM, sahte dizin ve düz metin SMTP reddedilir.",
    "auth_mode": "`oidc` (bearer JWT). `dev` imza doğrulamaz; yalnızca `development`.",
    "auth_issuer": "OIDC kipinde **zorunlu**. Belirteçteki `iss` ile birebir aynı olmalı.",
    "auth_audience": "OIDC kipinde **zorunlu**. Beklenen `aud`; Keycloak istemci rolleri de "
    "`resource_access.<bu değer>.roles` yolundan okunur.",
    "auth_jwks_url": "Boşsa `<issuer>/.well-known/openid-configuration` keşfinden.",
    "auth_roles_claim": "Rol talebinin noktalı yolu. Boşsa `realm_access.roles` + "
    "`resource_access.<aud>.roles` + `roles` birlikte okunur.",
    "auth_username_claim": "Aktör adı talebi; yoksa `upn`, `email`, `sub`.",
    "auth_leeway_seconds": "`exp`/`nbf` saat kayması toleransı (sn).",
    "auth_http_timeout": "JWKS / keşif isteği zaman aşımı (sn).",
    "cors_origins": "Portal kökenleri (virgüllü ya da JSON dizi). Boşsa CORS kapalı.",
    "database_url": "Üretimde `postgresql+asyncpg://kullanıcı:parola@host:5432/vt`.",
    "auto_create_schema": "Üretimde **false**; şema yalnızca `alembic upgrade head` ile.",
    "llm_provider": "`fake` yalnızca `development` (deterministik, ağsız).",
    "anthropic_api_key": "Boşsa LLM gerektiren uçlar 503, zamanlanmış CSI gerekçesi "
    "deterministik metne düşer.",
    "llm_reasoning_model": "Sorun yönetimi RCA taslağı.",
    "llm_fast_model": "Triage, değişiklik risk anlatısı, CSI gerekçesi.",
    "ai_confidence_threshold": "Bu güvenin altındaki yapay zekâ kararı insana gider.",
    "ai_shadow_mode": "true iken ajanlar yalnızca önerir (R1 araçlar da onaya düşer).",
    "cab_min_approvals": "Normal değişiklik için en az CAB onayı.",
    "action_request_ttl_hours": "Onay bekleyen eylem isteği bu süre sonunda `expired`.",
    "request_approval_ttl_hours": "İş onayı bekleyen hizmet talebi bu süre sonunda reddedilir.",
    "connector_provider": "Eski tek seçim. Yeni kurulumda `none` bırakıp aşağıdaki iki ayarı "
    "kullanın; çelişki açılışı durdurur.",
    "identity_provider": "`graph` | `keycloak` (| `memory` yalnızca geliştirme).",
    "notifier_provider": "`teams` | `slack` | `log`.",
    "identity_standard_groups": "Ayrıcalıksız, iş onayı gerektirmeyen grup kimlikleri; "
    "yalnızca bunlara ekleme R1 olabilir. Boş = her ekleme R3.",
    "identity_privileged_groups": "Her zaman ayrıcalıklı (R3) gruplar.",
    "graph_tenant_id": "`SZ_IDENTITY_PROVIDER=graph` iken zorunlu.",
    "graph_client_id": "`graph` iken zorunlu (uygulama kaydı istemci kimliği).",
    "graph_client_secret": "`graph` iken zorunlu.",
    "graph_base_url": "Graph API tabanı.",
    "graph_login_url": "Belirteç ucu tabanı.",
    "graph_timeout": "Graph / Teams HTTP zaman aşımı (sn).",
    "graph_sender_mailbox": "`SZ_NOTIFIER_PROVIDER=teams` iken zorunlu (`Mail.Send` göndereni).",
    "teams_webhook_urls": "`{\"kanal\": \"https://...\"}`; URL'ler belirteç taşır.",
    "keycloak_server_url": "`keycloak` iken zorunlu; Keycloak <17 için `.../auth` ile biter.",
    "keycloak_realm": "`keycloak` iken zorunlu.",
    "keycloak_client_id": "`keycloak` iken zorunlu (hizmet hesabı etkin gizli istemci).",
    "keycloak_client_secret": "`keycloak` iken zorunlu.",
    "keycloak_manager_attribute": "Yöneticinin kullanıcı adını taşıyan öznitelik.",
    "keycloak_secondary_email_attribute": "Geçici parolanın gideceği ikincil e-posta "
    "özniteliği; yalnızca yöneticilerce düzenlenebilir olmalı.",
    "smtp_host": "Host + gönderen doluysa SMTP bildiricisi etkin.",
    "smtp_port": "587 (STARTTLS) ya da 465 (örtük TLS).",
    "smtp_sender": "Gönderen adresi.",
    "smtp_username": "İsteğe bağlı kimlik doğrulama kullanıcısı.",
    "smtp_password": "İsteğe bağlı.",
    "smtp_starttls": "`SZ_SMTP_SSL` ile birlikte açılamaz.",
    "smtp_ssl": "Örtük TLS (SMTPS).",
    "smtp_allow_plaintext": "TLS'siz SMTP; yalnızca `development`.",
    "slack_bot_token": "`xoxb-...`; doluysa bildirim Slack olabilir.",
    "slack_default_channel": "`default` kanal kimliği (`C...`).",
    "slack_oncall_channel": "`oncall` kanal kimliği; nöbetçi uyarısını da alır.",
    "slack_webhook_url": "Bot yoksa tek kanallı gelen webhook.",
    "servicenow_instance_url": "Örn. `https://ornek.service-now.com`; üçü doluysa etkin.",
    "servicenow_username": "Table API kullanıcısı (Basic).",
    "servicenow_password": "Table API parolası.",
    "jira_base_url": "Örn. `https://ornek.atlassian.net`; adres + e-posta + belirteç doluysa "
    "etkin.",
    "jira_email": "API kullanıcısının e-postası (Basic).",
    "jira_api_token": "Atlassian API belirteci.",
    "jira_service_desk_id": "Talep açılacak hizmet masası.",
    "jira_request_type_id": "Boşsa hizmet masasının ilk istek türü.",
    "sync_conflict_policy": "İki yönlü senkron çakışmasında davranış.",
    "oncall_webhook_url": "Nöbetçi uyarısı webhook'u; her bildiriciden önceliklidir.",
    "notification_max_attempts": "Bu kadar denemeden sonra satır `dead`.",
    "notification_backoff_seconds": "Üstel geri çekilme tabanı (en fazla 1 sa).",
    "outbox_retention_days": "`sent` satırların saklama süresi.",
    "outbox_dead_retention_days": "`dead` satırların saklama süresi.",
    "scheduler_enabled": "Arka plan işleri (kuyruk, süre aşımları, SLA, CSI).",
    "scheduler_tick_seconds": "Zamanlayıcı tur aralığı.",
    "events_webhook_secret": "Boşsa `/events/*` her isteğe 401 (kapalı).",
    "events_max_body_bytes": "Webhook gövde sınırı (413).",
    "events_replay_window_seconds": "İmza zaman damgası penceresi; tekrar kaydı 2 pencere.",
    "events_ci_mapping": "Etiket → CI kuralları (JSON dizi); varsayılan `ci_id`, `ci`, "
    "`instance` (port atılır), `service`, `job`.",
    "events_correlation_window_minutes": "Bağıntı penceresi.",
    "events_flap_threshold": "Pencere içinde bu kadar ateşleme → bastırılır.",
    "events_flap_window_minutes": "Çırpınma penceresi.",
    "events_incident_min_severity": "Olay açan en düşük önem.",
    "events_max_incidents_per_hour": "Olay fırtınası sınırı (P1/P2 yine onaya gider).",
    "csi_window_days": "Sinyal penceresi (önceki eşit pencereyle karşılaştırılır).",
    "csi_measure_days": "Uygulama sonrası ölçüm penceresi.",
    "csi_cooldown_days": "Reddedilen/kapanan sinyalin yeniden önerilmeme süresi.",
}

_BOUNDS = (
    (annotated_types.Ge, "ge", "≥"),
    (annotated_types.Gt, "gt", ">"),
    (annotated_types.Le, "le", "≤"),
    (annotated_types.Lt, "lt", "<"),
)


def env_name(field: str) -> str:
    return f"SZ_{field.upper()}"


def _strip_optional(tp: Any) -> tuple[Any, bool]:
    args = typing.get_args(tp)
    if typing.get_origin(tp) in (typing.Union, types.UnionType) and type(None) in args:
        rest = [a for a in args if a is not type(None)]
        return (rest[0] if len(rest) == 1 else tp), True
    return tp, False


def _type_label(tp: Any, metadata: list[Any]) -> str:
    tp, _optional = _strip_optional(tp)
    origin = typing.get_origin(tp)
    if origin is Literal:
        label = " \\| ".join(f"`{v}`" for v in typing.get_args(tp))
    elif tp is SecretStr:
        label = "metin"
    elif tp is bool:
        label = "bool"
    elif tp is int:
        label = "int"
    elif tp is float:
        label = "sayı"
    elif tp is str:
        label = "metin"
    elif origin is list:
        (item,) = typing.get_args(tp)
        label = "JSON dizi" if isinstance(item, type) and issubclass(item, BaseModel) else "liste"
    elif origin is dict:
        label = "JSON nesne"
    else:  # pragma: no cover - yeni bir tür eklenirse görünür olsun
        label = str(tp)
    bounds = [
        f"{sym}{getattr(m, attr)}"
        for cls, attr, sym in _BOUNDS
        for m in metadata
        if isinstance(m, cls)
    ]
    return f"{label} ({', '.join(bounds)})" if bounds else label


def _default_label(field: Any) -> str:
    if field.default_factory is not None:
        value = field.default_factory()
    elif field.default is PydanticUndefined:
        return "—"
    else:
        value = field.default
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, list) and value and isinstance(value[0], BaseModel):
        return f"{len(value)} kural"
    if isinstance(value, list):
        return "`[]`" if not value else f"`{','.join(map(str, value))}`"
    if isinstance(value, dict):
        return "`{}`" if not value else "`{…}`"
    return f"`{value}`"


def _is_secret(name: str, field: Any) -> bool:
    tp, _ = _strip_optional(field.annotation)
    return tp is SecretStr or name in PLAIN_SECRETS


def area_of(name: str) -> str | None:
    for title, names in AREAS:
        if name in names:
            return title
    return None


def render() -> str:
    """Bölümlere ayrılmış Markdown tabloları (BEGIN/END işaretleri hariç)."""
    fields = Settings.model_fields
    lines: list[str] = []
    seen: set[str] = set()
    groups = list(AREAS)
    orphans = tuple(n for n in fields if area_of(n) is None)
    if orphans:
        groups.append(("Diğer (bölümü atanmamış — docs/_gen_settings.py AREAS)", orphans))
    for title, names in groups:
        present = [n for n in names if n in fields]
        if not present:
            continue
        lines += [
            f"#### {title}",
            "",
            "| Değişken | Tür | Varsayılan | Gizli | Açıklama |",
            "|---|---|---|---|---|",
        ]
        for name in present:
            seen.add(name)
            field = fields[name]
            desc = DESCRIPTIONS.get(name, "**Açıklama yok** (docs/_gen_settings.py DESCRIPTIONS)")
            desc = desc.replace("|", "\\|")  # tablo hücresi
            lines.append(
                f"| `{env_name(name)}` | {_type_label(field.annotation, field.metadata)} | "
                f"{_default_label(field)} | {'evet' if _is_secret(name, field) else ''} | "
                f"{desc} |"
            )
        lines.append("")
    assert seen == set(fields), "her ayar bir tabloda olmalı"
    return "\n".join(lines).rstrip() + "\n"


def current_block(text: str) -> str | None:
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        return None
    return text[start + len(BEGIN) : end].strip("\n") + "\n"


def write(doc: Path = DOC) -> bool:
    text = doc.read_text(encoding="utf-8")
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        raise SystemExit(f"{doc}: {BEGIN!r} / {END!r} işaretleri bulunamadı")
    new = text[: start + len(BEGIN)] + "\n" + render() + text[end:]
    if new != text:
        doc.write_text(new, encoding="utf-8")
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="docs/KURULUM.md bloğunu güncelle")
    mode.add_argument("--check", action="store_true", help="blok güncel değilse çıkış kodu 1")
    args = parser.parse_args(argv)
    if args.write:
        print("güncellendi" if write() else "zaten güncel")
        return 0
    if args.check:
        block = current_block(DOC.read_text(encoding="utf-8"))
        if block != render():
            print("docs/KURULUM.md ayar tablosu güncel değil: "
                  "uv run python docs/_gen_settings.py --write", file=sys.stderr)
            return 1
        print("güncel")
        return 0
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

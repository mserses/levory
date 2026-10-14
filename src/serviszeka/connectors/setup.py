"""Ayarlardan bağlayıcı kurulumu (`SZ_CONNECTOR_PROVIDER`).

- `none`: hiçbir bağlayıcı kayda alınmaz (ilgili araçlar 503 `unavailable`).
- `fake`: `InMemoryDirectory.demo()` — yalnızca `SZ_ENVIRONMENT=development` (aksi halde açılış
  reddedilir; `llm/fake.py` ile aynı kural).
- `graph`: `SZ_GRAPH_TENANT_ID`, `SZ_GRAPH_CLIENT_ID`, `SZ_GRAPH_CLIENT_SECRET` zorunlu.
- `keycloak`: `SZ_KEYCLOAK_SERVER_URL`, `SZ_KEYCLOAK_REALM`, `SZ_KEYCLOAK_CLIENT_ID`,
  `SZ_KEYCLOAK_CLIENT_SECRET` zorunlu; yalnızca `identity` kurulur (`connectors.keycloak`).
  `SZ_SMTP_HOST` + `SZ_SMTP_SENDER` doluysa `messaging` SMTP bildiricisidir (`connectors.smtp`;
  geçici parola ikincil e-posta özniteliğine gider).
- Her kipte `SZ_SLACK_BOT_TOKEN` / `SZ_SLACK_WEBHOOK_URL` varsa `messaging` Slack olur
  (`connectors.slack`).

Ayrı seçim (`resolve_providers`; boşsa yukarıdaki eski davranış aynen geçerli):
- `SZ_IDENTITY_PROVIDER` = `graph` (identity + mail + license) | `keycloak` (yalnızca identity) |
  `memory` (sahte dizin, yalnızca development). `SZ_CONNECTOR_PROVIDER` başka bir kimlik
  sağlayıcısı seçiyorsa açılış reddedilir.
- `SZ_NOTIFIER_PROVIDER` = `teams` (kimlik `graph` + `SZ_GRAPH_SENDER_MAILBOX` zorunlu; Slack
  ayarlı olsa da Teams) | `slack` (`SZ_SLACK_BOT_TOKEN` ya da `SZ_SLACK_WEBHOOK_URL` zorunlu; hassas
  bildirim kimlik yığınının bildiricisine devredilir — Keycloak'ta SMTP ayarlıysa SMTP) | `log` (`messaging`
  kaydedilmez; nöbetçi uyarısı log ya da `SZ_ONCALL_WEBHOOK_URL`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from serviszeka.config import Settings
from serviszeka.connectors.base import IDENTITY, LICENSE, MAIL, MESSAGING
from serviszeka.connectors.graph import (
    GraphClient,
    GraphIdentityProvider,
    GraphLicenseProvider,
    GraphMailProvider,
    TeamsGraphNotifier,
)
from serviszeka.connectors.memory import InMemoryDirectory

logger = logging.getLogger(__name__)


class ConnectorConfigError(RuntimeError):
    """Bağlayıcı ayarları güvensiz ya da eksik: uygulama açılmaz."""


# SZ_CONNECTOR_PROVIDER (eski tek ayar) -> kimlik sağlayıcısı
_LEGACY_IDENTITY = {"none": "none", "fake": "memory", "graph": "graph", "keycloak": "keycloak"}


@dataclass(frozen=True)
class ProviderSelection:
    """Çözülmüş seçim. `notifier="auto"`: eski davranış (kimlik yığınının bildiricisi + Slack)."""

    identity: str  # none | graph | keycloak | memory
    notifier: str  # auto | teams | slack | log
    identity_source: str  # hata mesajında anılan ayar, ör. "SZ_IDENTITY_PROVIDER=graph"


def _present(value: Any) -> bool:
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def _require(source: str, pairs: tuple[tuple[str, Any], ...]) -> None:
    missing = [name for name, value in pairs if not _present(value)]
    if missing:
        raise ConnectorConfigError(f"{source} için eksik ayar: {', '.join(missing)}")


def resolve_providers(settings: Settings) -> ProviderSelection:
    """Kimlik ve bildirim sağlayıcısını seçer ve zorunlu ayarları denetler.

    Hata `ConnectorConfigError` (Türkçe; gizli değer içermez, yalnızca ayar adlarını anar).
    """
    legacy = _LEGACY_IDENTITY[settings.connector_provider]
    if settings.identity_provider is not None:
        identity = settings.identity_provider
        source = f"SZ_IDENTITY_PROVIDER={identity}"
        if legacy not in ("none", identity):
            raise ConnectorConfigError(
                f"SZ_CONNECTOR_PROVIDER={settings.connector_provider} ile {source} çelişiyor; "
                "yalnızca SZ_IDENTITY_PROVIDER kullanın (SZ_CONNECTOR_PROVIDER=none bırakın)."
            )
    else:
        identity = legacy
        source = f"SZ_CONNECTOR_PROVIDER={settings.connector_provider}"

    if identity == "memory" and settings.environment != "development":
        raise ConnectorConfigError(
            f"{source} (sahte dizin) yalnızca SZ_ENVIRONMENT=development iken kullanılabilir."
        )
    if identity == "graph":
        _require(source, (
            ("SZ_GRAPH_TENANT_ID", settings.graph_tenant_id),
            ("SZ_GRAPH_CLIENT_ID", settings.graph_client_id),
            ("SZ_GRAPH_CLIENT_SECRET", settings.graph_client_secret),
        ))
    if identity == "keycloak":
        _require(source, (
            ("SZ_KEYCLOAK_SERVER_URL", settings.keycloak_server_url),
            ("SZ_KEYCLOAK_REALM", settings.keycloak_realm),
            ("SZ_KEYCLOAK_CLIENT_ID", settings.keycloak_client_id),
            ("SZ_KEYCLOAK_CLIENT_SECRET", settings.keycloak_client_secret),
        ))

    notifier = settings.notifier_provider or "auto"
    if notifier == "teams":
        if identity != "graph":
            raise ConnectorConfigError(
                "SZ_NOTIFIER_PROVIDER=teams, Microsoft Graph kimlik sağlayıcısı gerektirir "
                "(SZ_IDENTITY_PROVIDER=graph): kullanıcı bildirimi alıcıyı Entra ID'de çözer ve "
                "Graph sendMail ile gönderir."
            )
        _require("SZ_NOTIFIER_PROVIDER=teams", (
            ("SZ_GRAPH_SENDER_MAILBOX", settings.graph_sender_mailbox),
        ))
    if notifier == "slack" and not (
        _present(settings.slack_bot_token) or _present(settings.slack_webhook_url)
    ):
        raise ConnectorConfigError(
            "SZ_NOTIFIER_PROVIDER=slack için eksik ayar: SZ_SLACK_BOT_TOKEN ya da "
            "SZ_SLACK_WEBHOOK_URL"
        )
    return ProviderSelection(identity, notifier, source)


def check_startup(settings: Settings) -> None:
    sel = resolve_providers(settings)
    # Hassas bildirim (geçici parola) yalnızca ikincil adrese gider; teslim edilemeyecekse uyar
    smtp_ready = _present(settings.smtp_host) and _present(settings.smtp_sender)
    if (sel.identity == "keycloak" and (not smtp_ready or sel.notifier == "log")) or (
        sel.identity == "graph" and not _present(settings.graph_sender_mailbox)
        and sel.notifier != "log"
    ):
        logger.warning(
            "Bağlayıcılar (%s, bildirim %s): hassas bildirim (geçici parola) teslim edilemez "
            "(Keycloak'ta SZ_SMTP_HOST/SZ_SMTP_SENDER boş / SZ_GRAPH_SENDER_MAILBOX boş); "
            "identity.reset_password adımları başarısız olur.",
            sel.identity_source,
            sel.notifier,
        )


def build_graph(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    client = GraphClient(
        settings.graph_tenant_id or "",
        settings.graph_client_id or "",
        settings.graph_client_secret.get_secret_value() if settings.graph_client_secret else "",
        base_url=settings.graph_base_url,
        login_url=settings.graph_login_url,
        timeout=settings.graph_timeout,
        transport=transport,
    )
    identity = GraphIdentityProvider(
        client, privileged_groups=set(settings.identity_privileged_groups)
    )
    mail = GraphMailProvider(client, settings.graph_sender_mailbox)
    return {
        IDENTITY: identity,
        MAIL: mail,
        LICENSE: GraphLicenseProvider(client, identity),
        MESSAGING: TeamsGraphNotifier(
            identity,
            mail,
            settings.teams_webhook_urls,
            timeout=settings.graph_timeout,
            transport=transport,
        ),
    }


def build_fake(directory: InMemoryDirectory | None = None) -> dict[str, Any]:
    directory = directory or InMemoryDirectory.demo()
    return {IDENTITY: directory, MAIL: directory, LICENSE: directory, MESSAGING: directory}


def build(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    sel = resolve_providers(settings)
    check_startup(settings)
    if sel.identity == "memory":
        items = build_fake()
    elif sel.identity == "graph":
        items = build_graph(settings, transport=transport)
    elif sel.identity == "keycloak":
        from serviszeka.connectors.keycloak import build_keycloak

        items = build_keycloak(settings, transport=transport)
        # Keycloak'ta posta yok: SZ_SMTP_* ayarlıysa e-posta bildiricisi (hassas bildirim ikincil
        # e-posta özniteliğine gider; Slack ayarlıysa Slack'in hassas bildirim temsilcisi olur)
        from serviszeka.connectors.smtp import build_smtp

        smtp = build_smtp(settings, items.get(IDENTITY))
        if smtp is not None:
            items[MESSAGING] = smtp
    else:
        items = {}
    if sel.notifier == "auto":
        # Eski davranış (değişmez): kimlik yığınının bildiricisi, Slack ayarlıysa Slack
        return _with_slack(settings, items, transport=transport)
    # Kimlik yığınının kendi bildiricisi (Graph: posta + Teams; sahte: dizin) hassas bildirimde
    # ikincil adrese gönderen olarak kullanılabilir
    base = items.pop(MESSAGING, None)
    if sel.notifier == "teams":
        items[MESSAGING] = base
    elif sel.notifier == "slack":
        from serviszeka.connectors.slack import build_slack

        slack = build_slack(
            settings, identity=items.get(IDENTITY), sensitive_delegate=base, transport=transport
        )
        if slack is None:  # resolve_providers bunu engeller
            raise ConnectorConfigError("SZ_NOTIFIER_PROVIDER=slack için Slack ayarı yok.")
        items[MESSAGING] = slack
    # "log": `messaging` kaydedilmez (notify.send_* araçları 503 `unavailable`)
    return items


def _with_slack(
    settings: Settings, items: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None
) -> dict[str, Any]:
    """Slack ayarlıysa `messaging` Slack olur; hassas bildirim önceki bildiriciye devredilir."""
    from serviszeka.connectors.slack import build_slack

    slack = build_slack(
        settings,
        identity=items.get(IDENTITY),
        sensitive_delegate=items.get(MESSAGING),
        transport=transport,
    )
    return items if slack is None else {**items, MESSAGING: slack}


def install(registry: Any, items: dict[str, Any]) -> list[str]:
    """Bağlayıcıları kayıt defterine işler; kurulan türleri döndürür."""
    for kind, impl in items.items():
        registry.register(kind, impl)
    return sorted(items)


def configure(settings: Settings, registry: Any | None = None) -> list[str]:
    """Açılışta çağrılır: ayara göre bağlayıcıları kurar ve `tools.connectors`'a işler."""
    if registry is None:
        from serviszeka.tools.connectors import connectors as registry
    return install(registry, build(settings))

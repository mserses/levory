"""Ayarlardan bağlayıcı kurulumu (`SZ_CONNECTOR_PROVIDER`).

- `none`: hiçbir bağlayıcı kayda alınmaz (ilgili araçlar 503 `unavailable`).
- `fake`: `InMemoryDirectory.demo()` — yalnızca `SZ_ENVIRONMENT=development` (aksi halde açılış
  reddedilir; `llm/fake.py` ile aynı kural).
- `graph`: `SZ_GRAPH_TENANT_ID`, `SZ_GRAPH_CLIENT_ID`, `SZ_GRAPH_CLIENT_SECRET` zorunlu.
"""

from __future__ import annotations

from typing import Any

import httpx

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


class ConnectorConfigError(RuntimeError):
    """Bağlayıcı ayarları güvensiz ya da eksik: uygulama açılmaz."""


def check_startup(settings: Settings) -> None:
    provider = settings.connector_provider
    if provider == "fake" and settings.environment != "development":
        raise ConnectorConfigError(
            "SZ_CONNECTOR_PROVIDER=fake yalnızca SZ_ENVIRONMENT=development iken kullanılabilir."
        )
    if provider == "graph":
        missing = [
            name
            for name, value in (
                ("SZ_GRAPH_TENANT_ID", settings.graph_tenant_id),
                ("SZ_GRAPH_CLIENT_ID", settings.graph_client_id),
                ("SZ_GRAPH_CLIENT_SECRET", settings.graph_client_secret),
            )
            if not value
        ]
        if missing:
            raise ConnectorConfigError(
                f"SZ_CONNECTOR_PROVIDER=graph için eksik ayar: {', '.join(missing)}"
            )


def build_graph(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    client = GraphClient(
        settings.graph_tenant_id or "",
        settings.graph_client_id or "",
        settings.graph_client_secret or "",
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
    check_startup(settings)
    if settings.connector_provider == "fake":
        return build_fake()
    if settings.connector_provider == "graph":
        return build_graph(settings, transport=transport)
    return {}


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

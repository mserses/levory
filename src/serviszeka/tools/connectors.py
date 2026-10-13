"""Dış sistem bağlayıcı arayüzleri (Faz 3).

Varsayılan hedef yığın: Microsoft Entra ID (kimlik), Microsoft 365 / Graph (posta, takvim) ve
Teams (mesajlaşma). Araçlar somut sağlayıcıyı değil bu arayüzleri ister (`ToolSpec.connectors`);
Keycloak ya da Slack aynı protokolü uygulayarak eklenir. Arayüzler `serviszeka.connectors.base`,
Graph/Teams uygulaması `serviszeka.connectors.graph`, sahte `serviszeka.connectors.memory`;
bu modül tür -> uygulama kayıt defteridir (açılışta `connectors.setup.configure`).

Kurallar (uygulayıcılar için):
- HTTP yalnızca `httpx.AsyncClient` ile; kurucu `transport` parametresi alır (testte
  `httpx.MockTransport`, gerçek ağ yok). Gizli bilgiler `config.get_settings()`'ten okunur.
- Yazan her yöntem `dry_run` ve `idempotency_key` alır; `dry_run=True` dış sisteme yazmaz.
- Bağlayıcı politika/onay denetimi yapmaz: bunu `tools.execute_tool` ve eylem istekleri yapar.
"""

from __future__ import annotations

from typing import Any

from serviszeka.connectors.base import (
    IDENTITY,
    KNOWN_KINDS,
    LICENSE,
    MAIL,
    MESSAGING,
    IdentityProvider,
    LicenseProvider,
    MailProvider,
    Notifier,
)

# Geriye uyumlu adlar (temel katmandaki taslak protokoller)
IdentityConnector = IdentityProvider
MessagingConnector = Notifier

__all__ = [
    "IDENTITY",
    "KNOWN_KINDS",
    "LICENSE",
    "MAIL",
    "MESSAGING",
    "ConnectorRegistry",
    "IdentityConnector",
    "IdentityProvider",
    "LicenseProvider",
    "MailProvider",
    "MessagingConnector",
    "Notifier",
    "connectors",
]


class ConnectorRegistry:
    """Tür -> yapılandırılmış bağlayıcı. Yapılandırılmamış tür gerektiren araç kullanılamaz."""

    def __init__(self) -> None:
        self._items: dict[str, Any] = {}

    def register(self, kind: str, connector: Any) -> None:
        self._items[kind] = connector

    def unregister(self, kind: str) -> None:
        self._items.pop(kind, None)

    def get(self, kind: str) -> Any | None:
        return self._items.get(kind)

    def available(self, kind: str) -> bool:
        return kind in self._items

    def missing(self, kinds: tuple[str, ...] | list[str]) -> list[str]:
        return [k for k in kinds if not self.available(k)]

    def require(self, kind: str) -> Any:
        """Kayıtlı bağlayıcı; yoksa araç hatası (503). İşleyiciler bunu kullanır."""
        impl = self._items.get(kind)
        if impl is None:
            from serviszeka.tools.spec import ToolUnavailableError

            raise ToolUnavailableError(f"Bağlayıcı yapılandırılmamış: {kind}")
        return impl

    def snapshot(self) -> dict[str, Any]:
        return dict(self._items)

    def restore(self, items: dict[str, Any]) -> None:
        self._items = dict(items)


connectors = ConnectorRegistry()

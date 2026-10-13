"""Dış sistem bağlayıcıları (Faz 3): arayüzler, Microsoft Graph uygulaması, bellek içi sahte.

- `base`: `IdentityProvider`, `MailProvider`, `LicenseProvider`, `Notifier` protokolleri, hatalar.
- `graph`: Entra ID + Microsoft 365 (Graph, client credentials) + Teams gelen webhook.
- `memory`: `InMemoryDirectory` (testler, `SZ_CONNECTOR_PROVIDER=fake`, kapı değerlendirmesi).
- `setup`: ayarlardan bağlayıcıları kurar ve `tools.connectors` kayıt defterine işler.
"""

from serviszeka.connectors.base import (
    IDENTITY,
    KNOWN_KINDS,
    LICENSE,
    MAIL,
    MESSAGING,
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorNotSupportedError,
    DirectoryGroup,
    DirectoryUser,
    IdentityProvider,
    LicenseProvider,
    MailProvider,
    Notifier,
    SkuInfo,
    generate_temporary_password,
)

__all__ = [
    "IDENTITY",
    "KNOWN_KINDS",
    "LICENSE",
    "MAIL",
    "MESSAGING",
    "ConnectorAuthError",
    "ConnectorError",
    "ConnectorNotFoundError",
    "ConnectorNotSupportedError",
    "DirectoryGroup",
    "DirectoryUser",
    "IdentityProvider",
    "LicenseProvider",
    "MailProvider",
    "Notifier",
    "SkuInfo",
    "generate_temporary_password",
]

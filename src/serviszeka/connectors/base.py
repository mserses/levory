"""Dış sistem bağlayıcı arayüzleri (Faz 3): kimlik, posta, lisans, bildirim.

Varsayılan hedef yığın Microsoft Entra ID + Microsoft 365 (Graph) + Teams'tir
(`connectors.graph`); Keycloak / Slack aynı protokolleri uygulayarak eklenir. Testler, yerel
deneme (`SZ_CONNECTOR_PROVIDER=fake`) ve kapı değerlendirmesi `connectors.memory.InMemoryDirectory`
sahtesini kullanır.

Kurallar (uygulayıcılar için):
- HTTP yalnızca `httpx.AsyncClient`; kurucu `transport` alır (testte `httpx.MockTransport`).
- Gizli bilgiler `config.Settings`'ten (SZ_ önekli ortam değişkenleri) gelir; koda yazılmaz.
- Yazan her yöntem `dry_run` ve `idempotency_key` alır. `dry_run=True` dış sisteme yazmaz (okuma
  yapabilir). Yazma işlemleri doğası gereği tekrarlanabilir uygulanır (ör. zaten üye -> başarı).
- Bağlayıcı politika/onay denetimi yapmaz: bunu `tools.execute_tool` ve eylem istekleri yapar.
- Dönen sözlükler JSON'a uygundur ve **asla gizli bilgi (parola) içermez**.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# Bağlayıcı türleri (ToolSpec.connectors değerleri)
IDENTITY = "identity"  # Entra ID (Graph) / Keycloak Admin API
MAIL = "mail"  # Microsoft 365 posta (Graph / Exchange Online)
LICENSE = "license"  # Microsoft 365 lisansları (Graph subscribedSkus / assignLicense)
MESSAGING = "messaging"  # Kullanıcı/kanal bildirimi (Teams webhook + Graph sendMail) / Slack
KNOWN_KINDS: frozenset[str] = frozenset({IDENTITY, MAIL, LICENSE, MESSAGING})

MailboxAccess = Literal["full_access", "send_as"]


# --- hatalar ------------------------------------------------------------------------------------


class ConnectorError(Exception):
    """Dış sistem işlemi başarısız (mesaj Türkçe; gizli bilgi içermez)."""


class ConnectorNotFoundError(ConnectorError, LookupError):
    """Kullanıcı / grup / lisans / kanal dış sistemde bulunamadı."""


class ConnectorNotSupportedError(ConnectorError, NotImplementedError):
    """Bu sağlayıcı işlemi desteklemiyor (ör. Entra ID bulutta hesap kilidi açma)."""


class ConnectorAuthError(ConnectorError):
    """Dış sisteme kimlik doğrulanamadı (belirteç alınamadı / yetki yok)."""


# --- veri türleri ----------------------------------------------------------------------------------


@dataclass
class DirectoryUser:
    id: str
    user_principal_name: str
    display_name: str = ""
    mail: str | None = None
    account_enabled: bool = True
    department: str | None = None
    job_title: str | None = None
    # Entra `otherMails`: hassas bildirimler (geçici parola) yalnızca buraya gönderilir
    other_mails: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DirectoryGroup:
    id: str
    display_name: str = ""
    security_enabled: bool = True
    # Entra: isAssignableToRole=true ya da yapılandırmada ayrıcalıklı grup
    privileged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkuInfo:
    sku_id: str
    sku_part_number: str
    enabled: int = 0
    consumed: int = 0

    @property
    def available(self) -> int:
        return max(self.enabled - self.consumed, 0)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "available": self.available}


# --- protokoller -------------------------------------------------------------------------------------


@runtime_checkable
class IdentityProvider(Protocol):
    """Kimlik dizini (Entra ID varsayılan). `user` kimlik ya da UPN; `group` grup kimliği."""

    name: str

    async def get_user(self, user: str) -> DirectoryUser | None: ...

    async def get_manager(self, user: str) -> DirectoryUser | None: ...

    async def list_groups(self, user: str) -> list[DirectoryGroup]: ...

    async def get_group(self, group: str) -> DirectoryGroup | None: ...

    async def is_privileged_group(self, group: str) -> bool:
        """Ayrıcalıklı mı; bilinmeyen grup ayrıcalıklı sayılır (güvenli varsayılan)."""
        ...

    async def add_to_group(
        self, user: str, group: str, *, dry_run: bool = False, idempotency_key: str | None = None
    ) -> dict[str, Any]: ...

    async def remove_from_group(
        self, user: str, group: str, *, dry_run: bool = False, idempotency_key: str | None = None
    ) -> dict[str, Any]: ...

    async def revoke_sessions(
        self, user: str, *, dry_run: bool = False, idempotency_key: str | None = None
    ) -> dict[str, Any]: ...

    async def set_account_enabled(
        self,
        user: str,
        enabled: bool,
        *,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    async def reset_password(
        self,
        user: str,
        new_password: str,
        *,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Geçici parola atar (ilk girişte değiştirilmeli). Dönen sözlük parolayı içermez."""
        ...

    async def reset_mfa(
        self, user: str, *, dry_run: bool = False, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Parola dışındaki kimlik doğrulama yöntemlerini siler (kullanıcı MFA'yı yeniden kaydeder)."""
        ...

    async def unlock_account(
        self, user: str, *, dry_run: bool = False, idempotency_key: str | None = None
    ) -> dict[str, Any]: ...


@runtime_checkable
class MailProvider(Protocol):
    name: str

    async def grant_shared_access(
        self,
        mailbox: str,
        user: str,
        access: MailboxAccess,
        *,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    async def send_mail(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class LicenseProvider(Protocol):
    name: str

    async def list_skus(self) -> list[SkuInfo]: ...

    async def assign_license(
        self, user: str, sku_id: str, *, dry_run: bool = False, idempotency_key: str | None = None
    ) -> dict[str, Any]: ...


@runtime_checkable
class Notifier(Protocol):
    """Kullanıcıya ve kanala bildirim (Teams + Graph posta varsayılan; Slack eklenebilir).

    `sensitive=True` (ör. geçici parola) yalnızca kullanıcının doğrulanmış ikincil adresine
    gider; ikincil adres yoksa `ConnectorError` yükseltilir (asla birincil posta kutusuna değil:
    hesabı kilitli/parolasız kullanıcı onu okuyamaz, ele geçirilmiş hesapta saldırgan okur).
    """

    name: str

    async def send_to_user(
        self,
        user: str,
        subject: str,
        text: str,
        *,
        sensitive: bool = False,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    async def post_to_channel(
        self,
        channel: str,
        text: str,
        *,
        dry_run: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...


# --- yardımcılar ---------------------------------------------------------------------------------------

_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%*-_+"


def generate_temporary_password(length: int = 16) -> str:
    """Kriptografik rastgele geçici parola (büyük/küçük harf, rakam, sembol içerir)."""
    while True:
        pw = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(not c.isalnum() for c in pw)
        ):
            return pw

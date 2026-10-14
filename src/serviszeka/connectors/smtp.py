"""SMTP e-posta bildiricisi (`Notifier`): posta sağlayıcısı olmayan kimlik yığınları için (Keycloak).

Keycloak'ın kendi e-posta gönderimi (`execute-actions-email`) yalnızca birincil adrese bağlantı
yollar; geçici parola sözleşmesini karşılamaz. Bu bildirici:

- `send_to_user(sensitive=True)`: kullanıcının **ikincil** adresine (`DirectoryUser.other_mails[0]`,
  Keycloak'ta `SZ_KEYCLOAK_SECONDARY_EMAIL_ATTRIBUTE` özniteliği) gönderir; ikincil adres yoksa
  `ConnectorError` (asla birincil posta kutusuna değil).
- `send_to_user(sensitive=False)`: birincil e-posta (`mail`, yoksa e-posta biçimli UPN).
- `post_to_channel`: desteklenmez (`ConnectorNotSupportedError`; kanal için Slack kullanın).

Ayarlar `SZ_SMTP_HOST`, `SZ_SMTP_PORT` (587), `SZ_SMTP_SENDER`, isteğe bağlı `SZ_SMTP_USERNAME` +
`SZ_SMTP_PASSWORD` (gizli), `SZ_SMTP_STARTTLS` (true), `SZ_SMTP_SSL` (örtük TLS, false). Gönderim
`smtplib` ile ayrı iş parçacığında; testlerde `send` yerine sahte gönderici verilir (ağ yok). Hata
mesajı sunucu yanıtını/gizli bilgiyi taşımaz (yalnızca istisna türü). Mesaj gövdesi hiçbir zaman
loglanmaz.

**TLS zorunlu:** STARTTLS (sunucu sunmuyorsa düz metne düşülmez, kimlik bilgisi gönderilmez:
`SMTPNotSupportedError` -> `ConnectorError`) ya da örtük TLS; ikisi de kapalıysa bildirici kurulmaz.
TLS'siz SMTP yalnızca `SZ_SMTP_ALLOW_PLAINTEXT=true` ve `SZ_ENVIRONMENT=development` (yerel posta
yakalayıcı) iken. **Başlık enjeksiyonu:** konu / alıcı satır sonu içerirse `email` kütüphanesinin
`ValueError`'ı içeriği taşımayan `ConnectorError`'a çevrilir; hiçbir şey gönderilmez.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl as _ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from serviszeka.connectors.base import (
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorNotSupportedError,
)

Sender = Callable[[EmailMessage], None]


class SmtpNotifier:
    name = "smtp"

    def __init__(
        self,
        identity: Any,
        *,
        host: str,
        sender: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        ssl: bool = False,
        allow_plaintext: bool = False,
        timeout: float = 10.0,
        send: Sender | None = None,
    ) -> None:
        if not host or not sender:
            raise ConnectorError("SMTP için sunucu ve gönderen adresi gerekli.")
        if starttls and ssl:
            raise ConnectorError("SMTP: STARTTLS ve örtük TLS (SZ_SMTP_SSL) birlikte açılamaz.")
        if not (starttls or ssl or allow_plaintext):
            raise ConnectorError(
                "SMTP TLS'siz kullanılamaz: STARTTLS ya da örtük TLS açın "
                "(düz metin yalnızca geliştirmede SZ_SMTP_ALLOW_PLAINTEXT=true)."
            )
        self.identity = identity
        self.host = host
        self.port = port
        self.sender = sender
        self._username = username
        self._password = password
        self.starttls = starttls
        self.ssl = ssl
        self.timeout = timeout
        self._send = send or self._smtp_send

    def __repr__(self) -> str:  # parola repr'e girmez
        return f"SmtpNotifier(host={self.host!r}, port={self.port}, sender={self.sender!r})"

    def _connect(self) -> smtplib.SMTP:
        if self.ssl:
            return smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=_ssl.create_default_context()
            )
        return smtplib.SMTP(self.host, self.port, timeout=self.timeout)

    def _smtp_send(self, message: EmailMessage) -> None:
        with self._connect() as smtp:
            if self.starttls and not self.ssl:
                # Sunucu STARTTLS sunmazsa SMTPNotSupportedError: düz metne düşülmez
                smtp.starttls(context=_ssl.create_default_context())
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(message)

    def _build(self, target: str, subject: str, text: str, key: str | None) -> EmailMessage:
        """Mesajı kurar; başlıkta satır sonu (enjeksiyon) varsa içeriksiz `ConnectorError`."""
        try:
            message = EmailMessage()
            message["From"] = self.sender
            message["To"] = target
            message["Subject"] = subject
            if key:
                message["X-ServisZeka-Idempotency-Key"] = key
            message.set_content(text)
        except (ValueError, TypeError):
            # ValueError metni değeri taşımaz ama zincir (__context__) taşıyabilir: kesilir
            raise ConnectorError(
                "E-posta başlığı geçersiz (satır sonu ya da geçersiz karakter); gönderilmedi."
            ) from None
        return message

    async def send_to_user(
        self, user, subject, text, *, sensitive=False, dry_run=False, idempotency_key=None
    ):
        if self.identity is None:
            raise ConnectorError("SMTP bildiricisi için kimlik sağlayıcısı gerekli.")
        found = await self.identity.get_user(user)
        if found is None:
            raise ConnectorNotFoundError(f"Kullanıcı dizinde bulunamadı: {user}")
        if sensitive:
            if not found.other_mails:
                raise ConnectorError(
                    "Hassas bildirim için kullanıcının doğrulanmış ikincil adresi yok."
                )
            target = found.other_mails[0]
        else:
            upn = found.user_principal_name or ""
            target = found.mail or (upn if "@" in upn else "")
            if not target:
                raise ConnectorNotFoundError(f"Kullanıcının e-posta adresi yok: {user}")
        channel = "secondary_mail" if sensitive else "mail"
        result = {"dry_run": dry_run, "user_id": found.id, "channel": channel}
        if dry_run:
            return result
        message = self._build(target, subject, text, idempotency_key)
        try:
            await asyncio.to_thread(self._send, message)
        except (OSError, smtplib.SMTPException) as exc:
            # Sunucu yanıtı adres/parola parçası taşıyabilir: yalnızca tür
            raise ConnectorError(f"E-posta gönderilemedi ({type(exc).__name__}).") from None
        finally:
            del message
        return result

    async def post_to_channel(self, channel, text, *, dry_run=False, idempotency_key=None):
        raise ConnectorNotSupportedError(
            "SMTP bildiricisi kanal mesajı göndermez (Slack ya da Teams yapılandırın)."
        )


def build_smtp(settings: Any, identity: Any, *, send: Sender | None = None) -> SmtpNotifier | None:
    """`SZ_SMTP_HOST` + `SZ_SMTP_SENDER` doluysa SMTP bildiricisi; değilse None."""
    host = (getattr(settings, "smtp_host", None) or "").strip()
    sender = (getattr(settings, "smtp_sender", None) or "").strip()
    if not host or not sender:
        return None
    allow_plaintext = bool(getattr(settings, "smtp_allow_plaintext", False))
    if allow_plaintext and getattr(settings, "environment", "production") != "development":
        raise ConnectorError(
            "SZ_SMTP_ALLOW_PLAINTEXT=true yalnızca SZ_ENVIRONMENT=development iken kullanılabilir."
        )
    secret = getattr(settings, "smtp_password", None)
    password = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    return SmtpNotifier(
        identity,
        host=host,
        sender=sender,
        port=int(getattr(settings, "smtp_port", 587) or 587),
        username=(getattr(settings, "smtp_username", None) or None),
        password=password or None,
        starttls=bool(getattr(settings, "smtp_starttls", True)),
        ssl=bool(getattr(settings, "smtp_ssl", False)),
        allow_plaintext=allow_plaintext,
        send=send,
    )

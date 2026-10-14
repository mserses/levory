"""Slack bağlayıcısı: Web API (bot belirteci) ve isteğe bağlı gelen webhook.

Uygulananlar:
- `SlackClient`: `https://slack.com/api/<yöntem>` ince istemcisi (yalnızca httpx; testte
  `httpx.MockTransport`). Slack semantiği: **HTTP 200 + `{"ok": false, "error": ...}` hatadır**;
  429 + `Retry-After` -> en fazla bir kez, üst sınırlı bekleme (`max_retry_wait`) sonrası yeniden
  deneme; hatalar `ConnectorError` türlerine çevrilir. Belirteç günlüğe, hata mesajına ya da
  `repr`'e asla girmez.
- `SlackNotifier` (`connectors.base.Notifier`, tür `messaging`): `post_to_channel` yalnızca
  yapılandırılmış kanallara (`SZ_SLACK_DEFAULT_CHANNEL` -> "default", `SZ_SLACK_ONCALL_CHANNEL` ->
  "oncall", ya da bu kanal kimliklerinin kendisi) `chat.postMessage`; `send_to_user`
  `users.lookupByEmail` (e-posta; dizin kimliği verilirse kimlik sağlayıcıdan posta/UPN) ->
  `conversations.open` -> `chat.postMessage` ile DM. `sensitive=True` (geçici parola) **asla Slack'e
  gitmez**: yapılandırılmışsa ikincil adrese gönderen devralınan bildiriciye (Graph/sahte) devredilir,
  değilse `ConnectorError`.
- `SlackOncallNotifier` (`notifications.Notifier`, `notify(event, payload)`): nöbetçi uyarısını
  `SZ_SLACK_ONCALL_CHANNEL` kanalına (bot) ya da `SZ_SLACK_WEBHOOK_URL` webhook'una gönderir.
  **Tekillik** (kuyruk en az bir kez gönderir): bot kipinde mesaj `metadata`
  (`event_type=serviszeka_oncall`, `event_payload.idempotency_key`) taşır; yeniden denemede
  (`possibly_delivered=True`) önce `conversations.history` (son `ONCALL_HISTORY_LOOKBACK` sn, en çok
  `ONCALL_HISTORY_LIMIT` mesaj, `include_all_metadata`) okunur, anahtar varsa yeniden gönderilmez
  (`{"deduplicated": True}`). Slack `chat.postMessage` belgelenmiş bir istemci tekillik anahtarı
  (`client_msg_id`) kabul etmediği için meta veri + geçmiş kullanılır. Geçmiş okunamazsa (kapsam
  yok, hata) yine gönderilir: çift uyarı kaçırılan uyarıya tercih edilir. Webhook kipinde geçmiş
  okunamaz; anahtar mesajın bağlam satırında görünür (kalan pencere docs/faz4.md).
- `SlackWebhook`: gelen webhook (tek kanala bağlı; yanıt düz metin `ok`, hata metni ör.
  `no_service`, `invalid_payload`).

Kaçışlama: kullanıcı/yapay zekâ metninin tamamı `escape` ile `&`, `<`, `>` -> `&amp;`, `&lt;`,
`&gt;` olur; böylece `<!channel>`, `<!here>`, `<@U123>`, `<#C1>`, `<url|metin>` sözdizimi
çalışmaz. Ek savunma: düz `@channel` / `@here` / `@everyone` sıfır genişlikli boşlukla bozulur ve
`link_names` gönderilmez (varsayılan kapalı).

Gerekli bot belirteci kapsamları — **doğrulanmalı** (docs/faz3.md "doğrulanmalı"):
`chat:write` (kanal + DM gönderimi; bot kanala üye olmalı, değilse `not_in_channel`),
`im:write` (`conversations.open`), `users:read` + `users:read.email` (`users.lookupByEmail`);
nöbetçi tekilliği için `channels:history` (genel kanal) / `groups:history` (özel kanal).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

import httpx

from serviszeka.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorNotSupportedError,
)

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_RETRY_WAIT = 30.0
CONTEXT_LABEL = "ServisZeka"

# Block Kit sınırları (Slack belgeleri; doğrulanmalı)
HEADER_MAX = 150
SECTION_MAX = 3000
FIELD_MAX = 2000
FIELDS_MAX = 10
CONTEXT_ELEMENTS_MAX = 10
FALLBACK_TEXT_MAX = 40_000

# Slack kullanıcı kimliği (U.../W...); dizin kimlikleri (GUID) ve kısa adlar eşleşmez
_SLACK_USER_ID = re.compile(r"^[UW][A-Z0-9]{6,}$")
_BROADCAST_WORDS = re.compile(r"@(channel|here|everyone)\b", re.IGNORECASE)
_SAFE_ERROR = re.compile(r"[^a-z0-9_.:,\- ]", re.IGNORECASE)

# Slack `error` kodu -> istisna türü
_AUTH_ERRORS = frozenset(
    {
        "not_authed",
        "invalid_auth",
        "account_inactive",
        "token_revoked",
        "token_expired",
        "no_permission",
        "missing_scope",
        "not_allowed_token_type",
        "access_denied",
        "ekm_access_denied",
        "team_access_not_granted",
    }
)
_NOT_FOUND_ERRORS = frozenset({"channel_not_found", "users_not_found", "user_not_found"})


def _safe(value: Any, limit: int = 80) -> str:
    """Slack hata kodunu mesaja güvenle koyar (yalnızca kısa, düz karakterler)."""
    return _SAFE_ERROR.sub("", str(value))[:limit]


# --- kaçışlama ve Block Kit -------------------------------------------------------------------------


def escape(text: Any) -> str:
    """Slack metin kaçışlaması: `&`, `<`, `>` (bu sırayla) + düz yayın bahsetmelerini bozma.

    Girdi önceden kaçışlanmış olsa bile yeniden kaçışlanır (kullanıcı metnine güvenilmez).
    """
    out = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _BROADCAST_WORDS.sub("@\u200b\\1", out)


def _clip(escaped: str, limit: int) -> str:
    """Kaçışlanmış metni `limit`e keser; `&amp;` gibi varlıkları ortadan bölmez."""
    if len(escaped) <= limit:
        return escaped
    cut = escaped[: limit - 1]
    amp = cut.rfind("&")
    if amp > cut.rfind(";"):
        cut = cut[:amp]
    return cut + "…"


def _text(value: Any, limit: int) -> str:
    return _clip(escape(value), limit)


def build_blocks(
    title: str | None,
    text: str | None,
    *,
    fields: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
    context: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Başlık (header, plain_text), gövde (section, mrkdwn), alanlar ve bağlam blokları.

    Tüm metin `escape` ile kaçışlanır ve Block Kit sınırlarına kesilir. Not: `plain_text`
    başlıkta varlıkların (`&amp;`) nasıl gösterildiği **doğrulanmalı**; güvenlik için orada da
    kaçışlanır.
    """
    blocks: list[dict[str, Any]] = []
    if title:
        blocks.append(
            {
                "type": "header",
                "text": {"type": "plain_text", "text": _text(title, HEADER_MAX), "emoji": True},
            }
        )
    if text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _text(text, SECTION_MAX)}})
    items = list(fields.items() if isinstance(fields, Mapping) else (fields or ()))
    items = [(k, v) for k, v in items if v not in (None, "")][:FIELDS_MAX]
    if items:
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": _clip(f"*{escape(label)}*\n{escape(value)}", FIELD_MAX),
                    }
                    for label, value in items
                ],
            }
        )
    elements = [c for c in (context or ()) if c][:CONTEXT_ELEMENTS_MAX]
    if elements:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": _text(c, SECTION_MAX)} for c in elements],
            }
        )
    return blocks


def build_message(
    title: str | None,
    text: str,
    *,
    fields: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
    context: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Mesaj gövdesi: `text` (bildirim/erişilebilirlik yedeği, kaçışlı) + `blocks`."""
    fallback = escape(text) if not title else f"{escape(title)}\n{escape(text)}"
    return {
        "text": _clip(fallback, FALLBACK_TEXT_MAX),
        "blocks": build_blocks(title, text, fields=fields, context=context),
    }


# --- HTTP ------------------------------------------------------------------------------------------


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(float(resp.headers.get("Retry-After", "1")), 0.0)
    except ValueError:
        return 1.0


Sleep = Callable[[float], Awaitable[None]]


async def _send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    what: str,
    sleep: Sleep,
    max_retry_wait: float,
) -> httpx.Response:
    """İsteği gönderir; 429'da `Retry-After` <= üst sınırsa bir kez bekleyip yeniden dener."""
    for attempt in (0, 1):
        try:
            resp = await send()
        except httpx.HTTPError as exc:
            raise ConnectorError(f"{what} isteği başarısız: {type(exc).__name__}") from exc
        if resp.status_code != 429:
            return resp
        wait = _retry_after(resp)
        if attempt == 1 or wait > max_retry_wait:
            raise ConnectorError(
                f"{what} istek sınırı (429); {wait:g} sn sonra yeniden deneyin."
            )
        await sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


class SlackClient:
    """Bot belirteçli Slack Web API istemcisi."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = SLACK_API,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        max_retry_wait: float = DEFAULT_MAX_RETRY_WAIT,
    ) -> None:
        if not token:
            raise ConnectorAuthError("Slack bot belirteci (SZ_SLACK_BOT_TOKEN) tanımlı değil.")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport
        self._sleep = sleep
        self.max_retry_wait = max_retry_wait

    def __repr__(self) -> str:
        return f"SlackClient(base_url={self.base_url!r})"

    async def api(
        self,
        method: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        not_found: str | None = None,
    ) -> dict[str, Any]:
        """Web API yöntemini çağırır; `ok: true` gövdeyi döndürür, aksi halde hata yükseltir.

        `params` verilirse GET (ör. `users.lookupByEmail`), değilse JSON gövdeli POST.
        """
        url = f"{self.base_url}/{method}"
        headers = {"Authorization": f"Bearer {self._token}"}

        async def send() -> httpx.Response:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as c:
                if params is not None:
                    return await c.get(url, params=params, headers=headers)
                return await c.post(url, json=json or {}, headers=headers)

        resp = await _send_with_retry(
            send, what=f"Slack {method}", sleep=self._sleep, max_retry_wait=self.max_retry_wait
        )
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 300 or not isinstance(body, dict):
            code = body.get("error") if isinstance(body, dict) else ""
            raise ConnectorError(f"Slack {method} hatası (HTTP {resp.status_code} {_safe(code)}).")
        if body.get("ok") is not True:
            raise _translate(method, body, not_found)
        return body

    async def post_message(self, channel: str, message: dict[str, Any]) -> dict[str, Any]:
        """`chat.postMessage`; bağlantı önizlemeleri kapalı (yapay zekâ metnindeki URL açılmaz)."""
        payload = {"channel": channel, **message, "unfurl_links": False, "unfurl_media": False}
        return await self.api(
            "chat.postMessage", json=payload, not_found=f"Slack kanalı bulunamadı: {channel}"
        )

    async def lookup_user_by_email(self, email: str) -> str:
        """`users.lookupByEmail` -> Slack kullanıcı kimliği (silinmiş kullanıcı bulunamadı sayılır)."""
        body = await self.api(
            "users.lookupByEmail",
            params={"email": email},
            not_found=f"Slack'te bu e-postayla kullanıcı yok: {email}",
        )
        user = body.get("user") or {}
        if not user.get("id") or user.get("deleted"):
            raise ConnectorNotFoundError(f"Slack'te bu e-postayla etkin kullanıcı yok: {email}")
        return str(user["id"])

    async def open_dm(self, slack_user_id: str) -> str:
        """`conversations.open` `{"users": "U..."}` -> DM kanal kimliği (varsa mevcut olanı döner)."""
        body = await self.api("conversations.open", json={"users": slack_user_id})
        channel = (body.get("channel") or {}).get("id")
        if not channel:
            raise ConnectorError("Slack conversations.open kanal kimliği döndürmedi.")
        return str(channel)


def _translate(method: str, body: dict[str, Any], not_found: str | None) -> ConnectorError:
    error = _safe(body.get("error") or "unknown_error")
    if error in _AUTH_ERRORS:
        needed = f"; gereken kapsam: {_safe(body['needed'])}" if body.get("needed") else ""
        return ConnectorAuthError(f"Slack yetki hatası ({method}: {error}{needed}).")
    if error in _NOT_FOUND_ERRORS:
        return ConnectorNotFoundError(f"{not_found or 'Slack kaydı bulunamadı'} ({error}).")
    if error == "not_in_channel":
        return ConnectorError(f"Slack botu kanala üye değil ({method}: {error}); botu kanala ekleyin.")
    return ConnectorError(f"Slack hatası ({method}: {error}).")


class SlackWebhook:
    """Gelen webhook: tek kanala bağlı, `channel` gönderilmez. URL gizli bilgidir (repr/hata dışı)."""

    def __init__(
        self,
        url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        max_retry_wait: float = DEFAULT_MAX_RETRY_WAIT,
    ) -> None:
        if not url or not url.startswith("https://"):
            raise ConnectorError("Slack webhook URL'si (SZ_SLACK_WEBHOOK_URL) https olmalı.")
        self._url = url
        self._timeout = timeout
        self._transport = transport
        self._sleep = sleep
        self.max_retry_wait = max_retry_wait

    def __repr__(self) -> str:
        return "SlackWebhook(url=<gizli>)"

    async def post(self, message: dict[str, Any]) -> None:
        async def send() -> httpx.Response:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as c:
                return await c.post(self._url, json=message)

        resp = await _send_with_retry(
            send, what="Slack webhook", sleep=self._sleep, max_retry_wait=self.max_retry_wait
        )
        if resp.status_code >= 300:
            raise ConnectorError(
                f"Slack webhook'u reddetti (HTTP {resp.status_code} {_safe(resp.text, 40)})."
            )


# --- Notifier (connectors.base) ---------------------------------------------------------------------


class SlackNotifier:
    """`connectors.base.Notifier` Slack uygulaması (kanal mesajı + kullanıcıya DM)."""

    name = "slack"

    def __init__(
        self,
        client: SlackClient | None,
        *,
        channels: Mapping[str, str] | None = None,
        default_channel: str | None = None,
        webhook: SlackWebhook | None = None,
        identity: Any | None = None,
        sensitive_delegate: Any | None = None,
        context_label: str = CONTEXT_LABEL,
    ) -> None:
        if client is None and webhook is None:
            raise ConnectorError("Slack için bot belirteci ya da webhook URL'si gerekli.")
        self.client = client
        self.webhook = webhook
        self.channels = dict(channels or {})
        self.default_channel = default_channel
        self.identity = identity
        self.sensitive_delegate = sensitive_delegate
        self.context_label = context_label

    def _resolve_channel(self, channel: str) -> str:
        """Yalnızca yapılandırılmış kanallar: mantıksal ad ya da yapılandırılmış kanal kimliği."""
        if channel in self.channels:
            return self.channels[channel]
        if channel in self.channels.values():
            return channel
        raise ConnectorNotFoundError(f"Slack kanalı yapılandırılmamış: {channel}")

    def _message(self, title: str | None, text: str) -> dict[str, Any]:
        return build_message(title, text, context=[self.context_label] if self.context_label else None)

    async def post_to_channel(self, channel, text, *, dry_run=False, idempotency_key=None):
        if self.client is None:
            # Webhook kipi: webhook tek kanala bağlıdır
            allowed = {"default", *([self.default_channel] if self.default_channel else [])}
            if channel not in allowed:
                raise ConnectorNotFoundError(f"Slack kanalı yapılandırılmamış: {channel}")
            if dry_run:
                return {"dry_run": True, "channel": channel}
            assert self.webhook is not None
            await self.webhook.post(self._message(None, text))
            return {"dry_run": False, "channel": channel}
        target = self._resolve_channel(channel)
        if dry_run:
            return {"dry_run": True, "channel": channel}
        body = await self.client.post_message(target, self._message(None, text))
        return {"dry_run": False, "channel": channel, "ts": body.get("ts")}

    async def _slack_user_id(self, user: str) -> str:
        user = user.strip()
        assert self.client is not None
        if _SLACK_USER_ID.match(user):
            return user
        if "@" in user:
            return await self.client.lookup_user_by_email(user)
        if self.identity is not None:
            found = await self.identity.get_user(user)
            email = (found.mail or found.user_principal_name) if found else None
            if email:
                return await self.client.lookup_user_by_email(email)
        raise ConnectorNotFoundError(f"Slack kullanıcısı çözümlenemedi: {user}")

    async def send_to_user(
        self, user, subject, text, *, sensitive=False, dry_run=False, idempotency_key=None
    ):
        if sensitive:
            # Geçici parola gibi hassas içerik Slack DM'ine gitmez (ele geçirilmiş/kilitli hesap)
            if self.sensitive_delegate is None:
                raise ConnectorError(
                    "Hassas bildirim Slack'e gönderilmez; ikincil adrese gönderen bildirici yok."
                )
            return await self.sensitive_delegate.send_to_user(
                user,
                subject,
                text,
                sensitive=True,
                dry_run=dry_run,
                idempotency_key=idempotency_key,
            )
        if self.client is None:
            raise ConnectorNotSupportedError(
                "Slack webhook kipinde kullanıcıya DM gönderilemez (SZ_SLACK_BOT_TOKEN gerekir)."
            )
        slack_id = await self._slack_user_id(user)
        result = {"dry_run": dry_run, "user_id": user, "slack_user_id": slack_id, "channel": "slack_dm"}
        if dry_run:
            return result
        dm = await self.client.open_dm(slack_id)
        await self.client.post_message(dm, self._message(subject, text))
        return result


# --- nöbetçi (notifications.Notifier) ------------------------------------------------------------

_ONCALL_LABELS = {
    "incident_number": "Olay",
    "severity": "Önem",
    "current_priority": "Mevcut öncelik",
    "proposed_priority": "Önerilen öncelik",
    "outcome": "Karar",
    "requested_by": "İsteyen",
    "reason": "Gerekçe",
}


ONCALL_METADATA_TYPE = "serviszeka_oncall"
# Yeniden denemede geçmişte aranacak süre (kuyruk kirası 120 sn + geri çekilme en fazla 1 sa)
ONCALL_HISTORY_LOOKBACK = 6 * 3600
ONCALL_HISTORY_LIMIT = 200


class SlackOncallNotifier:
    """Nöbetçi uyarısı (`notifications.Notifier`): bot + nöbet kanalı ya da gelen webhook."""

    def __init__(
        self,
        client: SlackClient | None = None,
        *,
        channel: str | None = None,
        webhook: SlackWebhook | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not ((client is not None and channel) or webhook is not None):
            raise ConnectorError("Slack nöbetçi uyarısı için bot + kanal ya da webhook gerekli.")
        self.client = client
        self.channel = channel
        self.webhook = webhook
        self._clock = clock

    async def already_posted(self, idempotency_key: str) -> bool:
        """Nöbet kanalının yakın geçmişinde bu anahtarlı mesaj var mı (yalnızca bot kipi).

        Okunamazsa False (gönderim sürer; çift uyarı kaçırılan uyarıdan iyidir).
        """
        if self.client is None or not self.channel:
            return False
        try:
            body = await self.client.api(
                "conversations.history",
                params={
                    "channel": self.channel,
                    "oldest": f"{self._clock() - ONCALL_HISTORY_LOOKBACK:.6f}",
                    "limit": ONCALL_HISTORY_LIMIT,
                    "include_all_metadata": "true",
                },
            )
        except ConnectorError as exc:
            logger.warning("Slack nöbet kanalı geçmişi okunamadı (%s); uyarı yeniden gönderilir.", exc)
            return False
        for message in body.get("messages") or []:
            meta = message.get("metadata") if isinstance(message, dict) else None
            if not isinstance(meta, dict) or meta.get("event_type") != ONCALL_METADATA_TYPE:
                continue
            data = meta.get("event_payload")
            if isinstance(data, dict) and data.get("idempotency_key") == idempotency_key:
                return True
        return False

    async def notify(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        possibly_delivered: bool = False,
    ) -> dict[str, Any] | None:
        bot = self.client is not None and bool(self.channel)
        if bot and idempotency_key and possibly_delivered and await self.already_posted(
            idempotency_key
        ):
            return {"deduplicated": True}
        text = payload.get("message") or payload.get("title") or event
        severity = payload.get("severity") or payload.get("proposed_priority")
        title = f"Nöbetçi uyarısı ({severity})" if severity else "Nöbetçi uyarısı"
        fields = [
            (_ONCALL_LABELS.get(k, k), v)
            for k, v in payload.items()
            if k in _ONCALL_LABELS and isinstance(v, (str, int, float))
        ]
        context = f"{CONTEXT_LABEL} · {event}"
        if idempotency_key:
            # İnsan da yinelemeyi tanıyabilsin (webhook kipinde tek tekillik ipucu)
            context += f" · ref {idempotency_key[:120]}"
        message = build_message(title, str(text), fields=fields, context=[context])
        if bot:
            assert self.client is not None and self.channel
            if idempotency_key:
                message["metadata"] = {
                    "event_type": ONCALL_METADATA_TYPE,
                    "event_payload": {"idempotency_key": idempotency_key},
                }
            await self.client.post_message(self.channel, message)
        else:
            assert self.webhook is not None
            await self.webhook.post(message)
        return None


# --- ayarlardan kurulum ------------------------------------------------------------------------------


def _secret(value: Any) -> str | None:
    if value is None:
        return None
    raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    return raw or None


def build_slack(
    settings: Any,
    *,
    identity: Any | None = None,
    sensitive_delegate: Any | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SlackNotifier | None:
    """`SZ_SLACK_BOT_TOKEN` ya da `SZ_SLACK_WEBHOOK_URL` yoksa None."""
    token = _secret(settings.slack_bot_token)
    hook_url = _secret(settings.slack_webhook_url)
    if not token and not hook_url:
        return None
    channels: dict[str, str] = {}
    if settings.slack_default_channel:
        channels["default"] = settings.slack_default_channel
    if settings.slack_oncall_channel:
        channels["oncall"] = settings.slack_oncall_channel
    return SlackNotifier(
        SlackClient(token, transport=transport) if token else None,
        channels=channels,
        default_channel=settings.slack_default_channel,
        webhook=SlackWebhook(hook_url, transport=transport) if hook_url else None,
        identity=identity,
        sensitive_delegate=sensitive_delegate,
    )


def build_slack_oncall(
    settings: Any, *, transport: httpx.AsyncBaseTransport | None = None
) -> SlackOncallNotifier | None:
    """Bot belirteci + `SZ_SLACK_ONCALL_CHANNEL` öncelikli; yoksa webhook; ikisi de yoksa None."""
    token = _secret(settings.slack_bot_token)
    hook_url = _secret(settings.slack_webhook_url)
    if token and settings.slack_oncall_channel:
        return SlackOncallNotifier(
            SlackClient(token, transport=transport), channel=settings.slack_oncall_channel
        )
    if hook_url:
        return SlackOncallNotifier(webhook=SlackWebhook(hook_url, transport=transport))
    return None

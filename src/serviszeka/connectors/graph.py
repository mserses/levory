"""Microsoft Entra ID + Microsoft 365 (Graph) + Teams bağlayıcıları.

Kimlik doğrulama: OAuth 2.0 **client credentials** (uygulama izinleri). Belirteç
`POST {login}/{tenant}/oauth2/v2.0/token` (`scope=https://graph.microsoft.com/.default`) ile alınır
ve süresinden 60 sn önceye kadar önbellekte tutulur; 401 alınırsa bir kez yenilenir.

Gerekli uygulama izinleri (yönetici onayı) — **doğrulanmalı** (docs/faz3.md "doğrulanmalı"):
- `User.Read.All` (kullanıcı, yönetici), `GroupMember.Read.All` (memberOf, grup)
- `GroupMember.ReadWrite.All` (gruba ekleme/çıkarma)
- `User.RevokeSessions.All` ya da `User.ReadWrite.All` (oturum iptali)
- `User.ReadWrite.All` (accountEnabled); ayrıcalıklı kullanıcıları devre dışı bırakmak için
  uygulamaya ek dizin rolü gerekebilir
- `UserAuthenticationMethod.ReadWrite.All` (kimlik doğrulama yöntemleri: parola, MFA)
- `Organization.Read.All` (subscribedSkus), `LicenseAssignment.ReadWrite.All` ya da
  `User.ReadWrite.All` (assignLicense)
- `Mail.Send` (gönderen posta kutusundan e-posta; uygulama erişim ilkesiyle o posta kutusuna
  sınırlandırılması önerilir)

Teams kanal mesajı **gelen webhook** URL'si ile gönderilir (ek Graph izni gerekmez); URL'ler
`SZ_TEAMS_WEBHOOK_URLS` ayarındadır ve gizli bilgi gibi saklanmalıdır.

Desteklenmeyenler (açık `ConnectorNotSupportedError`, davranış uydurulmaz):
- Hesap kilidi açma: Entra ID bulutta doğrudan "kilidi aç" API'si yoktur (Akıllı Kilitleme süre
  dolunca kendiliğinden açılır). Şirket içi AD kilidi (`lockoutTime`) LDAP ile açılır; kapsam dışı.
- Paylaşılan posta kutusu izni (FullAccess / SendAs): Graph v1.0'da posta kutusu izni API'si yoktur;
  Exchange Online PowerShell (`Add-MailboxPermission`, `Add-RecipientPermission`) gerekir.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from serviszeka.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorNotSupportedError,
    DirectoryGroup,
    DirectoryUser,
    MailboxAccess,
    SkuInfo,
)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
USER_SELECT = (
    "id,displayName,userPrincipalName,mail,accountEnabled,department,jobTitle,otherMails"
)
GROUP_SELECT = "id,displayName,securityEnabled,isAssignableToRole"
MAX_PAGES = 20
TOKEN_SKEW_SECONDS = 60

# Kimlik doğrulama yöntemi türü -> silme uç noktası kesimi (/users/{id}/authentication/{kesim}/{id}).
# Parola yöntemi silinmez. Doğrulanmalı: varsayılan yöntem, başka yöntemler varken silinemeyebilir.
MFA_METHOD_SEGMENTS: dict[str, str] = {
    "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod": "microsoftAuthenticatorMethods",
    "#microsoft.graph.phoneAuthenticationMethod": "phoneMethods",
    "#microsoft.graph.softwareOathAuthenticationMethod": "softwareOathMethods",
    "#microsoft.graph.fido2AuthenticationMethod": "fido2Methods",
    "#microsoft.graph.emailAuthenticationMethod": "emailMethods",
    "#microsoft.graph.temporaryAccessPassAuthenticationMethod": "temporaryAccessPassMethods",
    "#microsoft.graph.windowsHelloForBusinessAuthenticationMethod": (
        "windowsHelloForBusinessMethods"
    ),
}


def _seg(value: str) -> str:
    """URL yol kesimi (UPN'deki @ ve . korunur; / ve # gibi karakterler kaçışlanır).

    `.`/`..` tek başına httpx'te yol gezinmesi olur (`/groups/../members` -> `/members`): kaçışlanır.
    """
    value = value.strip()
    if value in (".", ".."):
        return value.replace(".", "%2E")
    return quote(value, safe="@.-_")


class GraphClient:
    """Belirteç önbellekli ince Graph HTTP istemcisi (yalnızca httpx; testte MockTransport)."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = "https://graph.microsoft.com/v1.0",
        login_url: str = "https://login.microsoftonline.com",
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (tenant_id and client_id and client_secret):
            raise ConnectorAuthError("Graph için tenant, istemci kimliği ve gizli anahtar zorunlu.")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.login_url = login_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.token_requests = 0  # gözlem/test için

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    async def token(self, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and self._clock() < self._expires_at:
                return self._token
            url = f"{self.login_url}/{_seg(self.tenant_id)}/oauth2/v2.0/token"
            data = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._secret,
                "scope": GRAPH_SCOPE,
            }
            self.token_requests += 1
            try:
                async with self._client() as client:
                    resp = await client.post(url, data=data)
            except httpx.HTTPError as exc:
                raise ConnectorAuthError(f"Graph belirteci alınamadı: {type(exc).__name__}") from exc
            if resp.status_code != 200:
                # Gövde gizli bilgi içermez ama yalnızca hata kodunu taşıyoruz
                code = _error_code(resp)
                raise ConnectorAuthError(f"Graph belirteci alınamadı (HTTP {resp.status_code} {code}).")
            body = resp.json()
            self._token = body["access_token"]
            lifetime = int(body.get("expires_in", 3599))
            self._expires_at = self._clock() + max(lifetime - TOKEN_SKEW_SECONDS, 0)
            return self._token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        absolute: bool = False,
    ) -> httpx.Response:
        """İstek gönderir; 401'de belirteci bir kez yeniler. 2xx dışı yanıtlar çağırana döner."""
        url = path if absolute else f"{self.base_url}{path}"
        for attempt in (0, 1):
            token = await self.token(force=attempt == 1)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                async with self._client() as client:
                    resp = await client.request(
                        method, url, params=params, json=json, headers=headers
                    )
            except httpx.HTTPError as exc:
                raise ConnectorError(f"Graph isteği başarısız: {type(exc).__name__}") from exc
            if resp.status_code == 401 and attempt == 0:
                continue
            return resp
        return resp  # pragma: no cover - döngü her zaman döner

    async def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        absolute: bool = False,
        not_found: str | None = None,
    ) -> httpx.Response:
        """`request` + hata çevirisi: 404 -> ConnectorNotFoundError, diğer 4xx/5xx -> ConnectorError."""
        resp = await self.request(method, path, params=params, json=json, absolute=absolute)
        if resp.status_code < 300:
            return resp
        raise _translate(resp, not_found)


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("code") or "")
    if isinstance(err, str):  # OAuth hata biçimi
        return err
    return ""


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    return str(err.get("message") or "") if isinstance(err, dict) else ""


def _translate(resp: httpx.Response, not_found: str | None) -> ConnectorError:
    code = _error_code(resp)
    if resp.status_code == 404:
        return ConnectorNotFoundError(not_found or f"Graph kaydı bulunamadı ({code}).")
    if resp.status_code in (401, 403):
        return ConnectorAuthError(
            f"Graph yetki hatası (HTTP {resp.status_code} {code}); uygulama izinlerini denetleyin."
        )
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "?")
        return ConnectorError(f"Graph istek sınırı (429); {retry} sn sonra yeniden deneyin.")
    return ConnectorError(f"Graph hatası (HTTP {resp.status_code} {code}).")


def _user(data: dict[str, Any]) -> DirectoryUser:
    return DirectoryUser(
        id=data["id"],
        user_principal_name=data.get("userPrincipalName") or "",
        display_name=data.get("displayName") or "",
        mail=data.get("mail"),
        account_enabled=bool(data.get("accountEnabled", True)),
        department=data.get("department"),
        job_title=data.get("jobTitle"),
        other_mails=list(data.get("otherMails") or []),
    )


class GraphIdentityProvider:
    """Entra ID kimlik işlemleri (Graph v1.0)."""

    name = "graph"

    def __init__(self, client: GraphClient, *, privileged_groups: set[str] | None = None) -> None:
        self.client = client
        self.privileged_groups = set(privileged_groups or ())

    async def _require_user(self, user: str) -> DirectoryUser:
        found = await self.get_user(user)
        if found is None:
            raise ConnectorNotFoundError(f"Kullanıcı dizinde bulunamadı: {user}")
        return found

    async def get_user(self, user: str) -> DirectoryUser | None:
        """`GET /users/{id|userPrincipalName}?$select=...`; 404 -> None."""
        resp = await self.client.request(
            "GET", f"/users/{_seg(user)}", params={"$select": USER_SELECT}
        )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 300:
            raise _translate(resp, None)
        return _user(resp.json())

    async def get_manager(self, user: str) -> DirectoryUser | None:
        """`GET /users/{id}/manager`; yönetici yoksa Graph 404 döner -> None."""
        resp = await self.client.request(
            "GET", f"/users/{_seg(user)}/manager", params={"$select": USER_SELECT}
        )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 300:
            raise _translate(resp, None)
        return _user(resp.json())

    async def list_groups(self, user: str) -> list[DirectoryGroup]:
        """`GET /users/{id}/memberOf` (doğrudan üyelikler; sayfalama `@odata.nextLink`).

        memberOf dizin rollerini de döndürür; yalnızca `#microsoft.graph.group` alınır.
        """
        groups: list[DirectoryGroup] = []
        resp = await self.client.call(
            "GET",
            f"/users/{_seg(user)}/memberOf",
            params={"$select": GROUP_SELECT},
            not_found=f"Kullanıcı dizinde bulunamadı: {user}",
        )
        for _ in range(MAX_PAGES):
            body = resp.json()
            for item in body.get("value", []):
                if item.get("@odata.type", "#microsoft.graph.group") != "#microsoft.graph.group":
                    continue
                groups.append(self._group(item))
            next_link = body.get("@odata.nextLink")
            if not next_link:
                break
            resp = await self.client.call("GET", next_link, absolute=True)
        return groups

    def _group(self, data: dict[str, Any]) -> DirectoryGroup:
        gid = data["id"]
        return DirectoryGroup(
            id=gid,
            display_name=data.get("displayName") or "",
            security_enabled=bool(data.get("securityEnabled", True)),
            privileged=bool(data.get("isAssignableToRole")) or gid in self.privileged_groups,
        )

    async def get_group(self, group: str) -> DirectoryGroup | None:
        """`GET /groups/{id}?$select=id,displayName,securityEnabled,isAssignableToRole`."""
        resp = await self.client.request(
            "GET", f"/groups/{_seg(group)}", params={"$select": GROUP_SELECT}
        )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 300:
            raise _translate(resp, None)
        return self._group(resp.json())

    async def is_privileged_group(self, group: str) -> bool:
        """Yapılandırılmış ayrıcalıklı liste ya da `isAssignableToRole`; bilinmeyen grup -> True."""
        if group in self.privileged_groups:
            return True
        found = await self.get_group(group)
        return True if found is None else found.privileged

    async def add_to_group(self, user, group, *, dry_run=False, idempotency_key=None):
        """`POST /groups/{id}/members/$ref` `{"@odata.id": ".../directoryObjects/{uid}"}` -> 204.

        Zaten üyeyse Graph 400 ("One or more added object references already exist") döner;
        bu tekrarlanabilir başarı sayılır (mesaj metniyle eşleşir — doğrulanmalı).
        """
        u = await self._require_user(user)
        if await self.get_group(group) is None:
            raise ConnectorNotFoundError(f"Grup dizinde bulunamadı: {group}")
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "group_id": group}
        resp = await self.client.request(
            "POST",
            f"/groups/{_seg(group)}/members/$ref",
            json={"@odata.id": f"{self.client.base_url}/directoryObjects/{u.id}"},
        )
        if resp.status_code == 400 and "already exist" in _error_message(resp):
            return {"dry_run": False, "user_id": u.id, "group_id": group, "already_member": True}
        if resp.status_code >= 300:
            raise _translate(resp, f"Grup dizinde bulunamadı: {group}")
        return {"dry_run": False, "user_id": u.id, "group_id": group, "already_member": False}

    async def remove_from_group(self, user, group, *, dry_run=False, idempotency_key=None):
        """`DELETE /groups/{id}/members/{uid}/$ref` -> 204; üye değilse 404 (başarı sayılır)."""
        u = await self._require_user(user)
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "group_id": group}
        resp = await self.client.request("DELETE", f"/groups/{_seg(group)}/members/{u.id}/$ref")
        if resp.status_code == 404:
            return {"dry_run": False, "user_id": u.id, "group_id": group, "was_member": False}
        if resp.status_code >= 300:
            raise _translate(resp, None)
        return {"dry_run": False, "user_id": u.id, "group_id": group, "was_member": True}

    async def revoke_sessions(self, user, *, dry_run=False, idempotency_key=None):
        """`POST /users/{id}/revokeSignInSessions` -> 200 `{"value": true}`."""
        u = await self._require_user(user)
        if dry_run:
            return {"dry_run": True, "user_id": u.id}
        await self.client.call("POST", f"/users/{u.id}/revokeSignInSessions")
        return {"dry_run": False, "user_id": u.id}

    async def set_account_enabled(self, user, enabled, *, dry_run=False, idempotency_key=None):
        """`PATCH /users/{id}` `{"accountEnabled": bool}` -> 204."""
        u = await self._require_user(user)
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "enabled": enabled}
        await self.client.call("PATCH", f"/users/{u.id}", json={"accountEnabled": bool(enabled)})
        return {"dry_run": False, "user_id": u.id, "enabled": enabled}

    async def reset_password(self, user, new_password, *, dry_run=False, idempotency_key=None):
        """Kimlik doğrulama yöntemleri API'si ile parola sıfırlama.

        `GET /users/{id}/authentication/passwordMethods` -> yöntem kimliği;
        `POST /users/{id}/authentication/methods/{id}/resetPassword` `{"newPassword": ...}` ->
        202 Accepted + `Location` (uzun süren işlem). **Doğrulanmalı:** Microsoft belgeleri bu
        uç noktayı yalnızca *delegated* izinle (UserAuthenticationMethod.ReadWrite.All)
        listeliyordu; uygulama (client credentials) izniyle çalışması kiracıda sınanmalıdır.
        Alternatif (`PATCH /users/{id}` `passwordProfile`) uygulamaya dizin rolü (Parola /
        Kullanıcı Yöneticisi) atanmasını gerektirir ve burada uygulanmadı. Yalnızca bulut
        hesapları; şirket içinden eşitlenen hesaplarda parola geri yazma (SSPR writeback) gerekir.
        Parola hiçbir zaman dönen sözlükte, günlükte ya da hata mesajında yer almaz.
        """
        u = await self._require_user(user)
        resp = await self.client.call(
            "GET",
            f"/users/{u.id}/authentication/passwordMethods",
            not_found=f"Kullanıcı dizinde bulunamadı: {user}",
        )
        methods = resp.json().get("value", [])
        if not methods:
            raise ConnectorError("Kullanıcının parola yöntemi bulunamadı (parolasız hesap?).")
        method_id = methods[0]["id"]
        if dry_run:
            return {"dry_run": True, "user_id": u.id}
        resp = await self.client.call(
            "POST",
            f"/users/{u.id}/authentication/methods/{_seg(method_id)}/resetPassword",
            json={"newPassword": new_password},
        )
        return {
            "dry_run": False,
            "user_id": u.id,
            "status": "accepted" if resp.status_code == 202 else "completed",
            "operation": resp.headers.get("Location"),
            # Entra yeni parolayla ilk girişte değiştirmeyi zorunlu kılar (doğrulanmalı)
            "force_change_next_sign_in": True,
        }

    async def reset_mfa(self, user, *, dry_run=False, idempotency_key=None):
        """`GET /users/{id}/authentication/methods` + yöntem türüne göre
        `DELETE /users/{id}/authentication/{tür}Methods/{id}`; parola yöntemi korunur.

        Doğrulanmalı: Graph varsayılan MFA yöntemini, başka yöntemler varken silmeyi
        reddedebilir; bu durumda kısmi sonuç hatası yükseltilir (silinenler mesajda).
        """
        u = await self._require_user(user)
        resp = await self.client.call("GET", f"/users/{u.id}/authentication/methods")
        targets = [
            (MFA_METHOD_SEGMENTS[m["@odata.type"]], m["id"])
            for m in resp.json().get("value", [])
            if m.get("@odata.type") in MFA_METHOD_SEGMENTS
        ]
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "methods": [s for s, _ in targets]}
        removed: list[str] = []
        for segment, method_id in targets:
            r = await self.client.request(
                "DELETE", f"/users/{u.id}/authentication/{segment}/{_seg(method_id)}"
            )
            if r.status_code >= 300 and r.status_code != 404:
                raise ConnectorError(
                    f"MFA yöntemi silinemedi ({segment}, HTTP {r.status_code}); "
                    f"silinenler: {', '.join(removed) or '-'}."
                )
            removed.append(segment)
        return {"dry_run": False, "user_id": u.id, "removed_methods": removed}

    async def unlock_account(self, user, *, dry_run=False, idempotency_key=None):
        raise ConnectorNotSupportedError(
            "Entra ID bulutta hesap kilidi açma API'si yok (Akıllı Kilitleme süre dolunca "
            "kendiliğinden açılır). Şirket içi AD kilidi LDAP ile açılır ve kapsam dışıdır."
        )


class GraphLicenseProvider:
    name = "graph"

    def __init__(self, client: GraphClient, identity: GraphIdentityProvider) -> None:
        self.client = client
        self.identity = identity

    async def list_skus(self) -> list[SkuInfo]:
        """`GET /subscribedSkus` -> skuId, skuPartNumber, prepaidUnits.enabled, consumedUnits."""
        resp = await self.client.call("GET", "/subscribedSkus")
        return [
            SkuInfo(
                sku_id=s["skuId"],
                sku_part_number=s.get("skuPartNumber") or "",
                enabled=int((s.get("prepaidUnits") or {}).get("enabled") or 0),
                consumed=int(s.get("consumedUnits") or 0),
            )
            for s in resp.json().get("value", [])
        ]

    async def assign_license(self, user, sku_id, *, dry_run=False, idempotency_key=None):
        """`POST /users/{id}/assignLicense` `{"addLicenses": [{"skuId", "disabledPlans": []}],
        "removeLicenses": []}` -> 200. Kullanıcının `usageLocation` alanı dolu olmalıdır (yoksa
        Graph 400 döner — doğrulanmalı). Zaten atanmış lisansı yeniden eklemek hata vermez."""
        u = await self.identity._require_user(user)
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "sku_id": sku_id}
        await self.client.call(
            "POST",
            f"/users/{u.id}/assignLicense",
            json={"addLicenses": [{"skuId": sku_id, "disabledPlans": []}], "removeLicenses": []},
        )
        return {"dry_run": False, "user_id": u.id, "sku_id": sku_id}


class GraphMailProvider:
    name = "graph"

    def __init__(self, client: GraphClient, sender_mailbox: str | None) -> None:
        self.client = client
        self.sender_mailbox = sender_mailbox

    async def grant_shared_access(
        self, mailbox, user, access: MailboxAccess, *, dry_run=False, idempotency_key=None
    ):
        raise ConnectorNotSupportedError(
            "Graph v1.0'da paylaşılan posta kutusu izni (FullAccess/SendAs) API'si yok; Exchange "
            "Online PowerShell (Add-MailboxPermission / Add-RecipientPermission) gerekir."
        )

    async def send_mail(self, to, subject, body, *, dry_run=False, idempotency_key=None):
        """`POST /users/{gönderen}/sendMail` -> 202 (gönderilmiş öğelere kaydedilmez)."""
        if not self.sender_mailbox:
            raise ConnectorError("Gönderen posta kutusu (SZ_GRAPH_SENDER_MAILBOX) tanımlı değil.")
        if dry_run:
            return {"dry_run": True, "to": to}
        await self.client.call(
            "POST",
            f"/users/{_seg(self.sender_mailbox)}/sendMail",
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": False,
            },
        )
        return {"dry_run": False, "to": to}


class TeamsGraphNotifier:
    """Kullanıcıya e-posta (Graph sendMail), kanala Teams gelen webhook mesajı.

    Webhook gövdesi Teams iş akışı (Workflows) "webhook isteği alındığında kanala gönder"
    şablonunun beklediği Adaptive Card zarfıdır. Eski Office 365 bağlayıcı webhook'larının bu
    biçimi kabul ettiği ve emeklilik takvimi **doğrulanmalı**dır.
    """

    name = "teams"

    def __init__(
        self,
        identity: GraphIdentityProvider,
        mail: GraphMailProvider,
        webhooks: dict[str, str],
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.identity = identity
        self.mail = mail
        self.webhooks = dict(webhooks)
        self._timeout = timeout
        self._transport = transport

    async def send_to_user(
        self, user, subject, text, *, sensitive=False, dry_run=False, idempotency_key=None
    ):
        u = await self.identity._require_user(user)
        if sensitive:
            if not u.other_mails:
                raise ConnectorError(
                    "Hassas bildirim için kullanıcının doğrulanmış ikincil adresi (otherMails) yok."
                )
            to = u.other_mails[0]
        else:
            to = u.mail or u.user_principal_name
        await self.mail.send_mail(to, subject, text, dry_run=dry_run)
        return {
            "dry_run": dry_run,
            "user_id": u.id,
            "channel": "secondary_mail" if sensitive else "mail",
        }

    async def post_to_channel(self, channel, text, *, dry_run=False, idempotency_key=None):
        url = self.webhooks.get(channel)
        if not url:
            raise ConnectorNotFoundError(f"Teams kanalı için webhook tanımlı değil: {channel}")
        if dry_run:
            return {"dry_run": True, "channel": channel}
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                    },
                }
            ],
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as c:
                resp = await c.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Teams webhook'u başarısız: {type(exc).__name__}") from exc
        if resp.status_code >= 300:
            raise ConnectorError(f"Teams webhook'u reddetti (HTTP {resp.status_code}).")
        return {"dry_run": False, "channel": channel}

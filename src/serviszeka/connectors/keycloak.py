"""Keycloak kimlik bağlayıcısı (Admin REST API, realm kapsamlı).

Kimlik doğrulama: hizmet hesabı (service account) etkin, gizli (confidential) bir istemcinin
**client credentials** akışı: `POST {server}/realms/{realm}/protocol/openid-connect/token`.
Belirteç süresinden en fazla 60 sn (kısa ömürlü belirteçte ömrün yarısı) önceye kadar önbellekte
tutulur; Admin API 401 dönerse bir kez yenilenir. Admin API tabanı `{server}/admin/realms/{realm}`.
Keycloak 17 öncesi (WildFly) dağıtımlarda sunucu adresi `/auth` önekini içermelidir
(`SZ_KEYCLOAK_SERVER_URL=https://sso.ornek.com/auth`).

Hizmet hesabına gereken `realm-management` istemci rolleri — **doğrulanmalı** (docs/faz3.md):
- `view-users` (kullanıcı/üyelik okuma, kimlik bilgisi listesi, brute-force durumu)
- `query-groups` / `view-users` (grup arama, grup rol eşlemeleri)
- `manage-users` (grup üyeliği, oturum kapatma, enabled, parola, kimlik bilgisi silme,
  brute-force kilidini temizleme)

Eşleme ve kararlar (davranış uydurulmadı; belirsiz olanlar "doğrulanmalı" listesinde):
- Kullanıcı başvurusu: önce kimlik (`GET /users/{id}`), sonra kullanıcı adı
  (`GET /users?username=..&exact=true`), `@` içeriyorsa ayrıca e-posta
  (`GET /users?email=..&exact=true`). Sunucu sonucu yine istemcide tam eşleşmeyle süzülür;
  farklı kullanıcılara çıkan eşleşme `AmbiguousReferenceError` (asla ilkini seçmez).
- `DirectoryUser.user_principal_name` = Keycloak `username`, `mail` = `email`,
  `display_name` = `firstName lastName`. Keycloak'ta ikincil (doğrulanmış) e-posta alanı yoktur:
  `other_mails` boştur (hassas bildirim bu sağlayıcıyla teslim edilemez).
- Yönetici: Keycloak'ta yerleşik yönetici ilişkisi yok. Yapılandırılabilir kullanıcı özniteliği
  (`SZ_KEYCLOAK_MANAGER_ATTRIBUTE`, varsayılan `manager`) yöneticinin **kullanıcı adını** taşır;
  birden fazla farklı değer belirsizdir (hata), bulunamayan ya da kendisini gösteren değer `None`
  (talep yalnızca admin onayına düşer). Keycloak 24+ bildirime dayalı kullanıcı profilinde bu
  öznitelik profilde tanımlı olmalıdır (aksi halde yönetilmeyen öznitelik olarak saklanmaz/dönmez).
- Grup başvurusu: kimlik (`GET /groups/{id}`), tam yol (`/ust/alt`) ya da tam ad. Ad/yol
  `GET /groups?search=<yaprak ad>` ile aranır ve sonuç ağacı istemcide **NFKC + casefold**
  normalize edilerek tam eşleşmeyle süzülür; bulanık/alt dize eşleşmesi yoktur. Birden fazla grup
  eşleşirse `AmbiguousReferenceError` (ör. farklı üst gruplarda aynı ad ya da yalnızca harf
  büyüklüğüyle ayrışan iki grup). Böylece ayrıcalıklı grup denetimi harf/Unicode varyantıyla
  atlatılamaz: bilinmeyen ya da belirsiz grup ayrıcalıklı sayılır, yazma işlemi yapılmaz.
- Ayrıcalıklı grup: `SZ_IDENTITY_PRIVILEGED_GROUPS` içindeki kimlik / tam yol / ad (normalize)
  eşleşmesi; **alt gruplar** üst grubun rollerini devraldığından yolu ayrıcalıklı bir yolun altında
  olan grup da ayrıcalıklıdır. Ayrıca grubun doğrudan rol eşlemelerinde `realm-management` istemci
  rolü varsa ayrıcalıklıdır (Entra `isAssignableToRole` karşılığı). Üst gruplardan devralınan rol
  eşlemeleri ve bileşik (composite) roller taranmaz — bunlar için grubu ayara yolu ile ekleyin.
- Parola sıfırlama: `PUT /users/{id}/reset-password` `{"type": "password", "value": ...,
  "temporary": true}` (ilk girişte değiştirme zorunlu). `execute-actions-email` (UPDATE_PASSWORD)
  **seçilmedi**: bağlantıyı kullanıcının birincil e-postasına gönderir (ele geçirilmiş hesapta
  saldırgan okur; `Notifier` sözleşmesine aykırı) ve arayüzün geçici parola sözleşmesini
  karşılamaz. Parola hiçbir dönen sözlükte, günlükte ya da hata mesajında yer almaz.
- MFA sıfırlama: `GET /users/{id}/credentials` + ikinci etken türleri için
  `DELETE /users/{id}/credentials/{credentialId}` (`MFA_CREDENTIAL_TYPES`); parola ve bilinmeyen
  türler korunur. Kullanıcı yeniden kaydı, realm kimlik doğrulama akışı OTP/WebAuthn istiyorsa bir
  sonraki girişte yapılır (zorunlu eylem eklenmez).
- Kilit açma: `GET` + `DELETE /attack-detection/brute-force/users/{id}` (brute-force kilidi).
  Kalıcı kilitleme kullanıcıyı devre dışı bırakmışsa (`enabled=false`) bu çağrı yeniden etkinleştirmez
  (o ayrı ve daha riskli `set_account_enabled` işlemidir).
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import quote

import httpx

from serviszeka.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    DirectoryGroup,
    DirectoryUser,
)

TOKEN_SKEW_SECONDS = 60
PAGE_SIZE = 100
MAX_PAGES = 20
MAX_GROUP_DEPTH = 32
# Parola dışındaki ikinci etken kimlik bilgisi türleri (Keycloak yerleşik türleri; doğrulanmalı)
MFA_CREDENTIAL_TYPES: tuple[str, ...] = (
    "otp",
    "webauthn",
    "webauthn-passwordless",
    "recovery-authn-codes",
)
# Doğrudan rol eşlemesinde bu istemcinin rolü olan grup ayrıcalıklıdır (realm yönetimi)
PRIVILEGED_CLIENTS: frozenset[str] = frozenset({"realm-management"})
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


class AmbiguousReferenceError(ConnectorError):
    """Kullanıcı/grup başvurusu birden fazla nesneye çözülüyor: işlem yapılmaz."""


def normalize_ref(value: str) -> str:
    """Karşılaştırma anahtarı: NFKC + casefold (+ kenar boşlukları atılır), ardından yine NFKC."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", value).casefold().strip())


def _seg(value: str) -> str:
    """URL yol kesimi: her şey kaçışlanır; `.`/`..` yol gezinmesi olarak yorumlanamaz."""
    value = value.strip()
    if value in (".", ".."):
        return value.replace(".", "%2E")
    return quote(value, safe="")


class KeycloakClient:
    """Belirteç önbellekli ince Keycloak Admin REST istemcisi (yalnızca httpx; testte MockTransport)."""

    def __init__(
        self,
        server_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (server_url and realm and client_id and client_secret):
            raise ConnectorAuthError(
                "Keycloak için sunucu adresi, realm, istemci kimliği ve gizli anahtar zorunlu."
            )
        self.server_url = server_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self._secret = client_secret
        self.base_url = f"{self.server_url}/admin/realms/{_seg(realm)}"
        self.token_url = f"{self.server_url}/realms/{_seg(realm)}/protocol/openid-connect/token"
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.token_requests = 0  # gözlem/test için

    def __repr__(self) -> str:  # gizli anahtar/belirteç asla görünmez
        return f"KeycloakClient(server_url={self.server_url!r}, realm={self.realm!r})"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    async def token(self, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and self._clock() < self._expires_at:
                return self._token
            data = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._secret,
            }
            self.token_requests += 1
            try:
                async with self._client() as client:
                    resp = await client.post(self.token_url, data=data)
            except httpx.HTTPError as exc:
                raise ConnectorAuthError(
                    f"Keycloak belirteci alınamadı: {type(exc).__name__}"
                ) from None
            if resp.status_code != 200:
                code = _error_code(resp)
                suffix = f" {code}" if code else ""
                raise ConnectorAuthError(
                    f"Keycloak belirteci alınamadı (HTTP {resp.status_code}{suffix})."
                )
            try:
                body = resp.json()
                token = str(body["access_token"])
                lifetime = int(body.get("expires_in", 60))
            except (ValueError, KeyError, TypeError):
                raise ConnectorAuthError("Keycloak belirteç yanıtı geçersiz.") from None
            self._token = token
            skew = min(TOKEN_SKEW_SECONDS, lifetime // 2)
            self._expires_at = self._clock() + max(lifetime - skew, 0)
            return token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """İstek gönderir; 401'de belirteci bir kez yeniler. 2xx dışı yanıtlar çağırana döner."""
        url = f"{self.base_url}{path}"
        resp: httpx.Response | None = None
        for attempt in (0, 1):
            token = await self.token(force=attempt == 1)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                async with self._client() as client:
                    resp = await client.request(
                        method, url, params=params, json=json, headers=headers
                    )
            except httpx.HTTPError as exc:
                # İstisna metni istek gövdesini (parola) içerebilir: yalnızca türü taşınır
                raise ConnectorError(f"Keycloak isteği başarısız: {type(exc).__name__}") from None
            if resp.status_code == 401 and attempt == 0:
                continue
            return resp
        assert resp is not None
        return resp

    async def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        not_found: str | None = None,
    ) -> httpx.Response:
        """`request` + hata çevirisi: 404 -> ConnectorNotFoundError, diğer 4xx/5xx -> ConnectorError."""
        resp = await self.request(method, path, params=params, json=json)
        if resp.status_code < 300:
            return resp
        raise _translate(resp, not_found)


def _error_code(resp: httpx.Response) -> str:
    """Yalnızca tanımlayıcı biçimindeki hata kodu (ör. `invalidPasswordMinLengthMessage`).

    `error_description` / `errorMessage` serbest metindir ve girdiyi yansıtabilir: taşınmaz.
    """
    try:
        body = resp.json()
    except ValueError:
        return ""
    code = body.get("error") if isinstance(body, dict) else None
    if isinstance(code, str) and _SAFE_ERROR_CODE.match(code):
        return code
    return ""


def _translate(resp: httpx.Response, not_found: str | None) -> ConnectorError:
    code = _error_code(resp)
    suffix = f" {code}" if code else ""
    if resp.status_code == 404:
        return ConnectorNotFoundError(not_found or "Keycloak kaydı bulunamadı.")
    if resp.status_code in (401, 403):
        return ConnectorAuthError(
            f"Keycloak yetki hatası (HTTP {resp.status_code}{suffix}); hizmet hesabının "
            "realm-management rollerini denetleyin."
        )
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "?")
        return ConnectorError(f"Keycloak istek sınırı (429); {retry} sn sonra yeniden deneyin.")
    return ConnectorError(f"Keycloak hatası (HTTP {resp.status_code}{suffix}).")


def _user(data: dict[str, Any]) -> DirectoryUser:
    display = " ".join(p for p in (data.get("firstName"), data.get("lastName")) if p)
    return DirectoryUser(
        id=str(data["id"]),
        user_principal_name=data.get("username") or "",
        display_name=display or data.get("username") or "",
        mail=data.get("email") or None,
        account_enabled=bool(data.get("enabled", True)),
    )


def _walk_groups(items: Iterable[dict[str, Any]], depth: int = 0) -> Iterator[dict[str, Any]]:
    """Arama sonucu ağacını düzleştirir (alt gruplar `subGroups` içinde)."""
    if depth > MAX_GROUP_DEPTH:
        return
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            continue
        yield item
        yield from _walk_groups(item.get("subGroups") or (), depth + 1)


class KeycloakIdentityProvider:
    """Keycloak kimlik işlemleri (Admin REST API). Ayrıntılar modül belgesinde."""

    name = "keycloak"

    def __init__(
        self,
        client: KeycloakClient,
        *,
        privileged_groups: Iterable[str] | None = None,
        manager_attribute: str = "manager",
    ) -> None:
        self.client = client
        self.privileged_groups = {normalize_ref(g) for g in (privileged_groups or ()) if g.strip()}
        self.manager_attribute = manager_attribute

    # --- kullanıcı -------------------------------------------------------------------------------

    async def _user_by_id(self, user_id: str) -> dict[str, Any] | None:
        if not user_id.strip():
            return None
        resp = await self.client.request("GET", f"/users/{_seg(user_id)}")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 300:
            raise _translate(resp, None)
        return resp.json()

    async def _search_users(self, field: str, value: str) -> list[dict[str, Any]]:
        """`GET /users?{field}=..&exact=true`, istemcide de tam (normalize) eşleşme."""
        resp = await self.client.call(
            "GET", "/users", params={field: value.strip().lower(), "exact": "true"}
        )
        body = resp.json()
        want = normalize_ref(value)
        return [
            u
            for u in (body if isinstance(body, list) else [])
            if isinstance(u, dict) and "id" in u and normalize_ref(str(u.get(field) or "")) == want
        ]

    async def _find_user_rep(self, user: str, *, by_id: bool = True) -> dict[str, Any] | None:
        ref = user.strip()
        if not ref:
            return None
        if by_id:
            found = await self._user_by_id(ref)
            if found is not None:
                return found
        candidates = await self._search_users("username", ref)
        if "@" in ref:
            candidates += await self._search_users("email", ref)
        unique = {str(u["id"]): u for u in candidates}
        if len(unique) > 1:
            raise AmbiguousReferenceError(
                f"Kullanıcı başvurusu birden fazla kullanıcıya çözülüyor: {ref}"
            )
        return next(iter(unique.values()), None)

    async def _require_user_rep(self, user: str) -> dict[str, Any]:
        found = await self._find_user_rep(user)
        if found is None:
            raise ConnectorNotFoundError(f"Kullanıcı dizinde bulunamadı: {user}")
        return found

    async def _require_user(self, user: str) -> DirectoryUser:
        return _user(await self._require_user_rep(user))

    async def get_user(self, user: str) -> DirectoryUser | None:
        """Kimlik, kullanıcı adı ya da e-posta (tam eşleşme); yoksa None, belirsizse hata."""
        found = await self._find_user_rep(user)
        return _user(found) if found is not None else None

    async def get_manager(self, user: str) -> DirectoryUser | None:
        """Kullanıcı özniteliğindeki (`manager_attribute`) kullanıcı adıyla yöneticiyi bulur."""
        rep = await self._require_user_rep(user)
        # Arama sonucu öznitelikleri içermeyebilir: tam gösterim kimlikle okunur
        full = await self._user_by_id(str(rep["id"])) or rep
        attrs = full.get("attributes") or {}
        raw = attrs.get(self.manager_attribute) if isinstance(attrs, dict) else None
        values = [raw] if isinstance(raw, str) else list(raw or [])
        names = {normalize_ref(v): v.strip() for v in values if isinstance(v, str) and v.strip()}
        if not names:
            return None
        if len(names) > 1:
            raise AmbiguousReferenceError(
                f"Yönetici özniteliği ({self.manager_attribute}) birden fazla değer içeriyor."
            )
        manager = await self._find_user_rep(next(iter(names.values())), by_id=False)
        if manager is None or str(manager["id"]) == str(full["id"]):
            return None
        return _user(manager)

    # --- gruplar ---------------------------------------------------------------------------------

    def _group(self, data: dict[str, Any]) -> DirectoryGroup:
        return DirectoryGroup(
            id=str(data["id"]),
            display_name=data.get("path") or data.get("name") or "",
            security_enabled=True,
            privileged=self._configured_privileged(data),
        )

    def _configured_privileged(self, data: dict[str, Any]) -> bool:
        if not self.privileged_groups:
            return False
        keys = {normalize_ref(str(data.get(k) or "")) for k in ("id", "name", "path")}
        if keys & self.privileged_groups:
            return True
        path = normalize_ref(str(data.get("path") or ""))
        segments = [s for s in path.split("/") if s]
        for entry in self.privileged_groups:
            # Alt gruplar üst grubun rollerini devralır: ayrıcalıklı yolun altındaki her grup
            if entry.startswith("/") and path.startswith(entry.rstrip("/") + "/"):
                return True
            # Ad ile yapılandırılmış ayrıcalıklı grubun alt grupları (güvenli tarafta geniş)
            if not entry.startswith("/") and entry in segments:
                return True
        return False

    async def _user_groups(self, user_id: str) -> tuple[list[dict[str, Any]], bool]:
        """`GET /users/{id}/groups?first=&max=` (doğrudan üyelikler); (gruplar, tam mı)."""
        items: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            resp = await self.client.call(
                "GET",
                f"/users/{_seg(user_id)}/groups",
                params={"first": page * PAGE_SIZE, "max": PAGE_SIZE},
                not_found=f"Kullanıcı dizinde bulunamadı: {user_id}",
            )
            body = resp.json()
            chunk = [g for g in (body if isinstance(body, list) else []) if isinstance(g, dict)]
            items.extend(g for g in chunk if "id" in g)
            if len(chunk) < PAGE_SIZE:
                return items, True
        return items, False

    async def list_groups(self, user: str) -> list[DirectoryGroup]:
        """Doğrudan grup üyelikleri (sayfalı; en fazla `MAX_PAGES` sayfa). İç içe devralma yok."""
        u = await self._require_user(user)
        items, _complete = await self._user_groups(u.id)
        return [self._group(g) for g in items]

    async def _group_by_id(self, group_id: str) -> dict[str, Any] | None:
        resp = await self.client.request("GET", f"/groups/{_seg(group_id)}")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 300:
            raise _translate(resp, None)
        return resp.json()

    async def _resolve_group(self, group: str) -> dict[str, Any] | None:
        """Kimlik, tam yol ya da tam ad -> grup gösterimi; yoksa None, belirsizse hata."""
        ref = group.strip()
        if not ref or ref.strip("/") in ("", ".", ".."):
            return None
        is_path = ref.startswith("/")
        if not is_path:
            found = await self._group_by_id(ref)
            if found is not None:
                return found
        want = normalize_ref(ref)
        if is_path:
            want = "/" + "/".join(s for s in want.split("/") if s)
        leaf = normalize_ref(ref.rstrip("/").rsplit("/", 1)[-1])
        matches: dict[str, dict[str, Any]] = {}
        for page in range(MAX_PAGES):
            resp = await self.client.call(
                "GET",
                "/groups",
                params={"search": leaf, "first": page * PAGE_SIZE, "max": PAGE_SIZE},
            )
            body = resp.json()
            top = body if isinstance(body, list) else []
            for item in _walk_groups(top):
                key = item.get("path") if is_path else item.get("name")
                if normalize_ref(str(key or "")) == want:
                    matches[str(item["id"])] = item
            if len(top) < PAGE_SIZE:
                break
        else:
            raise AmbiguousReferenceError(
                f"Grup araması sınırı aştı; başvuru kesin çözülemedi: {ref}"
            )
        if len(matches) > 1:
            raise AmbiguousReferenceError(
                f"Grup başvurusu birden fazla gruba çözülüyor: {ref} (tam yol kullanın)."
            )
        return next(iter(matches.values()), None)

    async def _require_group(self, group: str) -> dict[str, Any]:
        found = await self._resolve_group(group)
        if found is None:
            raise ConnectorNotFoundError(f"Grup dizinde bulunamadı: {group}")
        return found

    async def get_group(self, group: str) -> DirectoryGroup | None:
        found = await self._resolve_group(group)
        return self._group(found) if found is not None else None

    async def _has_privileged_roles(self, group_id: str) -> bool:
        """`GET /groups/{id}/role-mappings`: doğrudan `realm-management` istemci rolü var mı."""
        resp = await self.client.call("GET", f"/groups/{_seg(group_id)}/role-mappings")
        body = resp.json()
        clients = body.get("clientMappings") if isinstance(body, dict) else None
        if not isinstance(clients, dict):
            return False
        for key, mapping in clients.items():
            name = mapping.get("client", key) if isinstance(mapping, dict) else key
            if key in PRIVILEGED_CLIENTS or name in PRIVILEGED_CLIENTS:
                return True
        return False

    async def is_privileged_group(self, group: str) -> bool:
        """Yapılandırma (kimlik/yol/ad, alt gruplar dahil) ya da realm-management rolü.

        Bilinmeyen ya da belirsiz grup ayrıcalıklı sayılır (güvenli varsayılan).
        """
        if normalize_ref(group) in self.privileged_groups:
            return True
        try:
            found = await self._resolve_group(group)
        except AmbiguousReferenceError:
            return True
        if found is None:
            return True
        if self._configured_privileged(found):
            return True
        return await self._has_privileged_roles(str(found["id"]))

    # --- yazma işlemleri -------------------------------------------------------------------------

    async def add_to_group(self, user, group, *, dry_run=False, idempotency_key=None):
        """`PUT /users/{id}/groups/{groupId}` -> 204 (zaten üyeyse de; tekrarlanabilir)."""
        u = await self._require_user(user)
        g = await self._require_group(group)
        gid = str(g["id"])
        items, _complete = await self._user_groups(u.id)
        already = any(str(i["id"]) == gid for i in items)
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "group_id": gid, "already_member": already}
        await self.client.call(
            "PUT",
            f"/users/{_seg(u.id)}/groups/{_seg(gid)}",
            not_found=f"Grup dizinde bulunamadı: {group}",
        )
        return {"dry_run": False, "user_id": u.id, "group_id": gid, "already_member": already}

    async def remove_from_group(self, user, group, *, dry_run=False, idempotency_key=None):
        """`DELETE /users/{id}/groups/{groupId}` -> 204; üye değilse yazma yapılmaz.

        Üyelik listesi sayfa sınırında kesildiyse üyelik bilinmez sayılır ve silme yine gönderilir.
        """
        u = await self._require_user(user)
        g = await self._require_group(group)
        gid = str(g["id"])
        items, complete = await self._user_groups(u.id)
        was_member = any(str(i["id"]) == gid for i in items)
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "group_id": gid, "was_member": was_member}
        if was_member or not complete:
            await self.client.call("DELETE", f"/users/{_seg(u.id)}/groups/{_seg(gid)}")
        return {"dry_run": False, "user_id": u.id, "group_id": gid, "was_member": was_member}

    async def revoke_sessions(self, user, *, dry_run=False, idempotency_key=None):
        """`POST /users/{id}/logout` -> 204: kullanıcının tüm oturumlarını kapatır.

        Daha önce verilmiş erişim belirteçleri süreleri dolana dek geçerlidir; çevrim dışı
        (offline) oturumların da kapanıp kapanmadığı sürüme bağlıdır — doğrulanmalı.
        """
        u = await self._require_user(user)
        if dry_run:
            return {"dry_run": True, "user_id": u.id}
        await self.client.call("POST", f"/users/{_seg(u.id)}/logout")
        return {"dry_run": False, "user_id": u.id}

    async def set_account_enabled(self, user, enabled, *, dry_run=False, idempotency_key=None):
        """`PUT /users/{id}` `{"enabled": bool}` -> 204 (kısmi gösterim; doğrulanmalı)."""
        u = await self._require_user(user)
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "enabled": enabled}
        await self.client.call("PUT", f"/users/{_seg(u.id)}", json={"enabled": bool(enabled)})
        return {"dry_run": False, "user_id": u.id, "enabled": enabled}

    async def reset_password(self, user, new_password, *, dry_run=False, idempotency_key=None):
        """`PUT /users/{id}/reset-password` `{"type": "password", "value", "temporary": true}`.

        Realm parola ilkesi reddederse 400 (`error` kodu taşınır, açıklama/parola taşınmaz).
        """
        u = await self._require_user(user)
        if dry_run:
            return {"dry_run": True, "user_id": u.id}
        await self.client.call(
            "PUT",
            f"/users/{_seg(u.id)}/reset-password",
            json={"type": "password", "value": new_password, "temporary": True},
        )
        return {
            "dry_run": False,
            "user_id": u.id,
            "status": "completed",
            "force_change_next_sign_in": True,
        }

    async def reset_mfa(self, user, *, dry_run=False, idempotency_key=None):
        """`GET /users/{id}/credentials` + `DELETE /users/{id}/credentials/{id}` (ikinci etkenler)."""
        u = await self._require_user(user)
        resp = await self.client.call(
            "GET",
            f"/users/{_seg(u.id)}/credentials",
            not_found=f"Kullanıcı dizinde bulunamadı: {user}",
        )
        body = resp.json()
        targets = [
            (str(c["type"]), str(c["id"]))
            for c in (body if isinstance(body, list) else [])
            if isinstance(c, dict) and c.get("type") in MFA_CREDENTIAL_TYPES and "id" in c
        ]
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "methods": [t for t, _ in targets]}
        removed: list[str] = []
        for cred_type, cred_id in targets:
            r = await self.client.request(
                "DELETE", f"/users/{_seg(u.id)}/credentials/{_seg(cred_id)}"
            )
            if r.status_code >= 300 and r.status_code != 404:
                raise ConnectorError(
                    f"MFA kimlik bilgisi silinemedi ({cred_type}, HTTP {r.status_code}); "
                    f"silinenler: {', '.join(removed) or '-'}."
                )
            removed.append(cred_type)
        return {"dry_run": False, "user_id": u.id, "removed_methods": removed}

    async def unlock_account(self, user, *, dry_run=False, idempotency_key=None):
        """`GET` + `DELETE /attack-detection/brute-force/users/{id}` (brute-force kilidi temizlenir).

        `was_locked` = brute-force durumundaki `disabled`. Kalıcı kilitlemeyle devre dışı kalmış
        hesap yeniden etkinleştirilmez. Son başarısız IP vb. ayrıntılar döndürülmez.
        """
        u = await self._require_user(user)
        path = f"/attack-detection/brute-force/users/{_seg(u.id)}"
        resp = await self.client.call("GET", path)
        status = resp.json() if resp.content else {}
        was_locked = bool(status.get("disabled")) if isinstance(status, dict) else False
        if dry_run:
            return {"dry_run": True, "user_id": u.id, "was_locked": was_locked}
        await self.client.call("DELETE", path)
        return {"dry_run": False, "user_id": u.id, "was_locked": was_locked}


def build_keycloak(
    settings: Any, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """`SZ_CONNECTOR_PROVIDER=keycloak`: yalnızca `identity` (posta/lisans/bildirim Keycloak'ta yok)."""
    from serviszeka.connectors.base import IDENTITY

    secret = settings.keycloak_client_secret
    client = KeycloakClient(
        settings.keycloak_server_url or "",
        settings.keycloak_realm or "",
        settings.keycloak_client_id or "",
        secret.get_secret_value() if hasattr(secret, "get_secret_value") else (secret or ""),
        transport=transport,
    )
    identity = KeycloakIdentityProvider(
        client,
        privileged_groups=settings.identity_privileged_groups,
        manager_attribute=settings.keycloak_manager_attribute,
    )
    return {IDENTITY: identity}

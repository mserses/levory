"""Kimlik doğrulama (OIDC bearer JWT) ve rol tabanlı yetkilendirme.

Keycloak ve Microsoft Entra ID ile çalışır: imza yayıncının JWKS anahtarlarıyla doğrulanır,
iss/aud/exp/nbf denetlenir, yalnızca asimetrik algoritmalar (RS256/ES256) kabul edilir.

Roller:
- ``agent``: olay ve bilgi bankası okuma/oluşturma/güncelleme, triage başlatma
- ``approver``: yapay zekâ kararlarını onaylama, senkron çakışmalarını çözme
- ``admin``: her şey (entegrasyon pull/push, denetim günlüğü dahil); tüm rolleri kapsar
- ``requester``: self-servis son kullanıcı; yalnızca kendi adına kayıt açar ve yalnızca
  arayanı (caller) kendisi olan kayıtları görür. Yapay zekâ kararlarını (iç ayrıntılar) göremez.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from serviszeka.config import Settings, get_settings

log = logging.getLogger(__name__)

ROLE_AGENT = "agent"
ROLE_APPROVER = "approver"
ROLE_ADMIN = "admin"
ROLE_REQUESTER = "requester"

# Simetrik (HS*) ve imzasız ('none') algoritmalar asla kabul edilmez
ALLOWED_ALGORITHMS: dict[str, str] = {"RS256": "RSA", "ES256": "EC"}
_USERNAME_FALLBACKS = ("preferred_username", "upn", "email", "sub")  # upn değiştirilemez, email değiştirilebilir
_DEV_PREFIX = "dev:"

MSG_MISSING = "Kimlik doğrulama gerekli"
MSG_INVALID = "Geçersiz veya süresi dolmuş erişim belirteci"
MSG_FORBIDDEN = "Bu işlem için yetkiniz yok"
MSG_NOT_CONFIGURED = "Kimlik doğrulama yapılandırılmamış"


class AuthError(Exception):
    """Belirteç eksik/geçersiz (HTTP 401). Mesaj yalnızca loglanır, istemciye dönmez."""


class AuthConfigError(RuntimeError):
    """Kimlik doğrulama ayarları güvensiz veya eksik; uygulama açılmamalı."""


@dataclass(frozen=True)
class CurrentUser:
    username: str
    roles: frozenset[str]

    def has_any(self, *roles: str) -> bool:
        # admin tüm rolleri kapsar
        return ROLE_ADMIN in self.roles or any(r in self.roles for r in roles)

    @property
    def is_requester_only(self) -> bool:
        """Yalnızca self-servis yetkisi var (agent/approver/admin değil): kendi kayıtlarıyla sınırlı."""
        return not self.has_any(ROLE_AGENT, ROLE_APPROVER)


def check_startup(settings: Settings) -> None:
    """Açılışta çağrılır: güvensiz yapılandırmada uygulamayı başlatmaz."""
    if settings.auth_mode == "dev":
        if settings.environment != "development":
            raise AuthConfigError(
                "SZ_AUTH_MODE=dev yalnızca SZ_ENVIRONMENT=development iken kullanılabilir "
                f"(şu an: {settings.environment})."
            )
        log.warning("Geliştirme kipi kimlik doğrulaması açık: belirteçler doğrulanmıyor!")
        return
    if not settings.auth_issuer or not settings.auth_audience:
        raise AuthConfigError(
            "OIDC kimlik doğrulaması için SZ_AUTH_ISSUER ve SZ_AUTH_AUDIENCE zorunludur."
        )


# --- JWKS önbelleği ---


class JWKSCache:
    """Yayıncının imza anahtarları; bilinmeyen kid'de (sınırlı sıklıkta) yenilenir."""

    def __init__(
        self,
        issuer: str,
        jwks_url: str | None = None,
        *,
        timeout: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl: float = 600.0,
        min_refresh_interval: float = 30.0,
        failure_backoff: float = 5.0,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self._jwks_url = jwks_url
        self._timeout = timeout
        self._transport = transport
        self._ttl = ttl
        self._min_refresh_interval = min_refresh_interval
        self._failure_backoff = failure_backoff
        self._keys: dict[str, jwt.PyJWK] = {}
        self._loaded_at: float | None = None
        self._last_forced: float | None = None
        # Son başarısız çekme: IdP erişilemezken her istek yeniden denemesin
        self._failed_at: float | None = None
        self._lock = asyncio.Lock()

    async def get_key(self, kid: str) -> jwt.PyJWK:
        now = time.monotonic()
        if self._loaded_at is None:
            await self._refresh(None)
        elif now - self._loaded_at > self._ttl:
            try:
                await self._refresh(self._loaded_at)
            except AuthError as exc:
                # IdP geçici olarak erişilemezse eski anahtarlarla devam; kısa süre sonra yeniden dene
                log.warning("JWKS yenilenemedi, önbellekteki anahtarlar kullanılıyor: %s", exc)
                self._loaded_at = now - self._ttl + self._min_refresh_interval
        key = self._keys.get(kid)
        if key is not None:
            return key
        # Anahtar döndürme olabilir: yenile; ama sahte kid'lerle IdP'yi boğdurma
        if self._last_forced is None or now - self._last_forced >= self._min_refresh_interval:
            self._last_forced = now
            await self._refresh(self._loaded_at)
            key = self._keys.get(kid)
        if key is None:
            raise AuthError(f"bilinmeyen anahtar kimliği (kid={kid!r})")
        return key

    async def _refresh(self, seen_loaded_at: float | None) -> None:
        async with self._lock:
            # Bekleyen eşzamanlı istek zaten yenilediyse tekrar çekme
            if self._loaded_at != seen_loaded_at:
                return
            if (
                self._failed_at is not None
                and time.monotonic() - self._failed_at < self._failure_backoff
            ):
                raise AuthError("JWKS kısa süre önce alınamadı; yeniden deneme bekleniyor")
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    url = self._jwks_url or await self._discover(client)
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
            except (httpx.HTTPError, ValueError, AuthError) as exc:
                self._failed_at = time.monotonic()
                if isinstance(exc, AuthError):
                    raise
                raise AuthError(f"JWKS alınamadı: {exc}") from exc
            keys: dict[str, jwt.PyJWK] = {}
            for raw in data.get("keys", []) if isinstance(data, dict) else []:
                if not isinstance(raw, dict) or not raw.get("kid") or raw.get("use") == "enc":
                    continue
                try:
                    keys[raw["kid"]] = jwt.PyJWK(raw)
                except jwt.PyJWTError:
                    continue  # desteklenmeyen anahtar türü
            self._keys = keys
            self._loaded_at = time.monotonic()
            self._failed_at = None

    async def _discover(self, client: httpx.AsyncClient) -> str:
        resp = await client.get(f"{self.issuer}/.well-known/openid-configuration")
        resp.raise_for_status()
        doc = resp.json()
        if str(doc.get("issuer", "")).rstrip("/") != self.issuer:
            raise AuthError("OIDC keşif belgesindeki issuer ayarla uyuşmuyor")
        jwks_uri = doc.get("jwks_uri")
        if not jwks_uri:
            raise AuthError("OIDC keşif belgesinde jwks_uri yok")
        self._jwks_url = jwks_uri
        return jwks_uri


# --- Belirteç doğrulama ---


def _claim(claims: dict[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _str_list(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(value.split())
    if isinstance(value, list):
        return {v for v in value if isinstance(v, str) and v}
    return set()


class OIDCAuthenticator:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not settings.auth_issuer or not settings.auth_audience:
            raise AuthConfigError(MSG_NOT_CONFIGURED)
        self.issuer = settings.auth_issuer
        self.audience = settings.auth_audience
        self.roles_claim = settings.auth_roles_claim
        self.username_claim = settings.auth_username_claim
        self.leeway = settings.auth_leeway_seconds
        self.jwks = JWKSCache(
            settings.auth_issuer,
            settings.auth_jwks_url,
            timeout=settings.auth_http_timeout,
            transport=transport,
        )

    async def authenticate(self, token: str) -> CurrentUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(f"belirteç çözümlenemedi: {exc}") from exc
        alg = header.get("alg")
        # alg saldırgan denetiminde: liste/nesne gelirse TypeError (500) yerine 401
        if not isinstance(alg, str) or alg not in ALLOWED_ALGORITHMS:
            raise AuthError(f"izin verilmeyen algoritma: {alg!r}")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError("belirteçte kid yok")
        jwk = await self.jwks.get_key(kid)
        if jwk.key_type != ALLOWED_ALGORITHMS[alg] or (
            jwk.algorithm_name and jwk.algorithm_name != alg
        ):
            raise AuthError("anahtar türü algoritmayla uyuşmuyor")
        try:
            claims = jwt.decode(
                token,
                jwk.key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"belirteç doğrulanamadı: {exc}") from exc
        return CurrentUser(self._username(claims), frozenset(self._roles(claims)))

    def _username(self, claims: dict[str, Any]) -> str:
        for name in (self.username_claim, *_USERNAME_FALLBACKS):
            value = _claim(claims, name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise AuthError("belirteçte kullanıcı adı yok")

    def _roles(self, claims: dict[str, Any]) -> set[str]:
        if self.roles_claim:
            return _str_list(_claim(claims, self.roles_claim))
        # Keycloak bölge + istemci rolleri, Entra uygulama rolleri
        roles = _str_list(_claim(claims, "realm_access.roles"))
        client_roles = (claims.get("resource_access") or {}).get(self.audience)
        if isinstance(client_roles, dict):
            roles |= _str_list(client_roles.get("roles"))
        return roles | _str_list(claims.get("roles"))


def parse_dev_token(token: str, settings: Settings) -> CurrentUser:
    """Geliştirme belirteci: ``dev:<kullanıcı>:<rol1,rol2>`` (yalnızca development)."""
    if settings.auth_mode != "dev" or settings.environment != "development":
        raise AuthError("geliştirme belirteci bu ortamda kabul edilmez")
    if not token.startswith(_DEV_PREFIX):
        raise AuthError("geliştirme belirteci biçimi geçersiz")
    parts = token[len(_DEV_PREFIX) :].split(":")
    if len(parts) != 2 or not parts[0].strip():
        raise AuthError("geliştirme belirteci biçimi geçersiz")
    roles = frozenset(r.strip() for r in parts[1].split(",") if r.strip())
    return CurrentUser(parts[0].strip(), roles)


# --- FastAPI bağımlılıkları ---

_bearer = HTTPBearer(auto_error=False, description="OIDC erişim belirteci (JWT)")
_authenticators: dict[tuple, OIDCAuthenticator] = {}


def get_auth_settings() -> Settings:
    """Kimlik doğrulama ayarları (testlerde dependency_overrides ile değiştirilir)."""
    return get_settings()


AuthSettingsDep = Annotated[Settings, Depends(get_auth_settings)]


def get_authenticator(settings: AuthSettingsDep) -> OIDCAuthenticator | None:
    """Ayarlara göre paylaşılan doğrulayıcı (JWKS önbelleği süreç boyunca yaşar)."""
    if settings.auth_mode != "oidc":
        return None
    cache_key = (
        settings.auth_issuer,
        settings.auth_audience,
        settings.auth_jwks_url,
        settings.auth_roles_claim,
        settings.auth_username_claim,
        settings.auth_leeway_seconds,
        settings.auth_http_timeout,
    )
    authenticator = _authenticators.get(cache_key)
    if authenticator is None:
        try:
            authenticator = OIDCAuthenticator(settings)
        except AuthConfigError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_NOT_CONFIGURED) from exc
        _authenticators[cache_key] = authenticator
    return authenticator


def _unauthorized(detail: str, error: str | None = None) -> HTTPException:
    challenge = f'Bearer error="{error}"' if error else "Bearer"
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": challenge}
    )


async def get_current_user(
    request: Request,
    settings: AuthSettingsDep,
    authenticator: Annotated[OIDCAuthenticator | None, Depends(get_authenticator)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        # Başlık var ama Bearer değil (örn. Basic) -> geçersiz; hiç yoksa -> eksik
        if request.headers.get("authorization"):
            raise _unauthorized(MSG_INVALID, "invalid_request")
        raise _unauthorized(MSG_MISSING)
    token = credentials.credentials
    try:
        if settings.auth_mode == "dev":
            user = parse_dev_token(token, settings)
        elif authenticator is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_NOT_CONFIGURED)
        else:
            user = await authenticator.authenticate(token)
    except AuthError as exc:
        log.info("Kimlik doğrulama reddedildi: %s", exc)
        raise _unauthorized(MSG_INVALID, "invalid_token") from exc
    request.state.user = user
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*roles: str):
    """Kullanıcının verilen rollerden en az birine (veya admin'e) sahip olmasını şart koşar."""

    async def _dependency(user: CurrentUserDep) -> CurrentUser:
        if not user.has_any(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, MSG_FORBIDDEN)
        return user

    return _dependency


# Uç noktalarda kullanılan hazır bağımlılıklar
AgentUser = Annotated[CurrentUser, Depends(require_roles(ROLE_AGENT))]
# Okuma: onaylayan da karar bağlamını görebilmeli
ReaderUser = Annotated[CurrentUser, Depends(require_roles(ROLE_AGENT, ROLE_APPROVER))]
ApproverUser = Annotated[CurrentUser, Depends(require_roles(ROLE_APPROVER))]
AdminUser = Annotated[CurrentUser, Depends(require_roles(ROLE_ADMIN))]
# Olay açma: agent veya self-servis requester (requester yalnızca kendi adına)
CreatorUser = Annotated[CurrentUser, Depends(require_roles(ROLE_AGENT, ROLE_REQUESTER))]
# Olay okuma: requester yalnızca arayanı kendisi olan kayıtları görür (uç noktada süzülür)
IncidentViewerUser = Annotated[
    CurrentUser, Depends(require_roles(ROLE_AGENT, ROLE_APPROVER, ROLE_REQUESTER))
]

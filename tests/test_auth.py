"""OIDC bearer kimlik doğrulama ve rol tabanlı yetkilendirme testleri.

Gerçek ağ yok: RSA/EC anahtarları testte üretilir, JWKS httpx.MockTransport ile sunulur.
"""

import time
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from serviszeka import auth, db
from serviszeka.api import incidents as incidents_api
from serviszeka.config import Settings, get_settings
from serviszeka.llm import FakeLLM
from serviszeka.main import app, lifespan

ISSUER = "https://sso.ornek.com/realms/serviszeka"
AUDIENCE = "serviszeka-api"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"


def _settings(**kw) -> Settings:
    data = {
        "auth_mode": "oidc",
        "environment": "production",
        "auth_issuer": ISSUER,
        "auth_audience": AUDIENCE,
    }
    data.update(kw)
    return Settings(**data)


class FakeIdP:
    """Keşif belgesini ve JWKS'yi sunan sahte kimlik sağlayıcı."""

    def __init__(self) -> None:
        self.keys: dict[str, object] = {}
        self.published: list[str] = []
        self.jwks_hits = 0
        self.discovery_hits = 0
        self.fail = False
        self.attempts = 0

    def add_rsa(self, kid: str, publish: bool = True):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.keys[kid] = key
        if publish:
            self.published.append(kid)
        return key

    def add_ec(self, kid: str):
        key = ec.generate_private_key(ec.SECP256R1())
        self.keys[kid] = key
        self.published.append(kid)
        return key

    def _jwk(self, kid: str) -> dict:
        key = self.keys[kid]
        if isinstance(key, rsa.RSAPrivateKey):
            jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
            jwk["alg"] = "RS256"
        else:
            jwk = ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
            jwk["alg"] = "ES256"
        jwk.update(kid=kid, use="sig")
        return jwk

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        if self.fail:
            raise httpx.ConnectTimeout("zaman aşımı", request=request)
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            self.discovery_hits += 1
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URL})
        if url == JWKS_URL:
            self.jwks_hits += 1
            return httpx.Response(200, json={"keys": [self._jwk(k) for k in self.published]})
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def token(self, kid: str = "k1", alg: str | None = None, **claims) -> str:
        now = int(time.time())
        payload = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "u-123",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "preferred_username": "ayse",
            "realm_access": {"roles": ["agent"]},
        }
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        key = self.keys[kid]
        alg = alg or ("RS256" if isinstance(key, rsa.RSAPrivateKey) else "ES256")
        return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture
def idp() -> FakeIdP:
    p = FakeIdP()
    p.add_rsa("k1")
    return p


@pytest.fixture
def authenticator(idp) -> auth.OIDCAuthenticator:
    return auth.OIDCAuthenticator(_settings(), transport=idp.transport())


# --- Belirteç doğrulama ---


async def test_valid_token_keycloak_roles(idp, authenticator):
    user = await authenticator.authenticate(idp.token())
    assert user.username == "ayse"
    assert user.roles == frozenset({"agent"})
    # JWKS adresi keşif belgesinden bulundu ve önbelleğe alındı
    await authenticator.authenticate(idp.token())
    assert idp.discovery_hits == 1 and idp.jwks_hits == 1


async def test_entra_roles_and_username_fallback(idp, authenticator):
    token = idp.token(
        realm_access=None,
        preferred_username=None,
        upn="mehmet@ornek.com",
        roles=["approver", "agent"],
    )
    user = await authenticator.authenticate(token)
    assert user.username == "mehmet@ornek.com"
    assert user.roles == frozenset({"approver", "agent"})


async def test_keycloak_client_roles_for_audience(idp, authenticator):
    token = idp.token(realm_access=None, resource_access={AUDIENCE: {"roles": ["admin"]}})
    assert (await authenticator.authenticate(token)).roles == frozenset({"admin"})


async def test_username_falls_back_to_sub(idp, authenticator):
    user = await authenticator.authenticate(idp.token(preferred_username=None))
    assert user.username == "u-123"


async def test_custom_claims(idp):
    a = auth.OIDCAuthenticator(
        _settings(auth_roles_claim="app.groups", auth_username_claim="email"),
        transport=idp.transport(),
    )
    user = await a.authenticate(
        idp.token(email="z@ornek.com", app={"groups": ["approver"]}, realm_access=None)
    )
    assert user.username == "z@ornek.com" and user.roles == frozenset({"approver"})


async def test_explicit_jwks_url_skips_discovery(idp):
    a = auth.OIDCAuthenticator(_settings(auth_jwks_url=JWKS_URL), transport=idp.transport())
    await a.authenticate(idp.token())
    assert idp.discovery_hits == 0 and idp.jwks_hits == 1


async def test_es256_accepted(idp, authenticator):
    idp.add_ec("e1")
    user = await authenticator.authenticate(idp.token(kid="e1"))
    assert user.username == "ayse"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 3600},  # süresi dolmuş
        {"aud": "baska-uygulama"},  # yanlış hedef kitle
        {"iss": "https://sahte.ornek.com/realms/x"},  # yanlış yayıncı
        {"nbf": int(time.time()) + 3600},  # henüz geçerli değil
        {"exp": None},  # exp zorunlu
        {"aud": None},  # aud zorunlu
    ],
    ids=["expired", "wrong-aud", "wrong-iss", "nbf-future", "no-exp", "no-aud"],
)
async def test_invalid_claims_rejected(idp, authenticator, claims):
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate(idp.token(**claims))


async def test_small_clock_skew_tolerated(idp, authenticator):
    token = idp.token(exp=int(time.time()) - 5)
    assert (await authenticator.authenticate(token)).username == "ayse"


async def test_unknown_kid_triggers_refresh(idp, authenticator):
    await authenticator.authenticate(idp.token())
    assert idp.jwks_hits == 1
    # Anahtar döndürme: IdP yeni anahtarı yayımlar, önbellekte henüz yok
    idp.add_rsa("k2")
    user = await authenticator.authenticate(idp.token(kid="k2"))
    assert user.username == "ayse" and idp.jwks_hits == 2


async def test_unknown_kid_refresh_is_rate_limited(idp, authenticator):
    idp.add_rsa("gizli", publish=False)  # IdP'de yayımlanmamış anahtar
    for _ in range(3):
        with pytest.raises(auth.AuthError):
            await authenticator.authenticate(idp.token(kid="gizli"))
    # İlk yükleme + en fazla bir yenileme; her istek IdP'ye gitmez
    assert idp.jwks_hits <= 2


async def test_alg_none_rejected(idp, authenticator):
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": now + 300, "preferred_username": "x"},
        None,
        algorithm="none",
        headers={"kid": "k1"},
    )
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate(token)
    assert idp.jwks_hits == 0  # imzasız belirteç anahtar bile aratmaz


async def test_hs256_rejected(idp, authenticator):
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": now + 300, "preferred_username": "x"},
        "paylasilan-gizli-anahtar-en-az-32-bayt-uzunlukta",
        algorithm="HS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate(token)


async def test_alg_key_mismatch_rejected(idp, authenticator):
    # RSA anahtarının kid'i ile ES256 başlığı: anahtar türü uyuşmaz
    ec_key = ec.generate_private_key(ec.SECP256R1())
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": now + 300},
        ec_key,
        algorithm="ES256",
        headers={"kid": "k1"},
    )
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate(token)


async def test_bad_signature_rejected(idp, authenticator):
    other = FakeIdP()
    other.add_rsa("k1")  # aynı kid, farklı anahtar
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate(other.token())


async def test_garbage_and_missing_kid_rejected(idp, authenticator):
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate("bu-bir-jwt-degil")
    key = idp.keys["k1"]
    token = jwt.encode({"iss": ISSUER, "aud": AUDIENCE}, key, algorithm="RS256")
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate(token)


async def test_non_string_alg_header_is_auth_error(idp, authenticator):
    # alg başlığı liste/nesne olabilir (saldırgan denetiminde): 500 değil, 401 olmalı
    import base64
    import json

    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    for alg in (["RS256"], {"x": 1}):
        token = f"{b64({'alg': alg, 'kid': 'k1'})}.{b64({'iss': ISSUER})}.c2ln"
        with pytest.raises(auth.AuthError):
            await authenticator.authenticate(token)


async def test_jwks_unreachable_is_auth_error(idp, authenticator):
    idp.fail = True
    with pytest.raises(auth.AuthError):
        await authenticator.authenticate(idp.token())


async def test_initial_jwks_failure_backs_off(idp, authenticator):
    # Önbellek boşken IdP erişilemezse her (sahte) istek IdP'ye gidip kilitte sıraya girmez
    idp.fail = True
    for _ in range(5):
        with pytest.raises(auth.AuthError):
            await authenticator.authenticate(idp.token())
    assert idp.attempts == 1
    # Bekleme süresi dolunca yeniden denenir
    authenticator.jwks._failed_at -= 10_000
    idp.fail = False
    assert (await authenticator.authenticate(idp.token())).username == "ayse"


# --- Geliştirme kipi ---


def test_dev_token_parsed_only_in_development():
    s = Settings(auth_mode="dev", environment="development")
    user = auth.parse_dev_token("dev:ayse:agent,approver", s)
    assert user == auth.CurrentUser("ayse", frozenset({"agent", "approver"}))
    with pytest.raises(auth.AuthError):
        auth.parse_dev_token("dev:ayse:agent", Settings(auth_mode="dev", environment="production"))
    with pytest.raises(auth.AuthError):
        auth.parse_dev_token("dev:ayse:agent", _settings())
    for bad in ("dev::agent", "dev:ayse", "ayse:agent"):
        with pytest.raises(auth.AuthError):
            auth.parse_dev_token(bad, s)


def test_safe_defaults(monkeypatch):
    monkeypatch.delenv("SZ_AUTH_MODE", raising=False)
    monkeypatch.delenv("SZ_ENVIRONMENT", raising=False)
    s = Settings()
    assert s.auth_mode == "oidc" and s.environment == "production"


@pytest.mark.parametrize("env", ["production", "staging"])
def test_dev_mode_refused_outside_development(env):
    with pytest.raises(auth.AuthConfigError):
        auth.check_startup(Settings(auth_mode="dev", environment=env))
    auth.check_startup(Settings(auth_mode="dev", environment="development"))


def test_oidc_requires_issuer_and_audience():
    with pytest.raises(auth.AuthConfigError):
        auth.check_startup(_settings(auth_issuer=None))
    with pytest.raises(auth.AuthConfigError):
        auth.check_startup(_settings(auth_audience=None))
    auth.check_startup(_settings())


async def test_lifespan_refuses_dev_mode_in_production(monkeypatch):
    monkeypatch.setenv("SZ_AUTH_MODE", "dev")
    monkeypatch.setenv("SZ_ENVIRONMENT", "production")
    monkeypatch.setenv("SZ_AUTO_CREATE_SCHEMA", "false")
    get_settings.cache_clear()
    with pytest.raises(auth.AuthConfigError):
        async with lifespan(app):
            pass


# --- API: 401 / 403 / kimlik ---


@pytest.fixture
async def oidc_client(session, idp) -> AsyncIterator[httpx.AsyncClient]:
    """OIDC kipinde uygulama; JWKS sahte IdP'den gelir."""

    async def _get_session():
        async with db.get_sessionmaker()() as s:
            yield s

    settings = _settings(ai_shadow_mode=True)
    authenticator = auth.OIDCAuthenticator(settings, transport=idp.transport())
    app.dependency_overrides[db.get_session] = _get_session
    app.dependency_overrides[auth.get_auth_settings] = lambda: settings
    app.dependency_overrides[auth.get_authenticator] = lambda: authenticator
    app.dependency_overrides[incidents_api.get_app_settings] = lambda: settings
    app.dependency_overrides[incidents_api.get_llm_client] = lambda: FakeLLM(
        [
            {
                "kind": "incident",
                "category": "ag",
                "service": "vpn",
                "impact": "medium",
                "urgency": "medium",
                "assignment_group": "network_ops",
                "confidence": 0.9,
            }
        ]
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_missing_token_401(oidc_client):
    r = await oidc_client.get("/incidents")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert r.json()["detail"] == "Kimlik doğrulama gerekli"


async def test_invalid_token_401(oidc_client, idp):
    for headers in (
        {"Authorization": "Basic YWxpOnBhcm9sYQ=="},
        _bearer("bozuk"),
        _bearer(idp.token(exp=int(time.time()) - 3600)),
        _bearer("dev:ayse:admin"),  # OIDC kipinde geliştirme belirteci geçmez
    ):
        r = await oidc_client.get("/incidents", headers=headers)
        assert r.status_code == 401, headers
        assert r.headers["www-authenticate"].startswith("Bearer")
        assert r.json()["detail"] == "Geçersiz veya süresi dolmuş erişim belirteci"


async def test_health_and_docs_are_public(oidc_client):
    assert (await oidc_client.get("/health")).status_code == 200
    assert (await oidc_client.get("/openapi.json")).status_code == 200
    assert (await oidc_client.get("/docs")).status_code == 200


async def test_every_api_route_requires_auth(oidc_client):
    # OpenAPI şemasındaki tüm yollar (iç içe yönlendiriciler dahil); yalnızca /health açık
    checked = 0
    for route_path, ops in app.openapi()["paths"].items():
        if route_path == "/health":
            continue
        path = route_path.replace("{", "").replace("}", "")
        for method in ops:
            r = await oidc_client.request(method.upper(), path)
            assert r.status_code == 401, (method, route_path, r.status_code)
            checked += 1
    assert checked >= 20


async def test_actor_comes_from_token_not_header(oidc_client, idp):
    token = idp.token(preferred_username="ayse", realm_access={"roles": ["agent"]})
    r = await oidc_client.post(
        "/incidents",
        json={"title": "VPN yok", "caller": "ayse"},
        headers={**_bearer(token), "X-Actor": "sahte.kisi"},
    )
    assert r.status_code == 201, r.text
    admin = idp.token(preferred_username="yonetici", realm_access={"roles": ["admin"]})
    items = (await oidc_client.get("/audit", headers=_bearer(admin))).json()["items"]
    actors = {i["actor"] for i in items if i["action"] == "incident.create"}
    assert actors == {"ayse"}


async def test_missing_role_403(oidc_client, idp):
    agent = _bearer(idp.token(realm_access={"roles": ["agent"]}))
    approver_only = _bearer(idp.token(realm_access={"roles": ["approver"]}))
    no_roles = _bearer(idp.token(realm_access={"roles": []}))

    r = await oidc_client.get("/audit", headers=agent)
    assert r.status_code == 403
    assert r.json()["detail"] == "Bu işlem için yetkiniz yok"
    assert (
        await oidc_client.post("/integrations/servicenow/pull", headers=agent)
    ).status_code == 403
    assert (await oidc_client.get("/integrations/conflicts", headers=agent)).status_code == 403
    assert (
        await oidc_client.post("/incidents/decisions/x/approve", headers=agent)
    ).status_code == 403
    r = await oidc_client.post(
        "/incidents", json={"title": "x", "caller": "y"}, headers=approver_only
    )
    assert r.status_code == 403
    assert (
        await oidc_client.post("/knowledge/articles", json={}, headers=approver_only)
    ).status_code == 403
    assert (await oidc_client.get("/incidents", headers=no_roles)).status_code == 403


async def test_admin_implies_all_roles(oidc_client, idp):
    admin = _bearer(idp.token(realm_access={"roles": ["admin"]}))
    assert (await oidc_client.get("/audit/verify", headers=admin)).status_code == 200
    r = await oidc_client.post("/incidents", json={"title": "x", "caller": "y"}, headers=admin)
    assert r.status_code == 201
    assert (await oidc_client.get("/integrations/status", headers=admin)).status_code == 200


async def test_approver_identity_recorded_from_token(oidc_client, idp):
    agent = _bearer(idp.token(preferred_username="ayse", realm_access={"roles": ["agent"]}))
    approver = _bearer(
        idp.token(preferred_username=None, upn="sef.ali@ornek.com", roles=["approver"])
    )
    inc = (
        await oidc_client.post("/incidents", json={"title": "VPN", "caller": "c"}, headers=agent)
    ).json()
    dec = (await oidc_client.post(f"/incidents/{inc['id']}/triage", headers=agent)).json()
    assert dec["outcome"] == "suggested"
    url = f"/incidents/decisions/{dec['id']}/approve"

    # Başkası adına onay verilemez: gövdedeki approver belirteçteki kişiyle aynı olmalı
    r = await oidc_client.post(url, json={"approver": "baska.biri"}, headers=approver)
    assert r.status_code == 403
    assert r.json()["detail"] == "Başkası adına onay verilemez"

    r = await oidc_client.post(url, headers={**approver, "X-Actor": "sahte"})
    assert r.status_code == 200, r.text
    assert r.json()["approved_by"] == "sef.ali@ornek.com"


async def test_stale_keys_used_when_idp_down_after_ttl(idp, authenticator):
    await authenticator.authenticate(idp.token())
    authenticator.jwks._loaded_at -= 10_000  # TTL doldu
    idp.fail = True
    # IdP çökmüş olsa da önbellekteki anahtarla doğrulama sürer
    assert (await authenticator.authenticate(idp.token())).username == "ayse"
    # Yeniden deneme hemen her istekte yapılmaz
    hits = idp.jwks_hits
    await authenticator.authenticate(idp.token())
    assert idp.jwks_hits == hits


async def test_username_prefers_upn_over_mutable_email():
    # email kullanıcı tarafından değiştirilebilir; upn kimlik için daha güvenilir
    from serviszeka.auth import _USERNAME_FALLBACKS

    assert _USERNAME_FALLBACKS.index("upn") < _USERNAME_FALLBACKS.index("email")

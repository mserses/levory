"""Keycloak Admin REST API kimlik bağlayıcısı: httpx.MockTransport ile (gerçek ağ yok)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, parse_qsl, quote

import httpx
import pytest

from serviszeka.config import Settings
from serviszeka.connectors import (
    IDENTITY,
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    IdentityProvider,
)
from serviszeka.connectors.keycloak import (
    AmbiguousReferenceError,
    KeycloakClient,
    KeycloakIdentityProvider,
    normalize_ref,
)
from serviszeka.connectors.setup import ConnectorConfigError, build, check_startup

SERVER = "https://sso.ornek.com"
REALM = "serviszeka"
ADMIN = f"{SERVER}/admin/realms/{REALM}"
TOKEN_URL = f"{SERVER}/realms/{REALM}/protocol/openid-connect/token"
SECRET = "cok-gizli-istemci-anahtari"

ALI_ID = "0b6f7c0e-1111-4a4a-9b9b-000000000001"
ZEYNEP_ID = "0b6f7c0e-2222-4a4a-9b9b-000000000002"
ALI = {
    "id": ALI_ID,
    "username": "ali",
    "email": "ali@ornek.com",
    "firstName": "Ali",
    "lastName": "Demir",
    "enabled": True,
    "attributes": {"manager": ["zeynep"]},
}
ZEYNEP = {
    "id": ZEYNEP_ID,
    "username": "zeynep",
    "email": "zeynep@ornek.com",
    "firstName": "Zeynep",
    "lastName": "Kaya",
    "enabled": True,
}
VPN = {"id": "g-vpn", "name": "vpn-kullanicilari", "path": "/vpn-kullanicilari", "subGroups": []}
ADMINS = {
    "id": "g-admins",
    "name": "Admins",
    "path": "/Admins",
    "subGroups": [{"id": "g-admins-db", "name": "db", "path": "/Admins/db", "subGroups": []}],
}


class FakeKeycloak:
    """İstekleri kaydeder; (yöntem, yol) ya da (yöntem, yol, sorgu) anahtarına göre yanıt verir."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.token_status = 200
        self.expires_in = 300
        self.routes: dict[tuple, object] = {}
        self.reject_token: set[str] = set()

    def route(self, method: str, path: str, response, query: str | None = None) -> None:
        key = frozenset(parse_qsl(query, keep_blank_values=True)) if query is not None else None
        self.routes[(method, path, key)] = response

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(TOKEN_URL):
            self.token_calls += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "unauthorized_client"})
            return httpx.Response(
                200, json={"access_token": f"tok-{self.token_calls}", "expires_in": self.expires_in}
            )
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token in self.reject_token:
            return httpx.Response(401)
        assert url.startswith(ADMIN), url
        path = request.url.path.removeprefix(f"/admin/realms/{REALM}")
        query = frozenset(request.url.params.multi_items())
        resp = self.routes.get((request.method, path, query))
        if resp is None:
            resp = self.routes.get((request.method, path, None))
        if resp is None and request.method == "GET" and path in ("/users", "/groups"):
            resp = []  # arama: eşleşme yok
        if resp is None:
            return httpx.Response(404, json={"error": "not found"})
        if callable(resp):
            resp = resp(request)
        if isinstance(resp, httpx.Response):
            return resp
        if resp == 204:
            return httpx.Response(204)
        return httpx.Response(200, json=resp)

    def writes(self) -> list[tuple[str, str]]:
        return [
            (r.method, r.url.path.removeprefix(f"/admin/realms/{REALM}"))
            for r in self.requests
            if r.method in ("PUT", "POST", "DELETE") and not str(r.url).startswith(TOKEN_URL)
        ]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def kc() -> FakeKeycloak:
    fake = FakeKeycloak()
    fake.route("GET", f"/users/{ALI_ID}", ALI)
    fake.route("GET", f"/users/{ZEYNEP_ID}", ZEYNEP)
    fake.route("GET", "/users", [ALI], query="username=ali&exact=true")
    fake.route("GET", "/users", [ZEYNEP], query="username=zeynep&exact=true")
    return fake


def make(fake: FakeKeycloak, clock: Clock | None = None, **kwargs) -> KeycloakIdentityProvider:
    client = KeycloakClient(
        SERVER,
        REALM,
        "serviszeka-connector",
        SECRET,
        transport=httpx.MockTransport(fake.handler),
        clock=clock or Clock(),
    )
    return KeycloakIdentityProvider(client, **kwargs)


# --- istemci: belirteç ------------------------------------------------------------------------------


async def test_token_request_and_caching(kc):
    clock = Clock()
    idp = make(kc, clock)
    await idp.get_user(ALI_ID)
    await idp.get_user(ALI_ID)
    assert kc.token_calls == 1
    token_req = kc.requests[0]
    assert str(token_req.url) == TOKEN_URL and token_req.method == "POST"
    form = parse_qs(token_req.content.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": ["serviszeka-connector"],
        "client_secret": [SECRET],
    }
    assert kc.requests[1].headers["Authorization"] == "Bearer tok-1"
    clock.now += 300  # süre doldu -> yeni belirteç
    await idp.get_user(ALI_ID)
    assert kc.token_calls == 2
    assert kc.requests[-1].headers["Authorization"] == "Bearer tok-2"


async def test_token_refreshed_once_on_401(kc):
    idp = make(kc)
    kc.reject_token.add("tok-1")
    user = await idp.get_user(ALI_ID)
    assert user is not None and user.id == ALI_ID
    assert kc.token_calls == 2


async def test_persistent_401_is_auth_error_without_secrets(kc):
    idp = make(kc)
    kc.reject_token.update({"tok-1", "tok-2", "tok-3"})
    with pytest.raises(ConnectorAuthError) as exc:
        await idp.get_user(ALI_ID)
    assert kc.token_calls == 2
    assert SECRET not in str(exc.value) and "tok-" not in str(exc.value)


async def test_token_failure_is_auth_error_without_secret(kc):
    kc.token_status = 401
    idp = make(kc)
    with pytest.raises(ConnectorAuthError) as exc:
        await idp.get_user(ALI_ID)
    assert SECRET not in str(exc.value)
    assert "401" in str(exc.value)


def test_client_requires_credentials():
    with pytest.raises(ConnectorAuthError):
        KeycloakClient(SERVER, REALM, "c", "")
    with pytest.raises(ConnectorAuthError):
        KeycloakClient("", REALM, "c", "s")


async def test_network_error_is_connector_error():
    def boom(request):
        raise httpx.ConnectError("bağlantı yok")

    client = KeycloakClient(SERVER, REALM, "c", SECRET, transport=httpx.MockTransport(boom))
    with pytest.raises(ConnectorAuthError):
        await KeycloakIdentityProvider(client).get_user("ali")


async def test_error_mapping(kc):
    idp = make(kc)
    kc.route("GET", f"/users/{ALI_ID}/groups", httpx.Response(403, json={"error": "HTTP 403"}))
    with pytest.raises(ConnectorAuthError):
        await idp.list_groups(ALI_ID)
    kc.route("GET", f"/users/{ALI_ID}/groups", httpx.Response(500, text="hata"))
    with pytest.raises(ConnectorError) as exc:
        await idp.list_groups(ALI_ID)
    assert "500" in str(exc.value)
    kc.route("GET", f"/users/{ALI_ID}/groups", httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(ConnectorError, match="429"):
        await idp.list_groups(ALI_ID)


# --- kullanıcı ---------------------------------------------------------------------------------------


async def test_protocol_conformance(kc):
    assert isinstance(make(kc), IdentityProvider)
    assert make(kc).name == "keycloak"


async def test_get_user_by_id(kc):
    user = await make(kc).get_user(ALI_ID)
    assert user is not None
    assert user.id == ALI_ID
    assert user.user_principal_name == "ali"
    assert user.display_name == "Ali Demir"
    assert user.mail == "ali@ornek.com"
    assert user.account_enabled is True


async def test_get_user_by_username_exact(kc):
    user = await make(kc).get_user("ali")
    assert user is not None and user.id == ALI_ID
    req = next(r for r in kc.requests if r.url.path.endswith("/users"))
    assert req.method == "GET"
    assert dict(req.url.params) == {"username": "ali", "exact": "true"}


async def test_get_user_username_casefold(kc):
    # Keycloak kullanıcı adlarını küçük harfle saklar; girdi normalize edilerek aranır
    user = await make(kc).get_user("ALİ".replace("İ", "I"))
    assert user is not None and user.id == ALI_ID


async def test_get_user_by_email(kc):
    kc.route("GET", "/users", [], query="username=ali%40ornek.com&exact=true")
    kc.route("GET", "/users", [ALI], query="email=ali%40ornek.com&exact=true")
    user = await make(kc).get_user("ali@ornek.com")
    assert user is not None and user.id == ALI_ID


async def test_get_user_email_ambiguous_rejected(kc):
    other = {**ZEYNEP, "email": "ali@ornek.com"}
    kc.route("GET", "/users", [], query="username=ali%40ornek.com&exact=true")
    kc.route("GET", "/users", [ALI, other], query="email=ali%40ornek.com&exact=true")
    with pytest.raises(AmbiguousReferenceError):
        await make(kc).get_user("ali@ornek.com")


async def test_get_user_username_vs_email_conflict_rejected(kc):
    # "ali@ornek.com" bir kullanıcının adı, başka bir kullanıcının e-postası -> belirsiz
    squatter = {**ZEYNEP, "username": "ali@ornek.com"}
    kc.route("GET", "/users", [squatter], query="username=ali%40ornek.com&exact=true")
    kc.route("GET", "/users", [ALI], query="email=ali%40ornek.com&exact=true")
    with pytest.raises(AmbiguousReferenceError):
        await make(kc).get_user("ali@ornek.com")


async def test_get_user_ignores_non_exact_results(kc):
    # Sunucu exact'i yok sayarsa bile yalnızca tam eşleşme kabul edilir
    kc.route("GET", "/users", [{**ALI, "username": "ali.veli"}], query="username=ali&exact=true")
    assert await make(kc).get_user("ali") is None


async def test_get_user_missing_returns_none(kc):
    kc.route("GET", "/users", [], query="username=yok&exact=true")
    assert await make(kc).get_user("yok") is None


async def test_get_manager_from_attribute(kc):
    manager = await make(kc).get_manager("ali")
    assert manager is not None and manager.id == ZEYNEP_ID


async def test_get_manager_custom_attribute(kc):
    kc.route("GET", f"/users/{ALI_ID}", {**ALI, "attributes": {"yonetici": ["zeynep"]}})
    assert await make(kc).get_manager("ali") is None
    manager = await make(kc, manager_attribute="yonetici").get_manager("ali")
    assert manager is not None and manager.id == ZEYNEP_ID


async def test_get_manager_absent_or_unknown(kc):
    assert await make(kc).get_manager(ZEYNEP_ID) is None
    kc.route("GET", f"/users/{ALI_ID}", {**ALI, "attributes": {"manager": ["kayip"]}})
    kc.route("GET", "/users", [], query="username=kayip&exact=true")
    assert await make(kc).get_manager("ali") is None


async def test_get_manager_multiple_values_ambiguous(kc):
    kc.route("GET", f"/users/{ALI_ID}", {**ALI, "attributes": {"manager": ["zeynep", "mehmet"]}})
    with pytest.raises(AmbiguousReferenceError):
        await make(kc).get_manager("ali")


async def test_get_manager_self_is_none(kc):
    kc.route("GET", f"/users/{ALI_ID}", {**ALI, "attributes": {"manager": ["ali"]}})
    assert await make(kc).get_manager("ali") is None


# --- gruplar -----------------------------------------------------------------------------------------


async def test_list_groups_paginated(kc):
    page1 = [{"id": f"g{i}", "name": f"g{i}", "path": f"/g{i}"} for i in range(100)]
    page2 = [{"id": "g-admins", "name": "Admins", "path": "/Admins"}]
    kc.route("GET", f"/users/{ALI_ID}/groups", page1, query="first=0&max=100")
    kc.route("GET", f"/users/{ALI_ID}/groups", page2, query="first=100&max=100")
    idp = make(kc, privileged_groups={"/admins"})
    groups = await idp.list_groups(ALI_ID)
    assert len(groups) == 101
    assert groups[-1].display_name == "/Admins"
    assert groups[-1].privileged is True
    assert groups[0].privileged is False


async def test_list_groups_unknown_user(kc):
    with pytest.raises(ConnectorNotFoundError):
        await make(kc).list_groups("bilinmeyen-id")


def _search(kc: FakeKeycloak, term: str, result: list) -> None:
    kc.route("GET", "/groups", result, query=f"search={quote(term, safe='')}&first=0&max=100")


async def test_get_group_by_id(kc):
    kc.route("GET", "/groups/g-vpn", VPN)
    group = await make(kc).get_group("g-vpn")
    assert group is not None and group.id == "g-vpn" and group.display_name == "/vpn-kullanicilari"


async def test_get_group_by_path_and_name(kc):
    _search(kc, "vpn-kullanicilari", [VPN])
    idp = make(kc)
    assert (await idp.get_group("/vpn-kullanicilari")).id == "g-vpn"
    assert (await idp.get_group("vpn-kullanicilari")).id == "g-vpn"


async def test_get_group_subgroup_by_path(kc):
    _search(kc, "db", [ADMINS])
    group = await make(kc).get_group("/Admins/db")
    assert group is not None and group.id == "g-admins-db"


async def test_get_group_case_and_unicode_normalized(kc):
    # Tam genişlikli / büyük harfli varyant aynı gruba çözülür (NFKC + casefold)
    _search(kc, "admins", [ADMINS])
    idp = make(kc)
    assert (await idp.get_group("ＡＤＭＩＮＳ")).id == "g-admins"
    assert (await idp.get_group("/admins")).id == "g-admins"


async def test_get_group_no_fuzzy_match(kc):
    _search(kc, "admin", [ADMINS])  # sunucu alt dize eşleşmesi döndürse bile
    assert await make(kc).get_group("admin") is None


async def test_get_group_ambiguous_name_rejected(kc):
    other = {"id": "g-x", "name": "db", "path": "/Baska/db", "subGroups": []}
    _search(kc, "db", [ADMINS, {"id": "g-baska", "name": "Baska", "path": "/Baska",
                                 "subGroups": [other]}])
    idp = make(kc)
    with pytest.raises(AmbiguousReferenceError):
        await idp.get_group("db")
    # tam yol belirsizliği giderir
    assert (await idp.get_group("/Baska/db")).id == "g-x"


async def test_get_group_case_collision_rejected(kc):
    twin = {"id": "g-admins2", "name": "admins", "path": "/admins", "subGroups": []}
    _search(kc, "admins", [ADMINS, twin])
    with pytest.raises(AmbiguousReferenceError):
        await make(kc).get_group("/ADMINS")


def test_normalize_ref():
    assert normalize_ref("  ＡＤＭＩＮＳ ") == "admins"
    assert normalize_ref("Straße") == normalize_ref("STRASSE")


async def test_is_privileged_configured_by_any_reference(kc):
    kc.route("GET", "/groups/g-admins", ADMINS)
    kc.route("GET", "/groups/g-admins/role-mappings", {})
    _search(kc, "admins", [ADMINS])
    idp = make(kc, privileged_groups={"/Admins"})
    assert await idp.is_privileged_group("g-admins") is True  # kimlikle
    assert await idp.is_privileged_group("ADMINS") is True  # ad, büyük harf
    assert await idp.is_privileged_group("ａｄｍｉｎｓ") is True  # tam genişlik


async def test_is_privileged_subgroup_inherits(kc):
    _search(kc, "db", [ADMINS])
    kc.route("GET", "/groups/g-admins-db/role-mappings", {})
    idp = make(kc, privileged_groups={"/Admins"})
    assert await idp.is_privileged_group("/Admins/db") is True


async def test_is_privileged_by_realm_management_roles(kc):
    kc.route("GET", "/groups/g-vpn", VPN)
    kc.route(
        "GET",
        "/groups/g-vpn/role-mappings",
        {"clientMappings": {"realm-management": {"client": "realm-management",
                                                 "mappings": [{"name": "manage-users"}]}}},
    )
    assert await make(kc).is_privileged_group("g-vpn") is True


async def test_not_privileged_standard_group(kc):
    kc.route("GET", "/groups/g-vpn", VPN)
    kc.route("GET", "/groups/g-vpn/role-mappings", {"realmMappings": [{"name": "vpn"}]})
    assert await make(kc, privileged_groups={"/Admins"}).is_privileged_group("g-vpn") is False


async def test_unknown_or_ambiguous_group_is_privileged(kc):
    _search(kc, "yok", [])
    assert await make(kc).is_privileged_group("yok") is True
    twin = {"id": "g-admins2", "name": "admins", "path": "/admins", "subGroups": []}
    _search(kc, "admins", [ADMINS, twin])
    assert await make(kc).is_privileged_group("admins") is True


# --- yazma işlemleri ---------------------------------------------------------------------------------


async def test_add_to_group(kc):
    kc.route("GET", "/groups/g-vpn", VPN)
    kc.route("GET", f"/users/{ALI_ID}/groups", [], query="first=0&max=100")
    kc.route("PUT", f"/users/{ALI_ID}/groups/g-vpn", 204)
    result = await make(kc).add_to_group("ali", "g-vpn")
    assert result == {"dry_run": False, "user_id": ALI_ID, "group_id": "g-vpn",
                      "already_member": False}
    assert kc.writes() == [("PUT", f"/users/{ALI_ID}/groups/g-vpn")]


async def test_add_to_group_by_path_uses_resolved_id(kc):
    _search(kc, "vpn-kullanicilari", [VPN])
    kc.route("GET", f"/users/{ALI_ID}/groups", [VPN], query="first=0&max=100")
    kc.route("PUT", f"/users/{ALI_ID}/groups/g-vpn", 204)
    result = await make(kc).add_to_group("ali", "/vpn-kullanicilari")
    assert result["group_id"] == "g-vpn" and result["already_member"] is True
    assert kc.writes() == [("PUT", f"/users/{ALI_ID}/groups/g-vpn")]


async def test_add_to_group_dry_run_and_missing(kc):
    kc.route("GET", "/groups/g-vpn", VPN)
    kc.route("GET", f"/users/{ALI_ID}/groups", [], query="first=0&max=100")
    result = await make(kc).add_to_group("ali", "g-vpn", dry_run=True)
    assert result["dry_run"] is True and kc.writes() == []
    _search(kc, "yok", [])
    with pytest.raises(ConnectorNotFoundError):
        await make(kc).add_to_group("ali", "yok")
    kc.route("GET", "/users", [], query="username=kimse&exact=true")
    with pytest.raises(ConnectorNotFoundError):
        await make(kc).add_to_group("kimse", "g-vpn")


async def test_add_to_group_ambiguous_never_writes(kc):
    twin = {"id": "g-admins2", "name": "admins", "path": "/admins", "subGroups": []}
    _search(kc, "admins", [ADMINS, twin])
    with pytest.raises(AmbiguousReferenceError):
        await make(kc).add_to_group("ali", "admins")
    assert kc.writes() == []


async def test_remove_from_group(kc):
    kc.route("GET", "/groups/g-vpn", VPN)
    kc.route("GET", f"/users/{ALI_ID}/groups", [VPN], query="first=0&max=100")
    kc.route("DELETE", f"/users/{ALI_ID}/groups/g-vpn", 204)
    result = await make(kc).remove_from_group("ali", "g-vpn")
    assert result == {"dry_run": False, "user_id": ALI_ID, "group_id": "g-vpn",
                      "was_member": True}
    assert kc.writes() == [("DELETE", f"/users/{ALI_ID}/groups/g-vpn")]


async def test_remove_from_group_not_member_is_noop(kc):
    kc.route("GET", "/groups/g-vpn", VPN)
    kc.route("GET", f"/users/{ALI_ID}/groups", [], query="first=0&max=100")
    result = await make(kc).remove_from_group("ali", "g-vpn")
    assert result["was_member"] is False and kc.writes() == []


async def test_revoke_sessions(kc):
    kc.route("POST", f"/users/{ALI_ID}/logout", 204)
    assert await make(kc).revoke_sessions("ali", dry_run=True) == {"dry_run": True,
                                                                  "user_id": ALI_ID}
    assert kc.writes() == []
    assert await make(kc).revoke_sessions("ali") == {"dry_run": False, "user_id": ALI_ID}
    assert kc.writes() == [("POST", f"/users/{ALI_ID}/logout")]


async def test_set_account_enabled(kc):
    seen = []

    def put(request):
        seen.append(json.loads(request.content))
        return httpx.Response(204)

    kc.route("PUT", f"/users/{ALI_ID}", put)
    result = await make(kc).set_account_enabled("ali", False)
    assert result == {"dry_run": False, "user_id": ALI_ID, "enabled": False}
    assert seen == [{"enabled": False}]
    await make(kc).set_account_enabled("ali", True, dry_run=True)
    assert len(seen) == 1


async def test_reset_password_temporary(kc):
    seen = []

    def put(request):
        seen.append(json.loads(request.content))
        return httpx.Response(204)

    kc.route("PUT", f"/users/{ALI_ID}/reset-password", put)
    pw = "Gecici-Parola-123!"
    result = await make(kc).reset_password("ali", pw)
    assert seen == [{"type": "password", "value": pw, "temporary": True}]
    assert pw not in json.dumps(result)
    assert result["force_change_next_sign_in"] is True and result["user_id"] == ALI_ID
    await make(kc).reset_password("ali", pw, dry_run=True)
    assert len(seen) == 1


async def test_reset_password_policy_error_hides_password(kc):
    pw = "kisa"
    kc.route(
        "PUT",
        f"/users/{ALI_ID}/reset-password",
        httpx.Response(400, json={"error": "invalidPasswordMinLengthMessage",
                                  "error_description": f"parola {pw} çok kısa"}),
    )
    with pytest.raises(ConnectorError) as exc:
        await make(kc).reset_password("ali", pw)
    assert pw not in str(exc.value)
    assert "invalidPasswordMinLengthMessage" in str(exc.value)


async def test_reset_mfa_deletes_only_second_factors(kc):
    kc.route(
        "GET",
        f"/users/{ALI_ID}/credentials",
        [
            {"id": "c-pw", "type": "password"},
            {"id": "c-otp", "type": "otp"},
            {"id": "c-wa", "type": "webauthn"},
            {"id": "c-x", "type": "ozel-tur"},
        ],
    )
    kc.route("DELETE", f"/users/{ALI_ID}/credentials/c-otp", 204)
    kc.route("DELETE", f"/users/{ALI_ID}/credentials/c-wa", 204)
    dry = await make(kc).reset_mfa("ali", dry_run=True)
    assert dry == {"dry_run": True, "user_id": ALI_ID, "methods": ["otp", "webauthn"]}
    assert kc.writes() == []
    result = await make(kc).reset_mfa("ali")
    assert result == {"dry_run": False, "user_id": ALI_ID, "removed_methods": ["otp", "webauthn"]}
    assert kc.writes() == [
        ("DELETE", f"/users/{ALI_ID}/credentials/c-otp"),
        ("DELETE", f"/users/{ALI_ID}/credentials/c-wa"),
    ]


async def test_reset_mfa_partial_failure_reports_removed(kc):
    kc.route("GET", f"/users/{ALI_ID}/credentials",
             [{"id": "c-otp", "type": "otp"}, {"id": "c-wa", "type": "webauthn"}])
    kc.route("DELETE", f"/users/{ALI_ID}/credentials/c-otp", 204)
    kc.route("DELETE", f"/users/{ALI_ID}/credentials/c-wa", httpx.Response(500))
    with pytest.raises(ConnectorError, match="otp"):
        await make(kc).reset_mfa("ali")


async def test_unlock_account(kc):
    kc.route("GET", f"/attack-detection/brute-force/users/{ALI_ID}",
             {"numFailures": 5, "disabled": True, "lastIPFailure": "10.0.0.1"})
    kc.route("DELETE", f"/attack-detection/brute-force/users/{ALI_ID}", 204)
    dry = await make(kc).unlock_account("ali", dry_run=True)
    assert dry == {"dry_run": True, "user_id": ALI_ID, "was_locked": True}
    assert kc.writes() == []
    result = await make(kc).unlock_account("ali")
    assert result == {"dry_run": False, "user_id": ALI_ID, "was_locked": True}
    assert kc.writes() == [("DELETE", f"/attack-detection/brute-force/users/{ALI_ID}")]
    assert "10.0.0.1" not in json.dumps(result)


async def test_unlock_account_not_locked(kc):
    kc.route("GET", f"/attack-detection/brute-force/users/{ALI_ID}",
             {"numFailures": 0, "disabled": False})
    kc.route("DELETE", f"/attack-detection/brute-force/users/{ALI_ID}", 204)
    result = await make(kc).unlock_account("ali")
    assert result["was_locked"] is False


async def test_write_unknown_user_not_found(kc):
    kc.route("GET", "/users", [], query="username=kimse&exact=true")
    idp = make(kc)
    for call in (
        idp.revoke_sessions("kimse"),
        idp.set_account_enabled("kimse", False),
        idp.reset_password("kimse", "X-yz12345!"),
        idp.reset_mfa("kimse"),
        idp.unlock_account("kimse"),
    ):
        with pytest.raises(ConnectorNotFoundError):
            await call
    assert kc.writes() == []


async def test_path_segments_escaped(kc):
    idp = make(kc)
    assert await idp.get_user("../admin") is None
    assert await idp.get_user("..") is None
    assert await idp.get_group("..") is None
    paths = [r.url.raw_path.decode() for r in kc.requests if r.method == "GET"]
    assert all(p.startswith(f"/admin/realms/{REALM}/") for p in paths if "/token" not in p)
    assert not any(p.rstrip("/").endswith(("/..", f"/realms/{REALM}")) for p in paths)


# --- kurulum -----------------------------------------------------------------------------------------


def _kc_settings(**overrides) -> Settings:
    base = {
        "connector_provider": "keycloak",
        "keycloak_server_url": SERVER,
        "keycloak_realm": REALM,
        "keycloak_client_id": "serviszeka-connector",
        "keycloak_client_secret": SECRET,
    }
    return Settings(**{**base, **overrides})


def test_settings_defaults():
    s = Settings()
    assert s.keycloak_manager_attribute == "manager"
    assert s.keycloak_server_url is None and s.keycloak_client_secret is None


def test_check_startup_requires_keycloak_settings():
    with pytest.raises(ConnectorConfigError) as exc:
        check_startup(_kc_settings(keycloak_client_secret=None, keycloak_realm=None))
    assert "SZ_KEYCLOAK_CLIENT_SECRET" in str(exc.value)
    assert "SZ_KEYCLOAK_REALM" in str(exc.value)
    check_startup(_kc_settings())


async def test_build_keycloak_wires_identity(kc):
    items = build(
        _kc_settings(keycloak_manager_attribute="yonetici", identity_privileged_groups=["/Admins"]),
        transport=httpx.MockTransport(kc.handler),
    )
    assert set(items) == {IDENTITY}
    idp = items[IDENTITY]
    assert isinstance(idp, KeycloakIdentityProvider)
    assert idp.manager_attribute == "yonetici"
    assert await idp.get_user(ALI_ID) is not None

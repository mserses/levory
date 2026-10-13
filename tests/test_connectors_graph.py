"""Microsoft Graph / Teams bağlayıcıları: httpx.MockTransport ile (gerçek ağ yok)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from serviszeka.config import Settings
from serviszeka.connectors import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorNotSupportedError,
    IdentityProvider,
    LicenseProvider,
    MailProvider,
    Notifier,
)
from serviszeka.connectors.graph import (
    GraphClient,
    GraphIdentityProvider,
    GraphLicenseProvider,
    GraphMailProvider,
    TeamsGraphNotifier,
)
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.setup import ConnectorConfigError, build, build_graph, check_startup

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
TENANT = "kiraci-1"
ALI = {
    "id": "11111111-aaaa",
    "userPrincipalName": "ali@contoso.com",
    "displayName": "Ali Demir",
    "mail": "ali@contoso.com",
    "accountEnabled": True,
    "otherMails": ["ali@kisisel.ornek"],
}


class FakeGraph:
    """Basit Graph benzetimi: istekleri kaydeder, yol/yönteme göre yanıt verir."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.routes: dict[tuple[str, str], object] = {}
        self.expire_next_token = False

    def route(self, method: str, path: str, response) -> None:
        self.routes[(method, path)] = response

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(LOGIN):
            self.token_calls += 1
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["client_credentials"]
            assert form["scope"] == ["https://graph.microsoft.com/.default"]
            assert form["client_id"] == ["istemci"] and form["client_secret"] == ["gizli"]
            return httpx.Response(
                200, json={"access_token": f"tok-{self.token_calls}", "expires_in": 3600}
            )
        path = request.url.path.removeprefix("/v1.0")
        resp = self.routes.get((request.method, path))
        if resp is None:
            return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})
        if callable(resp):
            return resp(request)
        return resp

    @property
    def graph_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).startswith(GRAPH)]


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


def _client(graph: FakeGraph, clock=None) -> GraphClient:
    kw = {"clock": clock} if clock else {}
    return GraphClient(
        TENANT, "istemci", "gizli", base_url=GRAPH, login_url=LOGIN,
        transport=httpx.MockTransport(graph.handler), **kw,
    )


def _identity(graph: FakeGraph, **kw) -> GraphIdentityProvider:
    return GraphIdentityProvider(_client(graph), **kw)


# --- belirteç -----------------------------------------------------------------------------------


async def test_token_is_cached_until_expiry_and_refreshed_on_401(graph):
    now = [1000.0]
    client = _client(graph, clock=lambda: now[0])
    graph.route("GET", "/users/ali@contoso.com", httpx.Response(200, json=ALI))
    ident = GraphIdentityProvider(client)
    await ident.get_user("ali@contoso.com")
    await ident.get_user("ali@contoso.com")
    assert graph.token_calls == 1
    assert graph.graph_requests[0].headers["authorization"] == "Bearer tok-1"
    # Süre (3600 - 60 sn pay) dolunca yeni belirteç
    now[0] += 3541
    await ident.get_user("ali@contoso.com")
    assert graph.token_calls == 2
    # 401: belirteç bir kez yenilenir ve istek tekrarlanır
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(401) if calls["n"] == 1 else httpx.Response(200, json=ALI)

    graph.route("GET", "/users/u2", flaky)
    assert (await ident.get_user("u2")).id == ALI["id"]
    assert graph.token_calls == 3 and calls["n"] == 2


async def test_token_failure_is_auth_error_without_secret(graph):
    def deny(request):
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "x"})

    client = GraphClient(TENANT, "istemci", "gizli", transport=httpx.MockTransport(deny))
    with pytest.raises(ConnectorAuthError) as exc:
        await client.token()
    assert "invalid_client" in str(exc.value) and "gizli" not in str(exc.value)
    with pytest.raises(ConnectorAuthError):
        GraphClient("", "istemci", "gizli")


# --- kimlik ---------------------------------------------------------------------------------------


async def test_get_user_selects_fields_and_404_is_none(graph):
    graph.route("GET", "/users/ali@contoso.com", httpx.Response(200, json=ALI))
    ident = _identity(graph)
    user = await ident.get_user("ali@contoso.com")
    assert user.id == ALI["id"] and user.other_mails == ["ali@kisisel.ornek"]
    select = graph.graph_requests[0].url.params["$select"]
    assert {"id", "userPrincipalName", "accountEnabled", "otherMails"} <= set(select.split(","))
    assert await ident.get_user("yok@contoso.com") is None
    # Yol kesimi kaçışlanır (yol enjeksiyonu yok)
    await ident.get_user("a/../groups")
    assert "/users/a%2F..%2Fgroups" in str(graph.graph_requests[-1].url)


async def test_manager_and_member_of_with_paging_skips_directory_roles(graph):
    graph.route("GET", "/users/ali@contoso.com/manager",
                httpx.Response(200, json={**ALI, "id": "mgr", "userPrincipalName": "z@contoso.com"}))
    page2 = f"{GRAPH}/users/ali@contoso.com/memberOf?$skiptoken=abc"
    graph.route(
        "GET",
        "/users/ali@contoso.com/memberOf",
        lambda r: httpx.Response(
            200,
            json=(
                {"value": [{"@odata.type": "#microsoft.graph.group", "id": "g2",
                            "displayName": "Yöneticiler", "isAssignableToRole": True}]}
                if "skiptoken" in str(r.url)
                else {
                    "value": [
                        {"@odata.type": "#microsoft.graph.group", "id": "g1", "displayName": "VPN"},
                        {"@odata.type": "#microsoft.graph.directoryRole", "id": "r1"},
                    ],
                    "@odata.nextLink": page2,
                }
            ),
        ),
    )
    ident = _identity(graph)
    assert (await ident.get_manager("ali@contoso.com")).user_principal_name == "z@contoso.com"
    groups = await ident.list_groups("ali@contoso.com")
    assert [(g.id, g.privileged) for g in groups] == [("g1", False), ("g2", True)]
    assert await ident.get_manager("yoneticisiz") is None


async def test_privileged_group_detection(graph):
    graph.route("GET", "/groups/g-std", httpx.Response(200, json={"id": "g-std", "isAssignableToRole": False}))
    graph.route("GET", "/groups/g-role", httpx.Response(200, json={"id": "g-role", "isAssignableToRole": True}))
    ident = _identity(graph, privileged_groups={"g-config"})
    assert await ident.is_privileged_group("g-std") is False
    assert await ident.is_privileged_group("g-role") is True
    assert await ident.is_privileged_group("g-config") is True
    assert await ident.is_privileged_group("bilinmeyen") is True  # güvenli varsayılan


async def test_add_and_remove_member_requests_and_idempotency(graph):
    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("GET", "/groups/g1", httpx.Response(200, json={"id": "g1"}))
    graph.route("POST", "/groups/g1/members/$ref", httpx.Response(204))
    ident = _identity(graph)
    res = await ident.add_to_group("ali", "g1")
    post = next(r for r in graph.graph_requests if r.method == "POST")
    assert json.loads(post.content) == {"@odata.id": f"{GRAPH}/directoryObjects/{ALI['id']}"}
    assert res["already_member"] is False
    # Zaten üye: Graph 400 "already exist" -> başarı
    graph.route("POST", "/groups/g1/members/$ref", httpx.Response(
        400, json={"error": {"code": "Request_BadRequest",
                             "message": "One or more added object references already exist for the following modified properties: 'members'."}}))
    assert (await ident.add_to_group("ali", "g1"))["already_member"] is True
    # Başka 400 hatası yükseltilir
    graph.route("POST", "/groups/g1/members/$ref", httpx.Response(400, json={"error": {"code": "X", "message": "kötü"}}))
    with pytest.raises(ConnectorError):
        await ident.add_to_group("ali", "g1")
    with pytest.raises(ConnectorNotFoundError):
        await ident.add_to_group("ali", "yok")
    # dry_run yazmaz
    before = len([r for r in graph.graph_requests if r.method == "POST"])
    assert (await ident.add_to_group("ali", "g1", dry_run=True))["dry_run"] is True
    assert len([r for r in graph.graph_requests if r.method == "POST"]) == before
    # Çıkarma: 204 -> üyeydi; 404 -> üye değildi (başarı)
    graph.route("DELETE", f"/groups/g1/members/{ALI['id']}/$ref", httpx.Response(204))
    assert (await ident.remove_from_group("ali", "g1"))["was_member"] is True
    graph.route("DELETE", f"/groups/g1/members/{ALI['id']}/$ref", httpx.Response(404))
    assert (await ident.remove_from_group("ali", "g1"))["was_member"] is False


async def test_revoke_sessions_and_disable_user(graph):
    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("POST", f"/users/{ALI['id']}/revokeSignInSessions", httpx.Response(200, json={"value": True}))
    graph.route("PATCH", f"/users/{ALI['id']}", httpx.Response(204))
    ident = _identity(graph)
    await ident.revoke_sessions("ali")
    await ident.set_account_enabled("ali", False)
    patch = next(r for r in graph.graph_requests if r.method == "PATCH")
    assert json.loads(patch.content) == {"accountEnabled": False}


async def test_reset_password_uses_authentication_methods_api(graph):
    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("GET", f"/users/{ALI['id']}/authentication/passwordMethods", httpx.Response(
        200, json={"value": [{"id": "28c10230-6103-485e-b985-444c60001490"}]}))
    graph.route(
        "POST",
        f"/users/{ALI['id']}/authentication/methods/28c10230-6103-485e-b985-444c60001490/resetPassword",
        httpx.Response(202, headers={"Location": f"{GRAPH}/users/x/authentication/operations/op1"}),
    )
    ident = _identity(graph)
    res = await ident.reset_password("ali", "Gecici!Parola123")
    post = next(r for r in graph.graph_requests if r.method == "POST")
    assert json.loads(post.content) == {"newPassword": "Gecici!Parola123"}
    assert res["status"] == "accepted" and res["operation"].endswith("op1")
    assert "Gecici!Parola123" not in json.dumps(res)
    # Parola yöntemi yok -> açık hata
    graph.route("GET", f"/users/{ALI['id']}/authentication/passwordMethods", httpx.Response(200, json={"value": []}))
    with pytest.raises(ConnectorError):
        await ident.reset_password("ali", "x")


async def test_reset_mfa_deletes_non_password_methods(graph):
    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("GET", f"/users/{ALI['id']}/authentication/methods", httpx.Response(200, json={"value": [
        {"@odata.type": "#microsoft.graph.passwordAuthenticationMethod", "id": "p"},
        {"@odata.type": "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod", "id": "m1"},
        {"@odata.type": "#microsoft.graph.phoneAuthenticationMethod", "id": "3179e48a"},
    ]}))
    graph.route("DELETE", f"/users/{ALI['id']}/authentication/microsoftAuthenticatorMethods/m1", httpx.Response(204))
    graph.route("DELETE", f"/users/{ALI['id']}/authentication/phoneMethods/3179e48a", httpx.Response(204))
    ident = _identity(graph)
    res = await ident.reset_mfa("ali")
    assert res["removed_methods"] == ["microsoftAuthenticatorMethods", "phoneMethods"]
    deletes = [r.url.path for r in graph.graph_requests if r.method == "DELETE"]
    assert len(deletes) == 2 and not any("password" in p for p in deletes)


async def test_unlock_account_not_supported_in_entra_cloud(graph):
    with pytest.raises(ConnectorNotSupportedError) as exc:
        await _identity(graph).unlock_account("ali")
    assert "LDAP" in str(exc.value)
    assert graph.requests == []


async def test_graph_errors_are_translated(graph):
    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("POST", f"/users/{ALI['id']}/revokeSignInSessions",
                httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied"}}))
    ident = _identity(graph)
    with pytest.raises(ConnectorAuthError) as exc:
        await ident.revoke_sessions("ali")
    assert "Authorization_RequestDenied" in str(exc.value)
    graph.route("POST", f"/users/{ALI['id']}/revokeSignInSessions",
                httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(ConnectorError, match="7 sn"):
        await ident.revoke_sessions("ali")
    with pytest.raises(ConnectorNotFoundError):
        await ident.revoke_sessions("yok")


# --- lisans, posta, Teams ------------------------------------------------------------------------


async def test_licenses_subscribed_skus_and_assign(graph):
    graph.route("GET", "/subscribedSkus", httpx.Response(200, json={"value": [
        {"skuId": "sku-1", "skuPartNumber": "SPE_E3", "prepaidUnits": {"enabled": 10}, "consumedUnits": 7},
    ]}))
    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("POST", f"/users/{ALI['id']}/assignLicense", httpx.Response(200, json=ALI))
    client = _client(graph)
    lic = GraphLicenseProvider(client, GraphIdentityProvider(client))
    (sku,) = await lic.list_skus()
    assert (sku.sku_part_number, sku.available) == ("SPE_E3", 3)
    await lic.assign_license("ali", "sku-1")
    body = json.loads(next(r for r in graph.graph_requests if r.method == "POST").content)
    assert body == {"addLicenses": [{"skuId": "sku-1", "disabledPlans": []}], "removeLicenses": []}


async def test_shared_mailbox_permission_not_supported_by_graph(graph):
    with pytest.raises(ConnectorNotSupportedError, match="Add-MailboxPermission"):
        await GraphMailProvider(_client(graph), "bt@contoso.com").grant_shared_access(
            "muhasebe@contoso.com", "ali", "full_access"
        )


async def test_send_to_user_mail_and_sensitive_goes_to_secondary_address(graph):
    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("GET", "/users/veli", httpx.Response(200, json={**ALI, "id": "v", "otherMails": []}))
    graph.route("POST", "/users/bt@contoso.com/sendMail", httpx.Response(202))
    client = _client(graph)
    ident = GraphIdentityProvider(client)
    notifier = TeamsGraphNotifier(ident, GraphMailProvider(client, "bt@contoso.com"), {})
    await notifier.send_to_user("ali", "Konu", "Metin")
    await notifier.send_to_user("ali", "Parola", "gizli-metin", sensitive=True)
    posts = [json.loads(r.content) for r in graph.graph_requests if r.method == "POST"]
    assert posts[0]["message"]["toRecipients"] == [{"emailAddress": {"address": "ali@contoso.com"}}]
    assert posts[1]["message"]["toRecipients"] == [{"emailAddress": {"address": "ali@kisisel.ornek"}}]
    assert posts[1]["saveToSentItems"] is False
    # İkincil adres yoksa hassas bildirim gönderilmez (birincil posta kutusuna asla)
    with pytest.raises(ConnectorError):
        await notifier.send_to_user("veli", "Parola", "x", sensitive=True)
    assert len([r for r in graph.graph_requests if r.method == "POST"]) == 2
    with pytest.raises(ConnectorError):
        await GraphMailProvider(client, None).send_mail("a@b.c", "s", "b")


async def test_teams_webhook_posts_adaptive_card(graph):
    sent: list[httpx.Request] = []

    def hook(request):
        sent.append(request)
        return httpx.Response(202)

    client = _client(graph)
    ident = GraphIdentityProvider(client)
    notifier = TeamsGraphNotifier(
        ident, GraphMailProvider(client, None), {"servis-masasi": "https://webhook.ornek/abc"},
        transport=httpx.MockTransport(hook),
    )
    await notifier.post_to_channel("servis-masasi", "VPN bakımı 22:00")
    body = json.loads(sent[0].content)
    assert str(sent[0].url) == "https://webhook.ornek/abc"
    card = body["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["content"]["body"][0]["text"] == "VPN bakımı 22:00"
    with pytest.raises(ConnectorNotFoundError):
        await notifier.post_to_channel("bilinmeyen", "x")
    rejecting = TeamsGraphNotifier(
        ident, GraphMailProvider(client, None), {"k": "https://webhook.ornek/k"},
        transport=httpx.MockTransport(lambda r: httpx.Response(400)),
    )
    with pytest.raises(ConnectorError):
        await rejecting.post_to_channel("k", "x")


# --- kurulum ve protokol uyumu ---------------------------------------------------------------------


def test_providers_satisfy_protocols():
    client = GraphClient(TENANT, "istemci", "gizli")
    ident = GraphIdentityProvider(client)
    mail = GraphMailProvider(client, None)
    assert isinstance(ident, IdentityProvider)
    assert isinstance(GraphLicenseProvider(client, ident), LicenseProvider)
    assert isinstance(mail, MailProvider)
    assert isinstance(TeamsGraphNotifier(ident, mail, {}), Notifier)
    fake = InMemoryDirectory.demo()
    for proto in (IdentityProvider, MailProvider, LicenseProvider, Notifier):
        assert isinstance(fake, proto)


def test_startup_checks():
    with pytest.raises(ConnectorConfigError):
        check_startup(Settings(connector_provider="fake", environment="production"))
    with pytest.raises(ConnectorConfigError, match="SZ_GRAPH_CLIENT_SECRET"):
        check_startup(Settings(connector_provider="graph", graph_tenant_id="t", graph_client_id="c"))
    check_startup(Settings(connector_provider="fake", environment="development"))
    assert build(Settings(connector_provider="none")) == {}
    items = build_graph(Settings(connector_provider="graph", graph_tenant_id="t", graph_client_id="c",
                                 graph_client_secret="s", teams_webhook_urls={"a": "https://w"}))
    assert set(items) == {"identity", "mail", "license", "messaging"}


def test_group_lists_parse_from_env(monkeypatch):
    monkeypatch.setenv("SZ_IDENTITY_STANDARD_GROUPS", "g1, g2")
    monkeypatch.setenv("SZ_IDENTITY_PRIVILEGED_GROUPS", '["g9"]')
    monkeypatch.setenv("SZ_TEAMS_WEBHOOK_URLS", '{"servis-masasi": "https://w"}')
    s = Settings()
    assert s.identity_standard_groups == ["g1", "g2"] and s.identity_privileged_groups == ["g9"]
    assert s.teams_webhook_urls == {"servis-masasi": "https://w"}


async def test_startup_rejects_fake_connectors_outside_development(monkeypatch):
    from serviszeka import main

    monkeypatch.setattr(main, "get_settings", lambda: Settings(
        connector_provider="fake", environment="production", auth_mode="oidc",
        auth_issuer="https://sso", auth_audience="api", auto_create_schema=False,
    ))
    with pytest.raises(ConnectorConfigError):
        async with main.lifespan(main.app):
            pass


# --- araç boru hattı Graph ile uçtan uca -------------------------------------------------------------


async def test_tool_runs_against_graph_connector(session, graph):
    from serviszeka.auth import CurrentUser
    from serviszeka.connectors.setup import install
    from serviszeka.tools import ToolStatus, execute_tool
    from serviszeka.tools.connectors import connectors

    graph.route("GET", "/users/ali", httpx.Response(200, json=ALI))
    graph.route("GET", f"/users/{ALI['id']}", httpx.Response(200, json=ALI))
    graph.route("GET", "/groups/g-std", httpx.Response(200, json={"id": "g-std"}))
    graph.route("POST", "/groups/g-std/members/$ref", httpx.Response(204))
    settings = Settings(connector_provider="graph", graph_tenant_id=TENANT, graph_client_id="istemci",
                        graph_client_secret="gizli", graph_base_url=GRAPH,
                        identity_standard_groups=["g-std"], ai_shadow_mode=False)
    saved = connectors.snapshot()
    install(connectors, build_graph(settings, transport=httpx.MockTransport(graph.handler)))
    try:
        ai = CurrentUser("ai:copilot", frozenset({"ai_agent"}))
        r = await execute_tool(session, ai, "identity.add_to_group",
                               {"user": "ali", "group": "g-std"}, confidence=0.9, settings=settings)
        assert r.status == ToolStatus.EXECUTED and r.risk_class.value == "R1", r
        assert any(req.method == "POST" for req in graph.graph_requests)
        # Talepsiz kilit açma doğrulanmamıştır: R2, insan onayı ister. Onaylanınca Entra ID bulutta
        # kilit açma olmadığı için eylem "failed" (unavailable) olur, istisna değil
        agent = CurrentUser("ayse", frozenset({"agent"}))
        r = await execute_tool(session, agent, "identity.unlock_account", {"user": "ali"},
                               settings=settings)
        assert r.status == ToolStatus.PENDING_APPROVAL and r.risk_class.value == "R2", r
        from serviszeka.tools import actions

        ar = await actions.approve(session, r.action_request_id,
                                   CurrentUser("sef.ali", frozenset({"approver"})),
                                   settings=settings)
        assert ar.state == "failed" and ar.error, (ar.state, ar.error)
    finally:
        connectors.restore(saved)

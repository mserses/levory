"""İnceleme (Faz 3, odak B): karşılama ölçümü ve bağlayıcı regresyonları.

- Başarısız (ölçülmüş) talep yeniden denendikten sonra talep edence iptal edilirse kapı paydasından
  düşmemeli.
- Keycloak: kimlik olarak çözülen başvuru başka bir kullanıcının adıyla çakışıyorsa belirsizdir
  (asla ilk eşleşme seçilmez).
- Keycloak: kimliğiyle yapılandırılmış ayrıcalıklı grubun alt grupları da ayrıcalıklıdır.
- Graph istemci gizli anahtarı ayar `repr`'inde görünmez.
"""

from __future__ import annotations

import pytest
from _requests import ALI, APPROVER, DESK, LIVE_REQ, seeded_item, writes
from test_keycloak_connector import ADMINS, ALI_ID, FakeKeycloak, make
from test_keycloak_connector import ALI as KC_ALI

from serviszeka.config import Settings
from serviszeka.connectors.base import ConnectorError
from serviszeka.connectors.keycloak import AmbiguousReferenceError
from serviszeka.services import requests
from serviszeka.tools import actions


async def test_retry_then_requester_cancel_keeps_failed_request_in_gate_metric(
    session, directory
):
    # Ajan adına açılan parola sıfırlama (R2) onaylandı ama yürütme başarısız oldu: talep
    # "kapandı, insansız değil" olarak ölçüldü. Ajan yeniden dener (yeni eylem isteği bekler),
    # ardından talep eden iptal eder: talep paydadan düşüp oranı şişirmemeli.
    item = await seeded_item(session, "password_reset")
    req = await requests.create_request(
        session, item, requester=ALI.username, opened_by=DESK, form_values={}, settings=LIVE_REQ
    )
    assert req.state == "fulfilling" and req.waiting_action_id
    directory.failures["reset_password"] = ConnectorError("dizin yanıt vermiyor")
    await actions.approve(session, req.waiting_action_id, APPROVER, settings=LIVE_REQ)
    await session.refresh(req)
    assert req.state == "failed"
    before = await requests.touchless_metrics(session)
    assert (before.standard_closed, before.standard_touchless) == (1, 0)

    directory.failures.pop("reset_password")
    req = await requests.retry(session, req.id, DESK, settings=LIVE_REQ)
    assert req.state == "fulfilling" and req.waiting_action_id
    req = await requests.cancel(session, req.id, ALI, "parolamı hatırladım")
    assert req.state == "cancelled" and req.touchless is False
    after = await requests.touchless_metrics(session)
    assert (after.standard_closed, after.standard_touchless) == (1, 0)
    assert writes(directory, "reset_password") == []


# --- Keycloak --------------------------------------------------------------------------------------


@pytest.fixture
def kc() -> FakeKeycloak:
    fake = FakeKeycloak()
    fake.route("GET", f"/users/{ALI_ID}", KC_ALI)
    fake.route("GET", "/users", [KC_ALI], query="username=ali&exact=true")
    return fake


async def test_keycloak_id_colliding_with_other_username_is_ambiguous(kc):
    # Kullanıcı adı başka bir kullanıcının kimliğine eşit (self-servis kayıt / ad değiştirme): bu
    # kullanıcı oturumuyla talep açınca başvurusu kimlik olarak kurbanın hesabına çözülürdü.
    squatter = {"id": "0b6f7c0e-9999-4a4a-9b9b-000000000009", "username": ALI_ID,
                "enabled": True}
    kc.route("GET", "/users", [squatter], query=f"username={ALI_ID}&exact=true")
    idp = make(kc)
    with pytest.raises(AmbiguousReferenceError):
        await idp.get_user(ALI_ID)
    with pytest.raises(AmbiguousReferenceError):
        await idp.unlock_account(ALI_ID)
    with pytest.raises(AmbiguousReferenceError):
        await idp.reset_password(ALI_ID, "X-yz12345!abc")
    assert kc.writes() == []
    # Kontrol: çakışma yoksa kimlikle çözülür
    kc.route("GET", "/users", [], query=f"username={ALI_ID}&exact=true")
    assert (await idp.get_user(ALI_ID)).id == ALI_ID


async def test_keycloak_privileged_group_configured_by_id_covers_subgroups(kc):
    # Belgelenen sözleşme: ayrıcalıklı liste (kimlik/yol/ad) **ve alt grupları**. Kimlikle
    # yapılandırılan /Admins grubunun alt grubu /Admins/db, üst grubun rollerini devralır.
    db = ADMINS["subGroups"][0]
    kc.route("GET", "/groups/g-admins", ADMINS)
    kc.route("GET", "/groups/g-admins-db", db)
    kc.route("GET", "/groups/g-admins-db/role-mappings", {})
    idp = make(kc, privileged_groups={"g-admins"})
    assert await idp.is_privileged_group("g-admins") is True
    assert await idp.is_privileged_group("g-admins-db") is True
    # Kontrol: ilgisiz grup ayrıcalıklı değil
    vpn = {"id": "g-vpn", "name": "vpn", "path": "/vpn", "subGroups": []}
    kc.route("GET", "/groups/g-vpn", vpn)
    kc.route("GET", "/groups/g-vpn/role-mappings", {})
    assert await idp.is_privileged_group("g-vpn") is False


# --- Graph ----------------------------------------------------------------------------------------


def test_graph_client_secret_hidden_in_settings_repr():
    s = Settings(graph_tenant_id="t", graph_client_id="c", graph_client_secret="graph-cok-gizli")
    assert "graph-cok-gizli" not in repr(s) and "graph-cok-gizli" not in str(s)


def test_build_graph_sends_raw_client_secret():
    # SecretStr ayarı bağlayıcıya açık değeriyle geçmeli (yoksa belirteç isteği "****" gönderir)
    from serviszeka.connectors.base import IDENTITY
    from serviszeka.connectors.setup import build_graph

    s = Settings(graph_tenant_id="t", graph_client_id="c", graph_client_secret="graph-cok-gizli")
    assert build_graph(s)[IDENTITY].client._secret == "graph-cok-gizli"


async def test_graph_dot_segment_references_stay_inside_their_collection():
    # `..` / `.` grup ya da kullanıcı başvurusu httpx'te yol gezinmesine dönüşüp Graph kökünü
    # (`/v1.0/`, `/v1.0/members/$ref`) çağırmamalı; istek kendi koleksiyonunda kalır (404 -> yok).
    import httpx
    from test_connectors_graph import GRAPH, FakeGraph, _identity

    graph = FakeGraph()
    graph.route("GET", "/users/ali@contoso.com",
                httpx.Response(200, json={"id": "u1", "userPrincipalName": "ali@contoso.com"}))
    idp = _identity(graph)
    assert await idp.get_user("..") is None
    assert await idp.get_group("..") is None
    assert await idp.get_group(".") is None
    await idp.remove_from_group("ali@contoso.com", "..")
    paths = [r.url.raw_path.decode().split("?")[0] for r in graph.graph_requests]
    prefix = GRAPH.removeprefix("https://graph.microsoft.com")
    assert all(p.startswith((f"{prefix}/users/", f"{prefix}/groups/")) for p in paths), paths
    assert not any(p.endswith(("/v1.0/", "/members/$ref")) and "/groups/" not in p for p in paths)

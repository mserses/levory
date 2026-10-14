"""Hizmet kataloğu: form şeması, plan şablonları, tohum öğeler ve /catalog API."""

from __future__ import annotations

import httpx
import pytest
from _auth import ADMIN, AGENT, dev_headers

from serviszeka import db
from serviszeka.main import app
from serviszeka.services import catalog

SCHEMA = {
    "type": "object",
    "properties": {
        "group": {"type": "string", "format": "group", "title": "Grup"},
        "access": {"type": "string", "enum": ["full_access", "send_as"], "default": "full_access"},
        "remote": {"type": "boolean", "title": "Uzaktan"},
        "note": {"type": "string", "maxLength": 10},
    },
    "required": ["group"],
}


def test_form_schema_subset_is_enforced():
    assert catalog.validate_form_schema(SCHEMA)["type"] == "object"
    bad = [
        {"type": "array"},
        {"type": "object", "properties": {"x": {"type": "number"}}},
        {"type": "object", "properties": {"x": {"type": "string", "format": "email"}}},
        {"type": "object", "properties": {"X-y": {"type": "string"}}},
        {"type": "object", "properties": {"x": {"type": "string", "pattern": ".*"}}},
        {"type": "object", "properties": {"x": {"type": "string", "enum": []}}},
        {"type": "object", "properties": {}, "required": ["yok"]},
        {"type": "object", "properties": {}, "$ref": "#/x"},
    ]
    for schema in bad:
        with pytest.raises(catalog.CatalogError):
            catalog.validate_form_schema(schema)


def test_form_values_validated_with_defaults():
    clean = catalog.validate_form(SCHEMA, {"group": "  sg-vpn  "})
    assert clean == {"group": "sg-vpn", "access": "full_access", "remote": False}
    for values, needle in (
        ({}, "zorunlu"),
        ({"group": "g", "access": "owner"}, "izin verilen"),
        ({"group": "g", "remote": "evet"}, "evet/hayır"),
        ({"group": "g", "note": "x" * 11}, "en fazla"),
        ({"group": "g", "bilinmeyen": 1}, "Bilinmeyen"),
        ({"group": "g\nx"}, "geçersiz karakter"),
        ({"group": 5}, "metin"),
    ):
        with pytest.raises(catalog.CatalogError, match=needle):
            catalog.validate_form(SCHEMA, values)


def test_plan_validation_and_rendering():
    plan = catalog.validate_plan(
        [
            {"tool": "identity.add_to_group", "title": "Ekle",
             "args": {"user": "{{requester}}", "group": "{{form.group}}"}},
            {"manual": True, "title": "Teslim", "when": "form.remote"},
        ],
        SCHEMA,
    )
    assert len(plan) == 2
    for bad in (
        [{"tool": "yok.arac", "title": "x"}],
        [{"tool": "identity.add_to_group", "title": "x", "args": {"bilinmeyen": 1}}],
        [{"tool": "identity.add_to_group", "title": "x", "args": {"user": "{{form.yok}}"}}],
        [{"tool": "request.create", "title": "x", "args": {}}],
        [{"tool": "catalog.list_items", "title": "x", "args": {}}],
        [{"tool": "identity.add_to_group", "title": "", "args": {}}],
        [{"manual": True, "title": "x", "tool": "identity.add_to_group"}],
        [],
    ):
        with pytest.raises(catalog.CatalogError):
            catalog.validate_plan(bad, SCHEMA)
    ctx = catalog.template_context(
        requester="ali", request_id="r1", request_number="REQ0000001",
        form={"group": "sg-vpn", "remote": True},
    )
    args = catalog.render_args(
        {"user": "{{requester}}", "group": "{{ form.group }}", "flag": "{{form.remote}}",
         "message": "{{request.number}} için {{form.group}}", "empty": "{{form.note}}"},
        ctx,
    )
    assert args == {"user": "ali", "group": "sg-vpn", "flag": True,
                    "message": "REQ0000001 için sg-vpn"}
    assert catalog.step_enabled({"when": "form.remote"}, ctx)
    assert not catalog.step_enabled({"when": "form.note"}, ctx)


async def test_seed_default_items_is_idempotent(session):
    created = await catalog.seed_default_items(session)
    assert len(created) == 10
    assert await catalog.seed_default_items(session) == []
    items = await catalog.list_items(session)
    by_key = {i.key: i for i in items}
    assert {k for k, i in by_key.items() if i.standard} == {
        "password_reset", "account_unlock", "standard_software_group", "m365_license",
        "teams_channel_access", "vpn_access",
    }
    assert by_key["new_employee_account"].approval_policy == "group"
    assert by_key["shared_mailbox_access"].approval_policy == "manager"
    # Her plan geçerli (kayıtlı araçlar, geçerli yer tutucular)
    for item in items:
        catalog.validate_plan(item.fulfilment_plan, item.form_schema)


@pytest.fixture
async def api(session):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_catalog_api_roles_and_crud(api, session):
    await catalog.seed_default_items(session)
    await session.commit()
    requester = dev_headers("ali", "requester")
    r = await api.get("/catalog/items", headers=requester)
    assert r.status_code == 200 and len(r.json()) == 10
    r = await api.get("/catalog/items", params={"standard": True, "q": "vpn"}, headers=requester)
    assert [i["key"] for i in r.json()] == ["vpn_access"]
    assert (await api.get("/catalog/items/vpn_access", headers=requester)).status_code == 200
    assert (await api.get("/catalog/items/yok", headers=requester)).status_code == 404
    body = {
        "key": "yazici_erisimi",
        "name": "Yazıcı erişimi",
        "category": "Donanım",
        "fulfilment_plan": [{"tool": "notify.send_to_user", "title": "Bilgi",
                             "args": {"user": "{{requester}}", "subject": "s", "message": "m"}}],
    }
    assert (await api.post("/catalog/items", json=body, headers=AGENT)).status_code == 403
    r = await api.post("/catalog/items", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    item = r.json()
    assert (await api.post("/catalog/items", json=body, headers=ADMIN)).status_code == 422
    bad = {**body, "key": "kotu", "fulfilment_plan": [{"tool": "yok.arac", "title": "x"}]}
    r = await api.post("/catalog/items", json=bad, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"]["errors"]
    r = await api.patch(f"/catalog/items/{item['id']}", json={"active": False, "expected_version": 1},
                        headers=ADMIN)
    assert r.status_code == 200 and r.json()["version"] == 2
    r = await api.patch(f"/catalog/items/{item['id']}", json={"name": "x", "expected_version": 1},
                        headers=ADMIN)
    assert r.status_code == 409
    # Etkin olmayan öğe yalnızca admin'e görünür
    assert (await api.get(f"/catalog/items/{item['id']}", headers=requester)).status_code == 404
    r = await api.get("/catalog/items", params={"include_inactive": True}, headers=ADMIN)
    assert len(r.json()) == 11
    r = await api.post("/catalog/seed", headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"created": []}
    assert (await api.post("/catalog/seed", headers=AGENT)).status_code == 403


async def test_dev_lifespan_seeds_catalog_in_fake_mode(monkeypatch, tmp_path):
    from serviszeka import main
    from serviszeka.config import Settings
    from serviszeka.tools.connectors import connectors

    saved = connectors.snapshot()
    db.init_engine(f"sqlite+aiosqlite:///{tmp_path / 'dev.db'}")
    monkeypatch.setattr(main, "get_settings", lambda: Settings(
        environment="development", auth_mode="dev", connector_provider="fake",
    ))
    try:
        async with main.lifespan(main.app):
            assert connectors.available("identity") and connectors.available("messaging")
            async with db.get_sessionmaker()() as s:
                assert len(await catalog.list_items(s)) == 10
    finally:
        connectors.restore(saved)
        await db.dispose_engine()

"""Kapı değerlendirmesi için sağlayıcı biçimli sahte yığınlar (`connectors.fake_stacks`).

`memory`: bugünkü sahte dizin (değişmez). `entra`: Graph semantiği (bulutta kilit açma ve paylaşılan
posta kutusu izni desteklenmez). `keycloak`: Keycloak + Slack semantiği (kilit açma desteklenir,
ikincil e-posta yok, posta/lisans sağlayıcısı yok, bildirim gerçek `SlackNotifier` + sahte Slack API'si
`httpx.MockTransport` üzerinden). Ağ yok.
"""

from __future__ import annotations

import pytest

from serviszeka.connectors.base import (
    IDENTITY,
    LICENSE,
    MAIL,
    MESSAGING,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorNotSupportedError,
    IdentityProvider,
    Notifier,
)
from serviszeka.connectors.fake_stacks import (
    STACKS,
    EntraShapedDirectory,
    FakeSlackWorkspace,
    KeycloakShapedDirectory,
    build_stack,
    install_stack,
)
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.slack import SlackNotifier
from serviszeka.tools.connectors import ConnectorRegistry


def _demo() -> InMemoryDirectory:
    d = InMemoryDirectory.demo()
    d.locked.add("u-ayse")
    return d


def test_stack_names():
    assert STACKS == ("memory", "entra", "keycloak")
    with pytest.raises(ValueError):
        build_stack("okta", _demo())


def test_memory_stack_is_the_plain_fake():
    d = _demo()
    items = build_stack("memory", d)
    assert set(items) == {IDENTITY, MAIL, LICENSE, MESSAGING}
    assert all(v is d for v in items.values())


async def test_entra_stack_refuses_what_graph_refuses():
    items = build_stack("entra", _demo())
    assert set(items) == {IDENTITY, MAIL, LICENSE, MESSAGING}
    ident = items[IDENTITY]
    assert isinstance(ident, EntraShapedDirectory) and isinstance(ident, IdentityProvider)
    with pytest.raises(ConnectorNotSupportedError):
        await ident.unlock_account("ayse")
    with pytest.raises(ConnectorNotSupportedError):
        await ident.unlock_account("ayse", dry_run=True)
    with pytest.raises(ConnectorNotSupportedError):
        await items[MAIL].grant_shared_access("muhasebe", "ayse", "full_access")
    # Desteklenenler sahte dizinle aynı (ikincil adrese hassas bildirim dahil)
    assert (await items[MESSAGING].send_to_user("ayse", "k", "m", sensitive=True))["channel"] == (
        "secondary_mail"
    )
    assert ident.writes == [("send_to_user", {"user_id": "u-ayse", "sensitive": True})]


async def test_keycloak_stack_semantics():
    d = _demo()
    items = build_stack("keycloak", d)
    # Keycloak yalnızca kimlik; bildirim Slack; posta/lisans sağlayıcısı yok
    assert set(items) == {IDENTITY, MESSAGING}
    ident, slack = items[IDENTITY], items[MESSAGING]
    assert isinstance(ident, KeycloakShapedDirectory) and isinstance(ident, IdentityProvider)
    assert isinstance(slack, SlackNotifier) and isinstance(slack, Notifier)
    assert ident.name == "keycloak-fake"
    # Hassas bildirim SMTP karşılığına (yalnızca ikincil adres) devredilir
    assert slack.identity is ident and slack.sensitive_delegate is ident
    # Kaynak dizine dokunulmaz (kopya); ikincil adres öznitelikten gelir
    assert ident.users["u-ayse"] is not d.users["u-ayse"]
    assert ident.users["u-ayse"].other_mails == d.users["u-ayse"].other_mails
    # Keycloak'ta kilit açma desteklenir (brute-force kaydı silinir)
    assert (await ident.unlock_account("ayse", dry_run=True))["was_locked"] is True
    assert "u-ayse" in ident.locked
    assert (await ident.unlock_account("ayse"))["was_locked"] is True
    assert "u-ayse" not in ident.locked
    # İkincil e-posta özniteliği olmayan kullanıcı
    user = ident.add_user("yeni", "Yeni")
    assert user.other_mails == []


async def test_keycloak_stack_slack_dm_and_sensitive_refusal():
    workspace = FakeSlackWorkspace()
    items = build_stack("keycloak", _demo(), slack_workspace=workspace)
    slack = items[MESSAGING]
    assert workspace.directory is items[IDENTITY]
    result = await slack.send_to_user("u-ali", "Talebiniz", "VPN grubuna eklendiniz.")
    assert result["channel"] == "slack_dm" and result["slack_user_id"].startswith("U")
    assert [p["channel"] for p in workspace.posts] == [f"D{result['slack_user_id'][1:]}"]
    assert "VPN grubuna eklendiniz." in workspace.posts[0]["text"]
    # dry_run DM açmaz
    await slack.send_to_user("u-ali", "x", "y", dry_run=True)
    assert len(workspace.posts) == 1
    # Geçici parola Slack'e gitmez: ikincil e-posta özniteliğindeki adrese (SMTP) gider
    sent = await slack.send_to_user("u-ali", "Geçici parola", "gizli", sensitive=True)
    assert sent["channel"] == "secondary_mail" and len(workspace.posts) == 1
    ident = items[IDENTITY]
    assert [(m.target, m.sensitive) for m in ident.messages] == [("ali@kisisel.ornek", True)]
    # İkincil adresi olmayan kullanıcı -> hata (asla Slack/birincil posta değil)
    ident.add_user("adsiz", "Adsız")
    with pytest.raises(ConnectorError, match="ikincil"):
        await slack.send_to_user("adsiz", "Geçici parola", "gizli", sensitive=True)
    assert len(workspace.posts) == 1
    # Slack'te olmayan kullanıcı (dizinde yok)
    with pytest.raises(ConnectorNotFoundError):
        await slack.send_to_user("kimse@ornek.com.tr", "x", "y")
    # Kanal: yalnızca yapılandırılan "default"
    await slack.post_to_channel("default", "duyuru")
    with pytest.raises(ConnectorNotFoundError):
        await slack.post_to_channel("servis-masasi", "duyuru")


def test_install_stack_unregisters_missing_kinds():
    registry = ConnectorRegistry()
    install_stack(registry, build_stack("memory", _demo()))
    assert all(registry.available(k) for k in (IDENTITY, MAIL, LICENSE, MESSAGING))
    installed = install_stack(registry, build_stack("keycloak", _demo()))
    assert installed == [IDENTITY, MESSAGING]
    assert not registry.available(MAIL) and not registry.available(LICENSE)


# --- kapı değerlendirmesi yığın seçimiyle -------------------------------------------------------------


@pytest.fixture(scope="module")
def records():
    from serviszeka import automation_eval as ae

    return ae.load_dataset()


def _pick(records, item, n, **profile):
    out = [
        r for r in records
        if r.item == item and r.expected_touchless
        and all(r.requester.get(k) == v for k, v in profile.items())
    ]
    assert len(out) >= n
    return out[:n]


async def test_automation_eval_runs_on_keycloak_stack(records):
    from serviszeka import automation_eval as ae
    from serviszeka.tools.connectors import connectors

    before = connectors.snapshot()
    sample = [
        *_pick(records, "account_unlock", 3),
        *_pick(records, "vpn_access", 2),
        *_pick(records, "password_reset", 2),
        *_pick(records, "m365_license", 2),
    ]
    report = await ae.run(sample, stack="keycloak")
    assert connectors.snapshot() == before  # genel kayıt geri yüklenir
    assert report.settings["stack"] == "keycloak"
    by_id = {o["id"]: o for o in report.outcomes}
    for rec in sample:
        o = by_id[rec.id]
        if rec.item in ("account_unlock", "vpn_access", "password_reset"):
            # kilit açma Keycloak'ta çalışır, DM Slack'ten; geçici parola ikincil e-posta
            # özniteliğindeki adrese SMTP ile gider
            assert o["touchless"], o
        else:
            # Lisans sağlayıcısı yok
            assert o["state"] == "failed" and not o["touchless"], o
    assert not report.false_touchless and not report.violations and report.llm_calls == 0


async def test_automation_eval_entra_stack_unlock_fails(records):
    from serviszeka import automation_eval as ae

    sample = [*_pick(records, "account_unlock", 2), *_pick(records, "vpn_access", 1)]
    report = await ae.run(sample, stack="entra")
    states = {o["item"]: o["state"] for o in report.outcomes}
    assert states == {"account_unlock": "failed", "vpn_access": "fulfilled"}
    assert report.settings["stack"] == "entra"


async def test_automation_eval_default_stack_unchanged(records):
    from serviszeka import automation_eval as ae

    sample = _pick(records, "account_unlock", 2)
    report = await ae.run(sample)
    assert report.settings["stack"] == "memory"
    assert all(o["touchless"] for o in report.outcomes)

"""Sağlayıcı seçimi (`SZ_IDENTITY_PROVIDER`, `SZ_NOTIFIER_PROVIDER`) ve açılış denetimi.

`SZ_CONNECTOR_PROVIDER` (eski tek ayar) davranışı değişmez; yeni ayarlar kimlik ve bildirim
sağlayıcısını ayrı ayrı seçer. Ağ yok: yalnızca nesne kurulumu denetlenir.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from serviszeka.config import Settings
from serviszeka.connectors.base import IDENTITY, LICENSE, MAIL, MESSAGING
from serviszeka.connectors.graph import GraphIdentityProvider, TeamsGraphNotifier
from serviszeka.connectors.keycloak import KeycloakIdentityProvider
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.setup import (
    ConnectorConfigError,
    build,
    check_startup,
    resolve_providers,
)
from serviszeka.connectors.slack import SlackNotifier

GRAPH = {"graph_tenant_id": "t", "graph_client_id": "c", "graph_client_secret": "graph-gizli"}
KEYCLOAK = {
    "keycloak_server_url": "https://sso.ornek.com.tr",
    "keycloak_realm": "ornek",
    "keycloak_client_id": "serviszeka",
    "keycloak_client_secret": "kc-gizli",
}
SLACK = {"slack_bot_token": "xoxb-gizli-belirtec", "slack_default_channel": "C0DESK"}
SECRETS = ("graph-gizli", "kc-gizli", "xoxb-gizli-belirtec")


def _s(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def _config_error(**kw) -> str:
    with pytest.raises(ConnectorConfigError) as exc:
        check_startup(_s(**kw))
    msg = str(exc.value)
    assert not any(secret in msg for secret in SECRETS)
    return msg


# --- eski davranış (SZ_CONNECTOR_PROVIDER) değişmez -------------------------------------------------


def test_defaults_are_none_and_legacy_build_unchanged():
    s = _s()
    assert s.identity_provider is None and s.notifier_provider is None
    assert build(s) == {}
    sel = resolve_providers(s)
    assert (sel.identity, sel.notifier) == ("none", "auto")


def test_legacy_fake_mode_unchanged():
    items = build(_s(environment="development", connector_provider="fake"))
    assert set(items) == {IDENTITY, MAIL, LICENSE, MESSAGING}
    assert isinstance(items[IDENTITY], InMemoryDirectory)
    assert items[MESSAGING] is items[IDENTITY]
    assert resolve_providers(_s(environment="development", connector_provider="fake")).identity == (
        "memory"
    )
    msg = _config_error(connector_provider="fake", environment="production")
    assert "SZ_CONNECTOR_PROVIDER=fake" in msg


def test_legacy_graph_and_keycloak_with_slack_overlay_unchanged():
    items = build(_s(connector_provider="graph", **GRAPH, **SLACK))
    assert isinstance(items[MESSAGING], SlackNotifier)
    assert isinstance(items[MESSAGING].sensitive_delegate, TeamsGraphNotifier)
    items = build(_s(connector_provider="keycloak", **KEYCLOAK))
    assert set(items) == {IDENTITY}


# --- SZ_IDENTITY_PROVIDER ------------------------------------------------------------------------------


def test_identity_memory_only_in_development():
    msg = _config_error(identity_provider="memory", environment="production")
    assert "SZ_IDENTITY_PROVIDER=memory" in msg and "SZ_ENVIRONMENT=development" in msg
    items = build(_s(identity_provider="memory", environment="development"))
    assert set(items) == {IDENTITY, MAIL, LICENSE, MESSAGING}
    assert isinstance(items[IDENTITY], InMemoryDirectory)


def test_identity_graph_requires_settings():
    msg = _config_error(identity_provider="graph", graph_tenant_id="t")
    assert "SZ_IDENTITY_PROVIDER=graph" in msg
    assert "SZ_GRAPH_CLIENT_ID" in msg and "SZ_GRAPH_CLIENT_SECRET" in msg
    assert "SZ_GRAPH_TENANT_ID" not in msg
    items = build(_s(identity_provider="graph", **GRAPH))
    assert set(items) == {IDENTITY, MAIL, LICENSE, MESSAGING}
    assert isinstance(items[IDENTITY], GraphIdentityProvider)


def test_identity_keycloak_requires_settings_and_rejects_blank_secret():
    msg = _config_error(identity_provider="keycloak", **{**KEYCLOAK, "keycloak_realm": None})
    assert "SZ_IDENTITY_PROVIDER=keycloak" in msg and "SZ_KEYCLOAK_REALM" in msg
    # `.env.example`'daki boş `SZ_KEYCLOAK_CLIENT_SECRET=` SecretStr("") olur: eksik sayılmalı
    msg = _config_error(
        identity_provider="keycloak", **{**KEYCLOAK, "keycloak_client_secret": SecretStr("  ")}
    )
    assert "SZ_KEYCLOAK_CLIENT_SECRET" in msg
    msg = _config_error(connector_provider="keycloak", **{**KEYCLOAK, "keycloak_client_secret": ""})
    assert "SZ_KEYCLOAK_CLIENT_SECRET" in msg
    items = build(_s(identity_provider="keycloak", **KEYCLOAK))
    assert set(items) == {IDENTITY}
    assert isinstance(items[IDENTITY], KeycloakIdentityProvider)


def test_identity_provider_conflicting_with_connector_provider_is_rejected():
    msg = _config_error(connector_provider="graph", identity_provider="keycloak", **GRAPH, **KEYCLOAK)
    assert "SZ_CONNECTOR_PROVIDER=graph" in msg and "SZ_IDENTITY_PROVIDER=keycloak" in msg
    # Aynı seçim iki ayarda da yazılmışsa çelişki yok
    check_startup(_s(connector_provider="keycloak", identity_provider="keycloak", **KEYCLOAK))
    check_startup(
        _s(connector_provider="fake", identity_provider="memory", environment="development")
    )


# --- SZ_NOTIFIER_PROVIDER ------------------------------------------------------------------------------


def test_notifier_teams_requires_graph_identity_and_sender_mailbox():
    msg = _config_error(identity_provider="keycloak", notifier_provider="teams", **KEYCLOAK)
    assert "SZ_NOTIFIER_PROVIDER=teams" in msg and "SZ_IDENTITY_PROVIDER=graph" in msg
    msg = _config_error(notifier_provider="teams")
    assert "SZ_IDENTITY_PROVIDER=graph" in msg
    msg = _config_error(identity_provider="graph", notifier_provider="teams", **GRAPH)
    assert "SZ_GRAPH_SENDER_MAILBOX" in msg
    items = build(
        _s(identity_provider="graph", notifier_provider="teams", graph_sender_mailbox="bt@o", **GRAPH,
           **SLACK)
    )
    # Açık seçim Slack katmanını devre dışı bırakır (eski kipteki gibi Slack'e geçmez)
    assert isinstance(items[MESSAGING], TeamsGraphNotifier)


def test_notifier_slack_requires_token_or_webhook():
    msg = _config_error(notifier_provider="slack")
    assert "SZ_SLACK_BOT_TOKEN" in msg and "SZ_SLACK_WEBHOOK_URL" in msg
    msg = _config_error(notifier_provider="slack", slack_bot_token=SecretStr(""))
    assert "SZ_SLACK_BOT_TOKEN" in msg
    items = build(_s(notifier_provider="slack", slack_webhook_url="https://hooks.slack.com/x"))
    assert set(items) == {MESSAGING} and isinstance(items[MESSAGING], SlackNotifier)


def test_notifier_slack_with_each_identity_stack():
    items = build(_s(identity_provider="keycloak", notifier_provider="slack", **KEYCLOAK, **SLACK))
    assert set(items) == {IDENTITY, MESSAGING}
    slack = items[MESSAGING]
    assert isinstance(slack, SlackNotifier)
    assert slack.identity is items[IDENTITY] and slack.sensitive_delegate is None

    items = build(_s(identity_provider="graph", notifier_provider="slack", **GRAPH, **SLACK))
    assert set(items) == {IDENTITY, MAIL, LICENSE, MESSAGING}
    assert isinstance(items[MESSAGING].sensitive_delegate, TeamsGraphNotifier)

    items = build(
        _s(identity_provider="memory", notifier_provider="slack", environment="development", **SLACK)
    )
    assert items[MESSAGING].sensitive_delegate is items[IDENTITY]


def test_notifier_log_registers_no_messaging_even_with_slack_settings():
    items = build(
        _s(identity_provider="memory", notifier_provider="log", environment="development", **SLACK)
    )
    assert set(items) == {IDENTITY, MAIL, LICENSE}
    items = build(_s(identity_provider="graph", notifier_provider="log", **GRAPH))
    assert set(items) == {IDENTITY, MAIL, LICENSE}


def test_settings_parse_from_env(monkeypatch):
    monkeypatch.setenv("SZ_IDENTITY_PROVIDER", "keycloak")
    monkeypatch.setenv("SZ_NOTIFIER_PROVIDER", "slack")
    s = Settings(_env_file=None)
    assert (s.identity_provider, s.notifier_provider) == ("keycloak", "slack")
    # `.env.example`'dan kopyalanan boş değer "seçilmedi" demektir
    monkeypatch.setenv("SZ_IDENTITY_PROVIDER", "")
    monkeypatch.setenv("SZ_NOTIFIER_PROVIDER", " ")
    s = Settings(_env_file=None)
    assert s.identity_provider is None and s.notifier_provider is None


def test_oncall_follows_explicit_notifier_choice():
    from serviszeka.connectors.slack import SlackOncallNotifier
    from serviszeka.notifications import LogNotifier, WebhookNotifier, get_notifier

    slack = {**SLACK, "slack_oncall_channel": "C0ONCALL"}
    assert isinstance(get_notifier(_s(**slack)), SlackOncallNotifier)  # eski kip
    assert isinstance(get_notifier(_s(notifier_provider="slack", **slack)), SlackOncallNotifier)
    assert isinstance(get_notifier(_s(notifier_provider="log", **slack)), LogNotifier)
    assert isinstance(
        get_notifier(_s(notifier_provider="log", oncall_webhook_url="https://o", **slack)),
        WebhookNotifier,
    )


async def test_startup_rejects_invalid_selection(monkeypatch):
    from serviszeka import main

    monkeypatch.setattr(main, "get_settings", lambda: _s(
        identity_provider="keycloak", environment="production", auth_mode="oidc",
        auth_issuer="https://sso", auth_audience="api", auto_create_schema=False,
    ))
    with pytest.raises(ConnectorConfigError, match="SZ_KEYCLOAK_SERVER_URL"):
        async with main.lifespan(main.app):
            pass

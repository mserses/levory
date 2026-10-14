"""SMTP e-posta bildiricisi (Keycloak yığını): sahte gönderici ile (gerçek ağ yok)."""

from __future__ import annotations

import logging
import smtplib

import httpx
import pytest
from pydantic import SecretStr

from serviszeka.config import Settings
from serviszeka.connectors import (
    IDENTITY,
    MESSAGING,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorNotSupportedError,
    Notifier,
)
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.setup import build, check_startup
from serviszeka.connectors.smtp import SmtpNotifier, build_smtp

KEYCLOAK = {
    "identity_provider": "keycloak",
    "keycloak_server_url": "https://sso.ornek.com",
    "keycloak_realm": "ornek",
    "keycloak_client_id": "serviszeka",
    "keycloak_client_secret": "kc-gizli",
}
SMTP = {"smtp_host": "smtp.ornek.com", "smtp_sender": "servismasasi@ornek.com"}


def _directory() -> InMemoryDirectory:
    d = InMemoryDirectory()
    d.add_user("ali", "Ali Demir", other_mails=["ali@kisisel.ornek"])
    d.add_user("veli", "Veli Kaya")
    return d


def _notifier(sent: list, **kw) -> SmtpNotifier:
    return SmtpNotifier(
        _directory(), host="smtp.ornek.com", sender="sm@ornek.com", send=sent.append, **kw
    )


async def test_sensitive_goes_only_to_secondary_address():
    sent: list = []
    n = _notifier(sent)
    assert isinstance(n, Notifier)
    r = await n.send_to_user(
        "ali", "Geçici parolanız", "Parola: X1", sensitive=True, idempotency_key="k1"
    )
    assert r == {"dry_run": False, "user_id": "u-ali", "channel": "secondary_mail"}
    assert len(sent) == 1
    msg = sent[0]
    assert msg["To"] == "ali@kisisel.ornek" and msg["From"] == "sm@ornek.com"
    assert msg["X-ServisZeka-Idempotency-Key"] == "k1"
    # İkincil adres yoksa birincil posta kutusuna asla gitmez
    with pytest.raises(ConnectorError, match="ikincil"):
        await n.send_to_user("veli", "Geçici parolanız", "Parola: X2", sensitive=True)
    assert len(sent) == 1


async def test_non_sensitive_to_primary_and_dry_run_and_missing_user():
    sent: list = []
    n = _notifier(sent)
    r = await n.send_to_user("veli", "Talebiniz", "Tamamlandı")
    assert r["channel"] == "mail" and sent[-1]["To"] == "veli@ornek.com.tr"
    assert (await n.send_to_user("ali", "x", "y", sensitive=True, dry_run=True))["dry_run"] is True
    assert len(sent) == 1
    with pytest.raises(ConnectorNotFoundError):
        await n.send_to_user("kimse", "x", "y")
    with pytest.raises(ConnectorNotSupportedError):
        await n.post_to_channel("default", "duyuru")


async def test_send_error_hides_server_reply_and_secret(caplog):
    def boom(message):
        raise smtplib.SMTPAuthenticationError(535, b"kullanici sm parola smtp-gizli reddedildi")

    n = SmtpNotifier(
        _directory(), host="smtp.ornek.com", sender="sm@ornek.com", password="smtp-gizli", send=boom
    )
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ConnectorError) as exc:
        await n.send_to_user("ali", "Geçici parolanız", "Parola: GIZLI-PAROLA", sensitive=True)
    assert "SMTPAuthenticationError" in str(exc.value)
    for text in (str(exc.value), repr(n), caplog.text):
        assert "smtp-gizli" not in text and "GIZLI-PAROLA" not in text
    assert exc.value.__cause__ is None


def test_build_smtp_requires_host_and_sender():
    assert build_smtp(Settings(), None) is None
    assert build_smtp(Settings(smtp_host="smtp.ornek.com"), None) is None
    n = build_smtp(
        Settings(
            **SMTP,
            smtp_port=2525,
            smtp_username="u",
            smtp_password=SecretStr("p"),
            smtp_starttls=False,
        ),
        None,
    )
    assert (n.host, n.port, n.sender, n.starttls) == (
        "smtp.ornek.com",
        2525,
        "servismasasi@ornek.com",
        False,
    )


def _kc_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(404))


def test_keycloak_with_smtp_registers_messaging():
    items = build(Settings(**KEYCLOAK, **SMTP), transport=_kc_transport())
    assert set(items) == {IDENTITY, MESSAGING}
    assert isinstance(items[MESSAGING], SmtpNotifier)
    assert items[MESSAGING].identity is items[IDENTITY]
    # SMTP yoksa yalnızca kimlik (eski davranış)
    assert set(build(Settings(**KEYCLOAK), transport=_kc_transport())) == {IDENTITY}


def test_keycloak_slack_delegates_sensitive_to_smtp():
    from serviszeka.connectors.slack import SlackNotifier

    items = build(
        Settings(**KEYCLOAK, **SMTP, notifier_provider="slack", slack_bot_token="xoxb-test"),
        transport=_kc_transport(),
    )
    assert isinstance(items[MESSAGING], SlackNotifier)
    assert isinstance(items[MESSAGING].sensitive_delegate, SmtpNotifier)
    legacy = build(
        Settings(
            **{**KEYCLOAK, "identity_provider": None},
            connector_provider="keycloak",
            **SMTP,
            slack_bot_token="xoxb-test",
        ),
        transport=_kc_transport(),
    )
    assert isinstance(legacy[MESSAGING].sensitive_delegate, SmtpNotifier)


def test_startup_warning_only_without_smtp(caplog):
    caplog.set_level(logging.WARNING)
    check_startup(Settings(**KEYCLOAK))
    assert "hassas bildirim" in caplog.text
    caplog.clear()
    check_startup(Settings(**KEYCLOAK, **SMTP))
    assert "hassas bildirim" not in caplog.text

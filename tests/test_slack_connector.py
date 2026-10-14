"""Slack bağlayıcısı (Web API + gelen webhook): httpx.MockTransport ile (gerçek ağ yok)."""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from serviszeka.config import Settings
from serviszeka.connectors import (
    MESSAGING,
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    Notifier,
)
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.slack import (
    SlackClient,
    SlackNotifier,
    SlackOncallNotifier,
    SlackWebhook,
    build_blocks,
    build_slack,
    escape,
)

API = "https://slack.com/api"
TOKEN = "xoxb-gizli-belirtec-123"
HOOK = "https://hooks.slack.com/services/T000/B000/gizli"


class Recorder:
    """Slack API sahtesi: yöntem adı -> yanıt (ya da yanıt listesi)."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = request.url.path.rsplit("/", 1)[-1] if request.url.host == "slack.com" else "hook"
        route = self.routes.get(key)
        if isinstance(route, list):
            route = route.pop(0)
        if route is None:
            return httpx.Response(404, text="no route")
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)

    def body(self, i: int) -> dict:
        return json.loads(self.calls[i].content)

    def methods(self) -> list[str]:
        return [c.url.path.rsplit("/", 1)[-1] for c in self.calls]


def _client(rec: Recorder, sleeps: list[float] | None = None, **kw) -> SlackClient:
    async def fake_sleep(s: float) -> None:
        (sleeps if sleeps is not None else []).append(s)

    return SlackClient(TOKEN, transport=httpx.MockTransport(rec), sleep=fake_sleep, **kw)


def _notifier(rec: Recorder, **kw) -> SlackNotifier:
    return SlackNotifier(
        _client(rec),
        channels={"servis-masasi": "C0DESK", "nobet": "C0ONCALL"},
        **kw,
    )


OK_POST = {"ok": True, "channel": "C0DESK", "ts": "1700000000.000100"}


# --- kaçışlama ---------------------------------------------------------------------------------------


def test_escape_neutralises_control_characters_and_mentions():
    assert escape("a & b < c > d") == "a &amp; b &lt; c &gt; d"
    # Özel bahsetme/kullanıcı bahsetme/bağlantı sözdizimi kırılır
    assert "<" not in escape("<!channel> <!here|here> <@U123> <#C1|genel> <https://x|tıkla>")
    # Önceden kaçışlanmış metin çift kaçışlanır (kullanıcı metnine güvenilmez)
    assert escape("&lt;!channel&gt;") == "&amp;lt;!channel&amp;gt;"
    # Düz @channel/@here/@everyone sıfır genişlikli boşlukla bozulur
    for word in ("@channel", "@here", "@everyone"):
        out = escape(f"dikkat {word}!")
        assert word not in out and "\u200b" in out


def test_build_blocks_shape_and_escaping():
    blocks = build_blocks(
        "Başlık <!channel>",
        "Gövde <@U123> & *kalın*",
        fields={"Öncelik": "P1 <!here>", "Olay": "INC-1"},
        context=["ServisZeka <#C1>"],
    )
    types = [b["type"] for b in blocks]
    assert types == ["header", "section", "section", "context"]
    assert blocks[0]["text"]["type"] == "plain_text"
    assert blocks[1]["text"] == {"type": "mrkdwn", "text": "Gövde &lt;@U123&gt; &amp; *kalın*"}
    fields = blocks[2]["fields"]
    assert fields[0] == {"type": "mrkdwn", "text": "*Öncelik*\nP1 &lt;!here&gt;"}
    assert blocks[3]["elements"][0]["type"] == "mrkdwn"
    dumped = json.dumps(blocks, ensure_ascii=False)
    for bad in ("<!channel>", "<!here>", "<@U123>", "<#C1>"):
        assert bad not in dumped


def test_build_blocks_respects_block_kit_limits():
    blocks = build_blocks("x" * 500, "y" * 10_000, fields={f"k{i}": "v" for i in range(15)})
    assert len(blocks[0]["text"]["text"]) <= 150
    assert len(blocks[1]["text"]["text"]) <= 3000
    assert len(blocks[2]["fields"]) <= 10
    # Kesme varlığı (&amp;) ortadan bölmez
    b = build_blocks("t", "&" * 2000)
    text = b[1]["text"]["text"]
    assert len(text) <= 3000 and text.rstrip("…").endswith("&amp;")


# --- kanal mesajı ---------------------------------------------------------------------------------------


async def test_post_to_channel_payload_and_auth_header():
    rec = Recorder({"chat.postMessage": OK_POST})
    n = _notifier(rec)
    assert isinstance(n, Notifier)
    result = await n.post_to_channel("servis-masasi", "Kesinti <!channel> & <@U999>")
    assert result == {"dry_run": False, "channel": "servis-masasi", "ts": "1700000000.000100"}
    req = rec.calls[0]
    assert str(req.url) == f"{API}/chat.postMessage"
    assert req.method == "POST"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["Content-Type"].startswith("application/json")
    body = rec.body(0)
    assert body["channel"] == "C0DESK"
    assert body["text"] == "Kesinti &lt;!channel&gt; &amp; &lt;@U999&gt;"
    assert body["unfurl_links"] is False and body["unfurl_media"] is False
    assert body["blocks"][0]["type"] == "section"
    raw = req.content.decode()
    assert "<!channel>" not in raw and "<@U999>" not in raw


async def test_post_to_channel_only_configured_channels():
    rec = Recorder({"chat.postMessage": OK_POST})
    n = _notifier(rec)
    # Yapay zekâ rastgele kanal/kullanıcı kimliğine yazamaz
    for target in ("C0OTHER", "U123", "#genel", "bilinmeyen"):
        with pytest.raises(ConnectorNotFoundError):
            await n.post_to_channel(target, "x")
    assert rec.calls == []
    # Yapılandırılmış kanal kimliğiyle de olur
    await n.post_to_channel("C0ONCALL", "x")
    assert rec.body(0)["channel"] == "C0ONCALL"


async def test_post_to_channel_dry_run_makes_no_call():
    rec = Recorder({})
    result = await _notifier(rec).post_to_channel("servis-masasi", "x", dry_run=True)
    assert result == {"dry_run": True, "channel": "servis-masasi"}
    assert rec.calls == []


# --- Slack API semantiği -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "exc"),
    [
        ("invalid_auth", ConnectorAuthError),
        ("not_authed", ConnectorAuthError),
        ("token_revoked", ConnectorAuthError),
        ("missing_scope", ConnectorAuthError),
        ("channel_not_found", ConnectorNotFoundError),
        ("not_in_channel", ConnectorError),
        ("is_archived", ConnectorError),
        ("msg_too_long", ConnectorError),
    ],
)
async def test_http_200_ok_false_is_error(error, exc, caplog):
    rec = Recorder({"chat.postMessage": {"ok": False, "error": error, "needed": "chat:write"}})
    caplog.set_level(logging.DEBUG)
    with pytest.raises(exc) as info:
        await _notifier(rec).post_to_channel("servis-masasi", "x")
    assert error in str(info.value)
    assert TOKEN not in str(info.value)
    assert TOKEN not in caplog.text


async def test_ok_false_missing_scope_mentions_needed_scope():
    rec = Recorder({"chat.postMessage": {"ok": False, "error": "missing_scope", "needed": "chat:write"}})
    with pytest.raises(ConnectorAuthError, match="chat:write"):
        await _notifier(rec).post_to_channel("servis-masasi", "x")


async def test_429_retries_once_after_retry_after():
    sleeps: list[float] = []
    rec = Recorder(
        {
            "chat.postMessage": [
                httpx.Response(429, headers={"Retry-After": "3"}, json={"ok": False, "error": "ratelimited"}),
                OK_POST,
            ]
        }
    )
    n = SlackNotifier(_client(rec, sleeps), channels={"servis-masasi": "C0DESK"})
    result = await n.post_to_channel("servis-masasi", "x")
    assert result["ts"] == OK_POST["ts"]
    assert sleeps == [3.0]
    assert len(rec.calls) == 2


async def test_429_twice_raises_without_third_attempt():
    sleeps: list[float] = []
    r429 = httpx.Response(429, headers={"Retry-After": "1"})
    rec = Recorder({"chat.postMessage": [r429, httpx.Response(429, headers={"Retry-After": "1"})]})
    n = SlackNotifier(_client(rec, sleeps), channels={"servis-masasi": "C0DESK"})
    with pytest.raises(ConnectorError, match="429"):
        await n.post_to_channel("servis-masasi", "x")
    assert len(rec.calls) == 2 and sleeps == [1.0]


async def test_429_retry_after_above_cap_is_not_retried():
    sleeps: list[float] = []
    rec = Recorder({"chat.postMessage": [httpx.Response(429, headers={"Retry-After": "120"})]})
    n = SlackNotifier(_client(rec, sleeps, max_retry_wait=10), channels={"servis-masasi": "C0DESK"})
    with pytest.raises(ConnectorError, match="120"):
        await n.post_to_channel("servis-masasi", "x")
    assert len(rec.calls) == 1 and sleeps == []


async def test_http_5xx_and_transport_errors_are_connector_errors():
    rec = Recorder({"chat.postMessage": httpx.Response(503, text="upstream")})
    with pytest.raises(ConnectorError, match="503"):
        await _notifier(rec).post_to_channel("servis-masasi", "x")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bağlanamadı")

    n = SlackNotifier(
        SlackClient(TOKEN, transport=httpx.MockTransport(boom)), channels={"a": "C1"}
    )
    with pytest.raises(ConnectorError) as info:
        await n.post_to_channel("a", "x")
    assert TOKEN not in str(info.value)


def test_client_repr_and_settings_hide_token():
    c = SlackClient(TOKEN)
    assert TOKEN not in repr(c) and TOKEN not in str(vars(c).get("token", ""))
    s = Settings(slack_bot_token=TOKEN, slack_webhook_url=HOOK)
    assert TOKEN not in repr(s) and HOOK not in repr(s)
    with pytest.raises(ConnectorAuthError):
        SlackClient("")


# --- kullanıcıya DM -----------------------------------------------------------------------------------


async def test_send_to_user_by_email_opens_dm_and_posts():
    rec = Recorder(
        {
            "users.lookupByEmail": {"ok": True, "user": {"id": "U0ALI", "name": "ali"}},
            "conversations.open": {"ok": True, "channel": {"id": "D0ALI"}},
            "chat.postMessage": {"ok": True, "channel": "D0ALI", "ts": "1.2"},
        }
    )
    result = await _notifier(rec).send_to_user("ali@contoso.com", "Talep <!channel>", "Hazır <@U1>")
    assert rec.methods() == ["users.lookupByEmail", "conversations.open", "chat.postMessage"]
    lookup = rec.calls[0]
    assert lookup.method == "GET"
    assert parse_qs(lookup.url.query.decode())["email"] == ["ali@contoso.com"]
    assert rec.body(1) == {"users": "U0ALI"}
    post = rec.body(2)
    assert post["channel"] == "D0ALI"
    assert post["blocks"][0]["type"] == "header"
    assert "<!channel>" not in rec.calls[2].content.decode()
    assert "<@U1>" not in rec.calls[2].content.decode()
    assert result == {
        "dry_run": False,
        "user_id": "ali@contoso.com",
        "slack_user_id": "U0ALI",
        "channel": "slack_dm",
    }


async def test_send_to_user_with_slack_user_id_skips_lookup():
    rec = Recorder(
        {
            "conversations.open": {"ok": True, "channel": {"id": "D1"}},
            "chat.postMessage": {"ok": True, "channel": "D1", "ts": "1.3"},
        }
    )
    await _notifier(rec).send_to_user("U0ABC123", "Konu", "Metin")
    assert rec.methods() == ["conversations.open", "chat.postMessage"]


async def test_send_to_user_resolves_directory_id_via_identity_provider():
    directory = InMemoryDirectory.demo()
    some = next(iter(directory.users.values()))
    email = some.mail or some.user_principal_name
    rec = Recorder(
        {
            "users.lookupByEmail": {"ok": True, "user": {"id": "U0X"}},
            "conversations.open": {"ok": True, "channel": {"id": "D0X"}},
            "chat.postMessage": {"ok": True, "channel": "D0X", "ts": "1.4"},
        }
    )
    await _notifier(rec, identity=directory).send_to_user(some.id, "Konu", "Metin")
    assert parse_qs(rec.calls[0].url.query.decode())["email"] == [email]


async def test_send_to_user_unknown_email_is_not_found():
    rec = Recorder({"users.lookupByEmail": {"ok": False, "error": "users_not_found"}})
    with pytest.raises(ConnectorNotFoundError):
        await _notifier(rec).send_to_user("yok@contoso.com", "K", "M")
    rec2 = Recorder({})
    with pytest.raises(ConnectorNotFoundError):
        await _notifier(rec2).send_to_user("kisa-ad", "K", "M")  # kimlik sağlayıcı yok
    assert rec2.calls == []


async def test_send_to_user_dry_run_reads_but_does_not_open_or_post():
    rec = Recorder({"users.lookupByEmail": {"ok": True, "user": {"id": "U0ALI"}}})
    result = await _notifier(rec).send_to_user("ali@contoso.com", "K", "M", dry_run=True)
    assert rec.methods() == ["users.lookupByEmail"]
    assert result["dry_run"] is True and result["slack_user_id"] == "U0ALI"


async def test_sensitive_never_goes_to_slack():
    rec = Recorder({})
    with pytest.raises(ConnectorError):
        await _notifier(rec).send_to_user("ali@contoso.com", "Parola", "gizli", sensitive=True)
    assert rec.calls == []


async def test_sensitive_is_delegated_when_configured():
    directory = InMemoryDirectory.demo()
    user = next(u for u in directory.users.values() if u.other_mails)
    rec = Recorder({})
    n = _notifier(rec, identity=directory, sensitive_delegate=directory)
    result = await n.send_to_user(user.id, "Parola", "gizli", sensitive=True)
    assert result["channel"] == "secondary_mail"
    assert rec.calls == []
    assert directory.messages[-1].sensitive is True


# --- gelen webhook kipi ------------------------------------------------------------------------------


async def test_webhook_mode_posts_blocks_and_handles_text_errors():
    rec = Recorder({"hook": httpx.Response(200, text="ok")})
    hook = SlackWebhook(HOOK, transport=httpx.MockTransport(rec))
    n = SlackNotifier(None, webhook=hook, default_channel="servis-masasi")
    result = await n.post_to_channel("servis-masasi", "Merhaba <!everyone>")
    assert result == {"dry_run": False, "channel": "servis-masasi"}
    body = json.loads(rec.calls[0].content)
    assert "channel" not in body and body["text"] == "Merhaba &lt;!everyone&gt;"
    assert body["blocks"][0]["type"] == "section"
    # Webhook tek kanala bağlıdır
    with pytest.raises(ConnectorNotFoundError):
        await n.post_to_channel("baska", "x")
    # DM webhook ile yapılamaz
    with pytest.raises(ConnectorError):
        await n.send_to_user("ali@contoso.com", "K", "M")

    bad = Recorder({"hook": httpx.Response(404, text="no_service")})
    n2 = SlackNotifier(
        None, webhook=SlackWebhook(HOOK, transport=httpx.MockTransport(bad)), default_channel="a"
    )
    with pytest.raises(ConnectorError) as info:
        await n2.post_to_channel("a", "x")
    assert "no_service" in str(info.value) and HOOK not in str(info.value)


def test_notifier_requires_client_or_webhook():
    with pytest.raises(ConnectorError):
        SlackNotifier(None)


# --- nöbetçi uyarısı (notifications.Notifier) --------------------------------------------------------


async def test_oncall_notifier_posts_to_oncall_channel():
    rec = Recorder({"chat.postMessage": OK_POST})
    on = SlackOncallNotifier(_client(rec), channel="C0ONCALL")
    await on.notify(
        "oncall.page",
        {
            "message": "DB çöktü <!channel>",
            "severity": "critical",
            "incident_number": "INC0001",
            "requested_by": "ai:triage",
        },
    )
    body = rec.body(0)
    assert body["channel"] == "C0ONCALL"
    assert body["blocks"][0]["type"] == "header"
    raw = rec.calls[0].content.decode()
    assert "<!channel>" not in raw
    assert "INC0001" in raw and "critical" in raw


async def test_oncall_notifier_raises_on_failure():
    rec = Recorder({"chat.postMessage": {"ok": False, "error": "channel_not_found"}})
    with pytest.raises(ConnectorError):
        await SlackOncallNotifier(_client(rec), channel="C0").notify("e", {"message": "x"})


def test_get_notifier_uses_slack_when_configured():
    from serviszeka import notifications

    s = Settings(slack_bot_token=TOKEN, slack_oncall_channel="C0ONCALL")
    assert isinstance(notifications.get_notifier(s), SlackOncallNotifier)
    s2 = Settings(slack_webhook_url=HOOK)
    assert isinstance(notifications.get_notifier(s2), SlackOncallNotifier)
    # Genel nöbetçi webhook'u tanımlıysa davranış değişmez
    s3 = Settings(oncall_webhook_url="https://example.invalid/h", slack_bot_token=TOKEN,
                  slack_oncall_channel="C0")
    assert isinstance(notifications.get_notifier(s3), notifications.WebhookNotifier)
    assert isinstance(notifications.get_notifier(Settings()), notifications.LogNotifier)


# --- kurulum ---------------------------------------------------------------------------------------------


def test_build_slack_from_settings():
    assert build_slack(Settings()) is None
    s = Settings(
        slack_bot_token=SecretStr(TOKEN),
        slack_default_channel="C0DESK",
        slack_oncall_channel="C0ONCALL",
    )
    n = build_slack(s)
    assert isinstance(n, SlackNotifier)
    assert n.channels == {"default": "C0DESK", "oncall": "C0ONCALL"}


def test_setup_registers_slack_as_messaging():
    from serviszeka.connectors.setup import build

    items = build(Settings(slack_bot_token=TOKEN, slack_default_channel="C0DESK"))
    assert isinstance(items[MESSAGING], SlackNotifier)
    # Sahte dizin kipinde Slack MESSAGING'in yerini alır, hassas bildirim dizine devredilir
    items = build(
        Settings(
            environment="development",
            connector_provider="fake",
            slack_bot_token=TOKEN,
            slack_default_channel="C0DESK",
        )
    )
    n = items[MESSAGING]
    assert isinstance(n, SlackNotifier)
    assert n.identity is items["identity"] and n.sensitive_delegate is not None
    assert not isinstance(build(Settings()).get(MESSAGING), SlackNotifier)

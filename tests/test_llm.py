"""LLM istemci testleri (ağ çağrısı yok)."""

from types import SimpleNamespace

import pytest

from serviszeka.config import Settings
from serviszeka.llm import (
    AnthropicLLM,
    FakeLLM,
    LLMClient,
    LLMError,
    extract_json,
    get_llm,
)

PII_TEXT = "Kullanıcı 10000000146, ali@ornek.com, 0532 123 45 67 VPN'e bağlanamıyor"


class FakeMessages:
    def __init__(self, text=None, blocks=None, exc=None):
        self.text = text
        self.blocks = blocks
        self.exc = exc
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        blocks = self.blocks or [SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(content=blocks, stop_reason="end_turn")


def make_client(**kw):
    messages = FakeMessages(**kw)
    return SimpleNamespace(messages=messages), messages


def settings(**kw) -> Settings:
    base = {"anthropic_api_key": None, "llm_fast_model": "fast-model"}
    base.update(kw)
    return Settings(**base)


# --- FakeLLM ---


async def test_fakellm_returns_responses_in_order_and_repeats_last():
    llm = FakeLLM([{"a": 1}, {"b": 2}])
    assert await llm.complete_json(system="s", user="u") == {"a": 1}
    assert await llm.complete_json(system="s", user="u") == {"b": 2}
    assert await llm.complete_json(system="s", user="u") == {"b": 2}
    assert len(llm.calls) == 3


async def test_fakellm_returns_copies():
    llm = FakeLLM([{"x": [1]}])
    r = await llm.complete_json(system="s", user="u")
    r["x"].append(2)
    assert await llm.complete_json(system="s", user="u") == {"x": [1]}


async def test_fakellm_masks_pii_and_records():
    llm = FakeLLM([{"ok": True}])
    await llm.complete_json(system="sistem", user=PII_TEXT, model="m1")
    call = llm.calls[0]
    assert "10000000146" not in call["user"]
    assert "ali@ornek.com" not in call["user"]
    assert "[TCKN]" in call["user"] and "[EPOSTA]" in call["user"]
    assert "[TELEFON]" in call["user"]
    assert call["model"] == "m1"


async def test_fakellm_callable_receives_masked_text():
    seen = {}

    def fn(system, user):
        seen["user"] = user
        return {"echo": user}

    llm = FakeLLM(fn)
    res = await llm.complete_json(system="s", user="mail ali@ornek.com")
    assert res == {"echo": "mail [EPOSTA]"}
    assert seen["user"] == "mail [EPOSTA]"


async def test_fakellm_async_callable():
    async def fn(system, user):
        return {"n": len(user)}

    assert await FakeLLM(fn).complete_json(system="", user="abc") == {"n": 3}


def test_fakellm_satisfies_protocol():
    assert isinstance(FakeLLM([{}]), LLMClient)


# --- extract_json ---


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": {"b": [1, 2]}}\n```', {"a": {"b": [1, 2]}}),
        ('İşte sonuç: {"kategori": "ağ", "güven": 0.9} umarım yardımcı olur', 
         {"kategori": "ağ", "güven": 0.9}),
        ('[1,2] sonra {"a": "}"}', {"a": "}"}),
        ('{bozuk} ama {"a": 2}', {"a": 2}),
    ],
)
def test_extract_json(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "json yok", "[1, 2, 3]", '{"a": 1'])
def test_extract_json_invalid(text):
    with pytest.raises(LLMError):
        extract_json(text)


# --- AnthropicLLM ---


async def test_anthropic_sends_masked_prompt_and_parses_json():
    client, messages = make_client(text='```json\n{"kategori": "ağ"}\n```')
    llm = AnthropicLLM(settings(), client=client)
    result = await llm.complete_json(system="Sınıflandır. Destek: destek@firma.com", user=PII_TEXT)
    assert result == {"kategori": "ağ"}
    call = messages.calls[0]
    assert call["model"] == "fast-model"
    assert call["max_tokens"] == 2048
    user_content = call["messages"][0]["content"]
    assert call["messages"][0]["role"] == "user"
    assert "10000000146" not in user_content and "[TCKN]" in user_content
    assert "ali@ornek.com" not in user_content
    assert "destek@firma.com" not in call["system"]
    assert "JSON" in call["system"]


async def test_anthropic_model_override_and_multiple_blocks():
    blocks = [
        SimpleNamespace(type="thinking", thinking="..."),
        SimpleNamespace(type="text", text='{"a":'),
        SimpleNamespace(type="text", text=' 1}'),
    ]
    client, messages = make_client(blocks=blocks)
    llm = AnthropicLLM(settings(), client=client)
    assert await llm.complete_json(system="s", user="u", model="big") == {"a": 1}
    assert messages.calls[0]["model"] == "big"


async def test_anthropic_invalid_json_raises():
    client, _ = make_client(text="Üzgünüm, bunu yapamam.")
    llm = AnthropicLLM(settings(), client=client)
    with pytest.raises(LLMError):
        await llm.complete_json(system="s", user="u")


async def test_anthropic_sdk_error_wrapped():
    client, _ = make_client(exc=RuntimeError("bağlantı koptu"))
    llm = AnthropicLLM(settings(), client=client)
    with pytest.raises(LLMError, match="bağlantı koptu"):
        await llm.complete_json(system="s", user="u")


def test_anthropic_without_key_and_client_raises():
    with pytest.raises(LLMError):
        AnthropicLLM(settings())


def test_get_llm_without_key_raises():
    with pytest.raises(LLMError, match="SZ_ANTHROPIC_API_KEY"):
        get_llm(settings())


def test_get_llm_with_key_returns_anthropic():
    llm = get_llm(settings(anthropic_api_key="test-key"))
    assert isinstance(llm, AnthropicLLM)
    assert isinstance(llm, LLMClient)

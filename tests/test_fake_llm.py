"""SZ_LLM_PROVIDER=fake: deterministik geliştirme LLM'i ve ortam koruması."""

import pytest
from _auth import AGENT
from httpx import ASGITransport, AsyncClient

from serviszeka import db
from serviszeka.config import Settings, get_settings
from serviszeka.llm import LLMError, get_llm
from serviszeka.llm.fake import FAKE_MODEL, DeterministicFakeLLM, check_provider, classify
from serviszeka.main import app, lifespan


def test_classify_is_deterministic_and_on_taxonomy():
    user = "Kayıt:\nBaşlık: VPN çöktü\nAçıklama: Tüm şirket bağlanamıyor\n\nBilgi bankası sonuçları (yalnızca bunlara atıf yap):\n[]"
    first, second = classify(user), classify(user)
    assert first == second
    assert first["category"] == "ag"
    assert first["service"] == "vpn"
    assert (first["impact"], first["urgency"]) == ("high", "high")
    assert first["kb_article_ids"] == []


def test_classify_cites_only_retrieved_kb():
    user = (
        'Kayıt:\nBaşlık: Yazıcı kağıt sıkıştırıyor\n\nBilgi bankası sonuçları:\n'
        '[{"id": "kb-1", "number": "KB0001", "title": "t", "snippet": "s"}]'
    )
    result = classify(user)
    assert result["kb_article_ids"] == ["kb-1"]
    assert (result["impact"], result["urgency"]) == ("low", "low")


@pytest.mark.parametrize("env", ["production", "staging", "test"])
def test_fake_refused_outside_development(env):
    settings = Settings(llm_provider="fake", environment=env, auth_mode="dev")
    with pytest.raises(LLMError):
        check_provider(settings)
    with pytest.raises(LLMError):
        get_llm(settings)


def test_get_llm_returns_fake_in_development():
    settings = Settings(llm_provider="fake", environment="development")
    assert isinstance(get_llm(settings), DeterministicFakeLLM)


async def test_startup_refuses_fake_outside_development(monkeypatch):
    monkeypatch.setenv("SZ_LLM_PROVIDER", "fake")
    monkeypatch.setenv("SZ_ENVIRONMENT", "production")
    monkeypatch.setenv("SZ_AUTH_MODE", "oidc")
    monkeypatch.setenv("SZ_AUTH_ISSUER", "https://sso.example.test")
    monkeypatch.setenv("SZ_AUTH_AUDIENCE", "serviszeka")
    get_settings.cache_clear()
    with pytest.raises(LLMError):
        async with lifespan(app):
            pass


async def test_triage_endpoint_with_fake_provider(monkeypatch):
    monkeypatch.setenv("SZ_LLM_PROVIDER", "fake")
    get_settings.cache_clear()
    db.init_engine("sqlite+aiosqlite:///:memory:")
    await db.create_all()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/incidents",
                json={"title": "VPN çöktü", "description": "Tüm şirket bağlanamıyor", "caller": "ayse"},
                headers=AGENT,
            )
            assert r.status_code == 201, r.text
            inc = r.json()
            r = await c.post(f"/incidents/{inc['id']}/triage", headers=AGENT)
            assert r.status_code == 200, r.text
            decision = r.json()
            assert decision["outcome"] == "needs_approval"
            assert decision["model"] == FAKE_MODEL
            assert decision["output"]["proposed_priority"] == "P1"
    finally:
        await db.dispose_engine()

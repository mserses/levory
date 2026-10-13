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


# --- değişiklik risk ajanı (Faz 2): fake sağlayıcı da şemaya uygun anlatı üretmeli -----------


def _risk_prompt(**factors) -> str:
    base = {
        "change_type": 15,
        "criticality": 35,
        "production": 10,
        "business_services": 5,
        "failure_history": 0,
        "recent_incidents": 0,
        "backout_plan": 10,
    }
    base.update(factors)
    import json

    det = {
        "score": sum(base.values()),
        "level": "high",
        "factors": base,
        "inputs": {"business_services": ["odeme"], "production": True},
        "notes": [],
    }
    return (
        "Değişiklik:\nNumara: CHG0000001\nBaşlık: Yama\n\n"
        f"Deterministik risk değerlendirmesi:\n{json.dumps(det, ensure_ascii=False)}"
    )


async def test_fake_change_risk_returns_valid_narrative():
    from serviszeka.agents import change_risk

    llm = DeterministicFakeLLM()
    user = _risk_prompt()
    first = await llm.complete_json(system=change_risk.SYSTEM_PROMPT, user=user)
    second = await llm.complete_json(system=change_risk.SYSTEM_PROMPT, user=user)
    assert first == second
    parsed = change_risk.RiskNarrative.model_validate(first)
    assert parsed.narrative and parsed.adjustment == 0 and parsed.confidence >= 0.75
    # Bileşenlere göre somut kontrol maddeleri: geri dönüş planı eksik, üretim, iş hizmeti
    joined = " ".join(parsed.checklist).lower()
    assert "geri dönüş" in joined and "bakım penceresi" in joined
    assert "kategori" not in first  # triage şeması değil


async def test_fake_change_risk_low_risk_has_short_checklist():
    from serviszeka.agents import change_risk

    llm = DeterministicFakeLLM()
    user = _risk_prompt(criticality=0, production=0, business_services=0, backout_plan=0)
    out = change_risk.RiskNarrative.model_validate(
        await llm.complete_json(system=change_risk.SYSTEM_PROMPT, user=user)
    )
    assert "geri dönüş" not in " ".join(out.checklist).lower()
    assert out.checklist  # en az bir genel madde


async def test_assess_endpoint_with_fake_provider(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from _auth import dev_headers

    monkeypatch.setenv("SZ_LLM_PROVIDER", "fake")
    get_settings.cache_clear()
    db.init_engine("sqlite+aiosqlite:///:memory:")
    await db.create_all()
    mgr = dev_headers("mudur", "change_manager")
    start = datetime.now(UTC) + timedelta(days=10)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/changes",
                json={
                    "title": "Yama",
                    "change_type": "normal",
                    "implementation_plan": "Yamayı uygula",
                    "backout_plan": "",
                    "planned_start": start.isoformat(),
                    "planned_end": (start + timedelta(hours=2)).isoformat(),
                },
                headers=mgr,
            )
            assert r.status_code == 201, r.text
            r = await c.post(f"/changes/{r.json()['id']}/assess", headers=mgr)
            assert r.status_code == 200, r.text
            decision = r.json()
            assert decision["model"] == FAKE_MODEL
            assert decision["outcome"] != "escalated", decision["output"]
            assert decision["output"]["llm"]["narrative"]
    finally:
        await db.dispose_engine()

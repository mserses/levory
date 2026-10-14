"""Faz 3 araç testleri için ortak yardımcılar (aktörler, ayarlar, örnek kayıtlar)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from serviszeka import knowledge
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import ChangeType, CIClass, CIEnvironment, RiskClass
from serviszeka.services import changes, cmdb
from serviszeka.services import incidents as incident_service
from serviszeka.services.change_schemas import ChangeCreate
from serviszeka.services.cmdb_schemas import CICreate
from serviszeka.services.schemas import IncidentCreate
from serviszeka.tools import ToolSpec, registry

AI = CurrentUser("ai:copilot", frozenset({"ai_agent"}))
AI2 = CurrentUser("ai:other", frozenset({"ai_agent"}))
AGENT = CurrentUser("ayse", frozenset({"agent"}))
APPROVER = CurrentUser("sef.ali", frozenset({"approver"}))
APPROVER2 = CurrentUser("sef.veli", frozenset({"approver"}))
ADMIN = CurrentUser("yonetici", frozenset({"admin"}))
CHANGE_MANAGER = CurrentUser("mudur", frozenset({"change_manager"}))
REQUESTER = CurrentUser("son.kullanici", frozenset({"requester"}))

# Yapay zekâ politikası izin verirse uygular: gölge mod kapalı, eşik 0.5
LIVE = Settings(ai_shadow_mode=False, ai_confidence_threshold=0.5)
SHADOW = Settings(ai_shadow_mode=True, ai_confidence_threshold=0.5)


@dataclass
class Fixtures:
    incident_id: str
    incident_number: str
    change_id: str
    article_id: str
    draft_article_id: str
    ci_id: str
    # Sorun yönetimi kapı örnekleri için (seed açmaz; kapı testi ayrıca kurar)
    problem_id: str = ""
    # Faz 4 izleme kapı örnekleri için (seed açmaz; kapı testi tests/_events ile kurar)
    event_group_id: str = ""
    # Olayın güncel sürümü: yapay zekâ / MCP olay yazması expected_version taşımak zorunda
    incident_version: int = 1


async def seed(session) -> Fixtures:
    """Her alandan birer kayıt: olay (P5), taslak değişiklik, yayında + taslak makale, CI."""
    ci = await cmdb.create_ci(
        session,
        CICreate(name="vpn-gw", ci_class=CIClass.NETWORK_DEVICE, environment=CIEnvironment.PROD),
        "mudur",
    )
    inc = await incident_service.create_incident(
        session,
        IncidentCreate(
            title="VPN bağlanmıyor",
            description="Kullanıcı ali@ornek.com evden bağlanamıyor, tel 0532 123 45 67",
            caller="ali",
        ),
        "ayse",
    )
    change = await changes.create_change(
        session,
        ChangeCreate(
            title="VPN yaması",
            change_type=ChangeType.NORMAL,
            implementation_plan="Yamayı uygula",
            backout_plan="Geri al",
        ),
        AGENT,
    )
    published = await knowledge.create_article(
        session, title="VPN sorun giderme", body="İstemciyi yeniden başlatın.", actor="ayse"
    )
    draft = await knowledge.create_article(
        session, title="Taslak", body="Gövde", state="draft", actor="ayse"
    )
    await session.commit()
    return Fixtures(
        inc.id, inc.number, change.id, published.id, draft.id, ci.id, incident_version=inc.version
    )


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=200, description="Not")


def make_test_tool(
    name: str,
    risk: RiskClass,
    calls: list[Any],
    *,
    risk_fn=None,
    connectors: tuple[str, ...] = (),
) -> ToolSpec:
    """Yalnızca testte kayda alınan, çağrıldığında `calls` listesine yazan araç."""

    async def handler(ctx, args):
        calls.append((ctx.actor.username, args))
        return {"ok": True, "actor": ctx.actor.username}

    return ToolSpec(
        name=name,
        description="Test aracı",
        risk_class=risk,
        input_model=EmptyArgs,
        handler=handler,
        roles=frozenset({"agent"}),
        risk_fn=risk_fn,
        connectors=connectors,
    )


class registered:
    """Bağlam yöneticisi: aracı (ve politikasını) geçici olarak kayda alır."""

    def __init__(self, monkeypatch, spec: ToolSpec, max_auto=None) -> None:
        from serviszeka import policy

        self.spec = spec
        domain, verb = spec.name.split(".", 1)
        monkeypatch.setitem(policy.POLICIES, (domain, verb), policy.ActionPolicy(max_auto, "test"))
        self._mp = monkeypatch

    def __enter__(self) -> ToolSpec:
        registry.register(self.spec)
        return self.spec

    def __exit__(self, *exc) -> None:
        registry.unregister(self.spec.name)


def spy_handler(monkeypatch, name: str, calls: list[Any]) -> None:
    """Kayıtlı aracın işleyicisini çağrıyı kaydeden bir casusla değiştirir."""
    spec = registry.get(name)

    async def spy(ctx, args):
        calls.append((name, ctx.actor.username))
        return {"spy": True}

    monkeypatch.setitem(registry._tools, name, replace(spec, handler=spy))

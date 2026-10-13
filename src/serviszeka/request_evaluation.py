"""Faz 3 kapı değerlendirmesi: "standart taleplerin %50'si insansız çözülüyor".

Gerçekçi bir standart talep karışımı (şifre sıfırlama, kilit açma, yazılım/Teams/VPN grubu,
M365 lisansı; içinde izin listesi dışı grup, boş lisansı olmayan SKU ve ajan tarafından başkası
adına açılan şifre sıfırlama gibi insansız çözülemeyecek talepler de var) bellek içi sahte dizinle
(`connectors.memory.InMemoryDirectory`) uçtan uca çalıştırılır. Onay bekleyen eylem istekleri
insan onaylayanlarla (R3 için iki kişi) sonuçlandırılır; ardından `requests.touchless_metrics` ile
oran ölçülür ve güvenlik değişmezleri denetlenir:

- İnsansız sayılan hiçbir talebin bağlı eylem isteği yoktur ve insan dokunuşu sıfırdır.
- Yürütülen her R2/R3 eylem isteğinin geçerli insan onayı sayısı gerekli sayıya eşit/büyüktür.

Kullanım: `uv run python evals/run_request_eval.py` (kapı geçilmezse çıkış kodu 1) ve
`tests/test_gate_no_unapproved_risk.py` bölüm 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import ActionRequestState, ApprovalDecision, RiskClass
from serviszeka.models import ActionApproval, ActionRequest, ServiceRequest
from serviszeka.services import catalog, requests

STANDARD_GROUPS = ["sg-yazilim-standart", "sg-vpn-kullanicilari", "m365-ekip-proje-x"]
APPROVERS = (
    CurrentUser("sef.ali", frozenset({"approver"})),
    CurrentUser("sef.veli", frozenset({"approver"})),
)
SERVICE_DESK = CurrentUser("ayse", frozenset({"agent"}))

# (katalog öğesi, talep eden, form, başkası adına açan ajan?)
SCENARIO: list[tuple[str, str, dict[str, Any], bool]] = [
    *[("password_reset", u, {}, False)
      for u in ("ali", "mehmet", "son.kullanici", "yeni.calisan", "zeynep", "mudur")],
    ("password_reset", "ali", {}, True),  # ajan adına açtı: doğrulanmadı -> R2
    *[("account_unlock", u, {}, False) for u in ("ali", "mehmet", "son.kullanici", "zeynep")],
    *[("standard_software_group", u, {"group": "sg-yazilim-standart"}, False)
      for u in ("ali", "mehmet", "yeni.calisan")],
    ("standard_software_group", "son.kullanici", {"group": "sg-finans-paylasim"}, False),  # R3
    *[("teams_channel_access", u, {"team": "m365-ekip-proje-x"}, False) for u in ("ali", "mehmet")],
    *[("vpn_access", u, {"justification": "Evden çalışma"}, False)
      for u in ("ali", "son.kullanici", "zeynep")],
    *[("m365_license", u, {"sku": "SPE_E3"}, False) for u in ("mehmet", "yeni.calisan")],
    ("m365_license", "ali", {"sku": "POWER_BI_PRO"}, False),  # boş lisans yok -> başarısız
    # Standart olmayanlar (orana girmez; R2/R3 adımları onay bekler)
    ("mfa_reset", "mehmet", {}, False),
    ("privileged_group_access", "ali",
     {"group": "sg-domain-admins", "justification": "Sunucu bakımı için geçici yetki"}, False),
]


def gate_settings(**overrides: Any) -> Settings:
    """Canlı politika: gölge mod kapalı, varsayılan eşik (0.75), standart grup izin listesi."""
    base = {
        "ai_shadow_mode": False,
        "ai_confidence_threshold": 0.75,
        "identity_standard_groups": STANDARD_GROUPS,
    }
    return Settings(**{**base, **overrides})


@dataclass
class ScenarioResult:
    metrics: requests.TouchlessMetrics
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics.to_dict(),
            "outcomes": self.outcomes,
            "violations": self.violations,
        }


async def _resolve_pending(session: AsyncSession, settings: Settings) -> None:
    """Bekleyen eylem isteklerini insan onaylayanlarla sonuçlandırır (R3: iki farklı kişi)."""
    from serviszeka.tools import actions

    for _ in range(10):
        pending = list(
            (
                await session.scalars(
                    select(ActionRequest).where(ActionRequest.state == ActionRequestState.PENDING)
                )
            ).all()
        )
        if not pending:
            return
        for ar in pending:
            for approver in APPROVERS:
                await session.refresh(ar)
                if ActionRequestState(ar.state) != ActionRequestState.PENDING:
                    break
                await actions.approve(session, ar.id, approver, "değerlendirme", settings=settings)
        await session.flush()


async def _business_approvals(session: AsyncSession, settings: Settings) -> None:
    """İş onayı bekleyenleri yöneticisi onaylar (demo dizinde herkesin yöneticisi zeynep)."""
    manager = CurrentUser("zeynep", frozenset({"requester"}))
    rows = await requests.list_requests(session, state="awaiting_approval", limit=500)
    for req in rows:
        await requests.decide(session, req.id, manager, ApprovalDecision.APPROVE, "uygun",
                              settings=settings)


async def check_invariants(session: AsyncSession) -> list[str]:
    violations: list[str] = []
    for req in (await session.scalars(select(ServiceRequest))).all():
        actions_ = await requests.linked_actions(session, req.id)
        if req.touchless and (actions_ or req.human_approvals or req.human_edits):
            violations.append(f"{req.number}: insansız sayıldı ama insan dokunuşu/onayı var")
    for ar in (await session.scalars(select(ActionRequest))).all():
        if ActionRequestState(ar.state) != ActionRequestState.EXECUTED:
            continue
        approvals = (
            await session.scalars(
                select(ActionApproval).where(
                    ActionApproval.action_request_id == ar.id,
                    ActionApproval.decision == ApprovalDecision.APPROVE,
                )
            )
        ).all()
        human = {a.approver_key for a in approvals if not a.approver.startswith("ai:")}
        needed = 2 if RiskClass(ar.risk_class) == RiskClass.R3 else 1
        if len(human) < needed:
            violations.append(f"{ar.number}: {ar.risk_class} {len(human)} insan onayıyla yürütüldü")
    return violations


async def run_scenario(session: AsyncSession, settings: Settings | None = None) -> ScenarioResult:
    """Kataloğu tohumlar, senaryoyu çalıştırır, onayları sonuçlandırır ve ölçer.

    Bağlayıcılar (`tools.connectors`) çağırandan önce sahte dizinle kurulmuş olmalıdır.
    """
    settings = settings or gate_settings()
    await catalog.seed_default_items(session)
    for key, requester, form, on_behalf in SCENARIO:
        item = await catalog.get_item(session, key)
        opener = SERVICE_DESK if on_behalf else CurrentUser(requester, frozenset({"requester"}))
        await requests.create_request(
            session, item, requester=requester, opened_by=opener, form_values=form,
            settings=settings,
        )
    await _business_approvals(session, settings)
    await _resolve_pending(session, settings)
    await session.flush()
    outcomes = [
        {
            "number": r.number,
            "item": r.item_key,
            "standard": r.standard,
            "requester": r.requester,
            "state": r.state.value,
            "touchless": r.touchless,
            "reason": r.error or r.touchless_reason,
        }
        for r in await requests.list_requests(session, limit=500)
    ]
    return ScenarioResult(
        metrics=await requests.touchless_metrics(session),
        outcomes=sorted(outcomes, key=lambda o: o["number"]),
        violations=await check_invariants(session),
    )

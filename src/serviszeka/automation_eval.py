"""Faz 3 kapı ölçümü: "standart taleplerin %50'si insansız çözülüyor" — etiketli veri setiyle.

`evals/data/requests.jsonl` 200 gerçekçi Türkçe hizmet talebidir (10 katalog öğesinin hepsi; standart
olmayanlar dahil). Her satırda talep edenin dizin öznitelikleri (departman, yönetici, ikincil e-posta,
hesap etkin mi, dizinde var mı, kilitli mi), talebi kimin açtığı (kendisi / servis masası), form
değerleri, insan karar verenlerin simüle kararı ve altın etiket `{item, expected_touchless, reason}`
vardır. Ayrıntılı alanlar: `evals/data/REQUESTS_CARD.md`.

Değerlendirme her talebi **gerçek talep boru hattından** (`services.requests.create_request` ->
`tools.execute_tool` -> `policy.evaluate`) geçirir:

- Dış sistemler: bellek içi sahte dizin (`connectors.memory.InMemoryDirectory`), veri setindeki
  kullanıcılar + bu modüldeki örnek kiracı (yöneticiler, onaylayanlar, gruplar, lisanslar). Ağ yok.
- Politika: gölge mod KAPALI, güven eşiği varsayılan (`Settings.ai_confidence_threshold`), standart
  grup izin listesi `STANDARD_GROUPS`.
- LLM yok: karşılama deterministik katalog planıdır. Değerlendirme, çağrılırsa sayıp hata veren bir
  LLM verir; `llm_calls` 0 olmalıdır (FakeLLM'e de gerek yoktur).
- İnsanlar simüle edilir: iş onayı yönetici / İK onay grubu, R2/R3 eylem istekleri BT onaylayanlar
  (R3 iki farklı kişi), el ile adım servis masası. Karar, satırdaki `human_decisions`'tan gelir.

Rapor: standart talepler üzerinde insansız oranı (kapı >= %50), **yanlış insansız** sayısı (etiket
"otomatikleşmemeli" derken insansız biten talep — 0 olmalı, sert kalma), öğe bazında oranlar, risk
sınıfına göre açılan eylem istekleri, güvenlik değişmezleri (`request_evaluation.check_invariants`).

Kullanım: `uv run python evals/run_automation_eval.py` (+ `--json`), testler
`tests/test_automation_eval.py` ve kapı bölüm 6.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.connectors.fake_stacks import STACKS, build_stack, install_stack
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.domain.enums import (
    ActionRequestState,
    CatalogApprovalPolicy,
    RiskClass,
)
from serviszeka.domain.enums import ServiceRequestState as RS
from serviszeka.models import ActionRequest, CatalogItem, ServiceRequest
from serviszeka.services import catalog, requests

DATASET_PATH = Path(__file__).resolve().parents[2] / "evals" / "data" / "requests.jsonl"

DOMAIN = "ornek.com.tr"

# --- örnek kiracı (Entra ID) ---------------------------------------------------------------------

# Departman yöneticileri (iş onayı "manager" politikasında onaylayan)
MANAGERS: dict[str, tuple[str, str]] = {
    "kerem.aydin": ("Kerem Aydın", "Finans"),
    "selin.arslan": ("Selin Arslan", "İnsan Kaynakları"),
    "murat.celik": ("Murat Çelik", "Satış"),
    "elif.ozturk": ("Elif Öztürk", "Bilgi Teknolojileri"),
    "hakan.yilmaz": ("Hakan Yılmaz", "Operasyon"),
    "derya.kurt": ("Derya Kurt", "Pazarlama"),
    "ozan.sahin": ("Ozan Şahin", "Hukuk"),
}
# İK onay grubu üyesi (yeni çalışan hesabı iş onayı)
HR_APPROVAL_GROUP = "sg-ik-onaycilari"
HR_APPROVER = "burcu.tan"
# BT onaylayanlar (R2/R3 eylem istekleri; R3 iki farklı kişi)
IT_APPROVERS = ("burak.koc", "deniz.ay")
# Servis masası ajanları (başkası adına talep açar, el ile adımı tamamlar)
SERVICE_DESK = ("emre.dogan", "gul.yildirim")

STANDARD_GROUPS = [
    "sg-yazilim-standart",
    "sg-yazilim-visio",
    "sg-yazilim-pdf-duzenleyici",
    "sg-yazilim-python",
    "sg-vpn-kullanicilari",
    "m365-ekip-proje-x",
    "m365-ekip-satis-bolge",
    "m365-ekip-ik-duyurular",
    "m365-ekip-finans-kapanis",
]
OTHER_GROUPS = {
    "sg-finans-paylasim": "Finans dosya paylaşımı",
    "m365-ekip-yonetim-kurulu": "Yönetim kurulu ekibi (gizli)",
    HR_APPROVAL_GROUP: "İK onaycıları",
}
PRIVILEGED_GROUPS = {
    "sg-domain-admins": "Etki alanı yöneticileri",
    "sg-sunucu-yoneticileri": "Sunucu yöneticileri",
    "sg-sql-sysadmin": "SQL Server sysadmin",
}
MAILBOXES = {
    "muhasebe": "Muhasebe paylaşılan posta kutusu",
    "destek": "Destek paylaşılan posta kutusu",
    "satis": "Satış paylaşılan posta kutusu",
    "ik-basvuru": "İK başvuru posta kutusu",
}
# (sku id, parça no, toplam, kullanılan): Power BI Pro'da boş lisans yok
SKUS = (("sku-spe-e3", "SPE_E3", 600, 480), ("sku-power-bi-pro", "POWER_BI_PRO", 25, 25))

DECISIONS = frozenset({"approve", "reject"})
PROFILE_KEYS = frozenset({
    "username", "display_name", "department", "title", "manager", "secondary_email",
    "account_enabled", "in_directory", "locked",
})
PROFILE_DEFAULTS = {"title": "", "secondary_email": True, "account_enabled": True,
                    "in_directory": True, "locked": False}


class DatasetError(ValueError):
    """Veri seti satırı şemaya ya da örnek kiracıya uymuyor."""


def _catalog_keys() -> dict[str, bool]:
    return {i["key"]: bool(i["standard"]) for i in catalog.default_items()}


def _profile(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DatasetError(f"{where}: kullanıcı profili nesne olmalı")
    unknown = set(raw) - PROFILE_KEYS
    if unknown:
        raise DatasetError(f"{where}: bilinmeyen profil alanı {sorted(unknown)}")
    for key in ("username", "display_name", "department", "manager"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise DatasetError(f"{where}: profil alanı eksik: {key}")
    if raw["manager"] not in MANAGERS:
        raise DatasetError(f"{where}: yönetici örnek kiracıda yok: {raw['manager']}")
    reserved = {*MANAGERS, HR_APPROVER, *IT_APPROVERS, *SERVICE_DESK}
    if raw["username"] in reserved:
        raise DatasetError(f"{where}: {raw['username']} örnek kiracıda ayrılmış bir kullanıcı")
    return {**PROFILE_DEFAULTS, **raw}


@dataclass(frozen=True)
class EvalRecord:
    id: str
    text: str
    item: str
    expected_touchless: bool
    reason: str
    requester: dict[str, Any]
    opened_by: str  # "self" ya da servis masası ajanı
    form: dict[str, Any]
    business_decision: str
    action_decision: str
    related_users: tuple[dict[str, Any], ...]
    raw: dict[str, Any] = field(repr=False, compare=False)

    @property
    def username(self) -> str:
        return self.requester["username"]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EvalRecord:
        rid = raw.get("id")
        if not isinstance(rid, str) or not rid:
            raise DatasetError("Satırda id yok")
        label = raw.get("label")
        if not isinstance(label, dict):
            raise DatasetError(f"{rid}: label yok")
        item, expected, reason = label.get("item"), label.get("expected_touchless"), label.get("reason")
        if item not in _catalog_keys():
            raise DatasetError(f"{rid}: katalogda olmayan öğe: {item}")
        if not isinstance(expected, bool):
            raise DatasetError(f"{rid}: label.expected_touchless bool olmalı")
        if not isinstance(reason, str) or not reason.strip():
            raise DatasetError(f"{rid}: label.reason boş")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise DatasetError(f"{rid}: text boş")
        opened_by = raw.get("opened_by", "self")
        if opened_by != "self" and opened_by not in SERVICE_DESK:
            raise DatasetError(f"{rid}: opened_by 'self' ya da servis masası ajanı olmalı")
        form = raw.get("form", {})
        if not isinstance(form, dict):
            raise DatasetError(f"{rid}: form nesne olmalı")
        decisions = raw.get("human_decisions", {})
        if not isinstance(decisions, dict) or set(decisions) - {"business", "action"}:
            raise DatasetError(f"{rid}: human_decisions yalnızca business/action içerir")
        business = decisions.get("business", "approve")
        action = decisions.get("action", "approve")
        if business not in DECISIONS or action not in DECISIONS:
            raise DatasetError(f"{rid}: karar approve/reject olmalı")
        related = raw.get("related_users", [])
        if not isinstance(related, list):
            raise DatasetError(f"{rid}: related_users liste olmalı")
        return cls(
            id=rid,
            text=text,
            item=item,
            expected_touchless=expected,
            reason=reason,
            requester=_profile(raw.get("requester"), rid),
            opened_by=opened_by,
            form=form,
            business_decision=business,
            action_decision=action,
            related_users=tuple(_profile(u, rid) for u in related),
            raw=raw,
        )


def load_dataset(path: Path | str = DATASET_PATH) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"satır {n}: geçersiz JSON ({exc})") from exc
            rec = EvalRecord.from_dict(raw)
            if rec.id in seen:
                raise DatasetError(f"satır {n}: yinelenen id {rec.id}")
            seen.add(rec.id)
            records.append(rec)
    return records


def build_directory(records: list[EvalRecord]) -> InMemoryDirectory:
    """Örnek kiracı + veri setindeki kullanıcı profilleri (tutarsız profil -> DatasetError)."""
    d = InMemoryDirectory()
    for username, (display, department) in MANAGERS.items():
        d.add_user(username, display, domain=DOMAIN, department=department,
                   other_mails=[f"{username}@kisisel.ornek"])
    for username in (HR_APPROVER, *IT_APPROVERS, *SERVICE_DESK):
        d.add_user(username, username, domain=DOMAIN, manager="elif.ozturk", department="BT")
    for gid in STANDARD_GROUPS:
        d.add_group(gid, gid)
    for gid, display in OTHER_GROUPS.items():
        d.add_group(gid, display)
    for gid, display in PRIVILEGED_GROUPS.items():
        d.add_group(gid, display, privileged=True)
    d.add_member(HR_APPROVAL_GROUP, HR_APPROVER)
    d.add_member(HR_APPROVAL_GROUP, "selin.arslan")
    for mailbox, display in MAILBOXES.items():
        d.add_mailbox(mailbox, display)
    for sku_id, part, enabled, consumed in SKUS:
        d.add_sku(sku_id, part, enabled=enabled, consumed=consumed)
    d.channels.update({"servis-masasi"})

    profiles: dict[str, dict[str, Any]] = {}
    for rec in records:
        for prof in (rec.requester, *rec.related_users):
            key = prof["username"].casefold()
            if key in profiles and profiles[key] != prof:
                raise DatasetError(f"{rec.id}: {prof['username']} profili önceki kayıtlarla çelişiyor")
            profiles[key] = prof
    for prof in profiles.values():
        if not prof["in_directory"]:
            continue
        user = d.add_user(
            prof["username"], prof["display_name"], domain=DOMAIN, manager=prof["manager"],
            department=prof["department"],
            other_mails=[f"{prof['username']}@kisisel.ornek"] if prof["secondary_email"] else [],
            enabled=prof["account_enabled"],
        )
        if prof["locked"]:
            d.locked.add(user.id)
    return d


def eval_settings(*, shadow_mode: bool = False) -> Settings:
    """Canlı politika: gölge kapalı, eşik varsayılan; `.env` okunmaz (tekrarlanabilir ölçüm)."""
    return Settings(
        _env_file=None,
        ai_shadow_mode=shadow_mode,
        ai_confidence_threshold=Settings.model_fields["ai_confidence_threshold"].default,
        identity_standard_groups=STANDARD_GROUPS,
        identity_privileged_groups=[],
    )


class _NoLLM:
    """Karşılama LLM kullanmamalı: herhangi bir çağrı sayılır ve hata verir."""

    def __init__(self) -> None:
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        def _refuse(*_: Any, **__: Any) -> Any:
            self.calls += 1
            raise RuntimeError(f"Değerlendirmede LLM çağrıldı: {name}")

        return _refuse


@dataclass
class AutomationReport:
    total: int
    standard_total: int
    standard_touchless: int
    false_touchless: list[dict[str, Any]]
    missed_automation: list[dict[str, Any]]
    by_item: list[dict[str, Any]]
    actions_by_risk: dict[str, dict[str, int]]
    violations: list[str]
    unresolved: list[dict[str, Any]]
    llm_calls: int
    label_agreement: float
    metrics: dict[str, Any]
    settings: dict[str, Any]
    outcomes: list[dict[str, Any]] = field(repr=False)

    @property
    def standard_touchless_rate(self) -> float:
        return self.standard_touchless / self.standard_total if self.standard_total else 0.0

    @property
    def meets_target(self) -> bool:
        return self.standard_total > 0 and self.standard_touchless_rate >= requests.GATE_TARGET

    @property
    def passed(self) -> bool:
        return (
            self.meets_target
            and not self.false_touchless
            and not self.violations
            and not self.unresolved
            and self.llm_calls == 0
        )

    def to_dict(self, *, outcomes: bool = False) -> dict[str, Any]:
        data = {
            "gate": {
                "target": requests.GATE_TARGET,
                "standard_total": self.standard_total,
                "standard_touchless": self.standard_touchless,
                "standard_touchless_rate": round(self.standard_touchless_rate, 4),
                "meets_target": self.meets_target,
                "false_touchless": len(self.false_touchless),
                "violations": len(self.violations),
                "unresolved": len(self.unresolved),
                "llm_calls": self.llm_calls,
                "passed": self.passed,
            },
            "total": self.total,
            "label_agreement": round(self.label_agreement, 4),
            "by_item": self.by_item,
            "actions_by_risk": self.actions_by_risk,
            "false_touchless": self.false_touchless,
            "missed_automation": self.missed_automation,
            "violations": self.violations,
            "unresolved": self.unresolved,
            "metrics": self.metrics,
            "settings": self.settings,
        }
        if outcomes:
            data["outcomes"] = self.outcomes
        return data


def _user(username: str, *roles: str) -> CurrentUser:
    return CurrentUser(username, frozenset(roles))


async def _drive(
    session: AsyncSession, req: ServiceRequest, rec: EvalRecord, settings: Settings, llm: Any
) -> None:
    """İnsanları simüle ederek talebi kapanana (ya da ilerleyemeyene) kadar sürdürür."""
    from serviszeka.tools import actions

    for _ in range(20):
        await session.refresh(req)
        state = RS(req.state)
        if state == RS.AWAITING_APPROVAL:
            if CatalogApprovalPolicy(req.approval_policy) == CatalogApprovalPolicy.GROUP:
                decider = _user(HR_APPROVER, "approver")
            else:
                decider = _user(rec.requester["manager"], "requester")
            await requests.decide(session, req.id, decider, rec.business_decision,
                                  "değerlendirme: iş onayı", settings=settings, llm=llm)
            continue
        if state != RS.FULFILLING:
            return
        if req.waiting_action_id:
            ar = await session.get(ActionRequest, req.waiting_action_id)
            if rec.action_decision == "reject":
                await actions.reject(session, ar.id, _user(IT_APPROVERS[0], "approver"),
                                     "değerlendirme: reddedildi")
                continue
            for approver in IT_APPROVERS:
                await session.refresh(ar)
                if ActionRequestState(ar.state) != ActionRequestState.PENDING:
                    break
                await actions.approve(session, ar.id, _user(approver, "approver"),
                                      "değerlendirme: onaylandı", settings=settings, llm=llm)
            continue
        plan = req.plan or []
        if req.current_step < len(plan) and plan[req.current_step].get("manual"):
            await requests.complete_manual_step(session, req.id, _user(SERVICE_DESK[0], "agent"),
                                                "teslim edildi", settings=settings, llm=llm)
            continue
        return


async def _evaluate(
    session: AsyncSession, records: list[EvalRecord], settings: Settings, llm: _NoLLM
) -> AutomationReport:
    from serviszeka.request_evaluation import check_invariants

    await catalog.seed_default_items(session)
    standard_keys = _catalog_keys()
    outcomes: list[dict[str, Any]] = []
    for rec in records:
        item = await catalog.get_item(session, rec.item)
        assert isinstance(item, CatalogItem)
        opener = (_user(rec.username, "requester") if rec.opened_by == "self"
                  else _user(rec.opened_by, "agent"))
        req = await requests.create_request(
            session, item, requester=rec.username, opened_by=opener, form_values=rec.form,
            settings=settings, llm=llm,
        )
        await _drive(session, req, rec, settings, llm)
        await session.flush()
        await session.refresh(req)
        linked = await requests.linked_actions(session, req.id)
        state = RS(req.state)
        touchless = bool(state == RS.FULFILLED and req.touchless)
        outcomes.append({
            "id": rec.id,
            "number": req.number,
            "item": rec.item,
            "standard": standard_keys[rec.item],
            "state": state.value,
            "touchless": touchless,
            "expected_touchless": rec.expected_touchless,
            "label_reason": rec.reason,
            "detail": req.error or req.touchless_reason,
            "actions": [
                {"number": a.number, "tool": a.tool, "risk_class": RiskClass(a.risk_class).value,
                 "state": ActionRequestState(a.state).value}
                for a in linked
            ],
        })
        await session.commit()

    closed = {s.value for s in requests.MEASURED_STATES}
    unresolved = [o for o in outcomes if o["state"] not in closed]
    std = [o for o in outcomes if o["standard"] and o["state"] in closed]
    false_touchless = [o for o in outcomes if o["touchless"] and not o["expected_touchless"]]
    missed = [o for o in outcomes if o["expected_touchless"] and not o["touchless"]]
    agreement = sum(o["touchless"] == o["expected_touchless"] for o in outcomes)

    rows: dict[str, dict[str, Any]] = {}
    for o in outcomes:
        row = rows.setdefault(o["item"], {"item": o["item"], "standard": o["standard"], "total": 0,
                                          "touchless": 0, "expected_touchless": 0,
                                          "states": Counter()})
        row["total"] += 1
        row["touchless"] += int(o["touchless"])
        row["expected_touchless"] += int(o["expected_touchless"])
        row["states"][o["state"]] += 1
    by_item = []
    for key in sorted(rows, key=lambda k: (not rows[k]["standard"], k)):
        row = rows[key]
        by_item.append({**row, "states": dict(sorted(row["states"].items())),
                        "touchless_rate": round(row["touchless"] / row["total"], 4)})

    actions_by_risk: dict[str, dict[str, int]] = defaultdict(lambda: {"created": 0})
    for ar in (await session.scalars(select(ActionRequest))).all():
        risk, state = RiskClass(ar.risk_class).value, ActionRequestState(ar.state).value
        actions_by_risk[risk]["created"] += 1
        actions_by_risk[risk][state] = actions_by_risk[risk].get(state, 0) + 1

    return AutomationReport(
        total=len(outcomes),
        standard_total=len(std),
        standard_touchless=sum(o["touchless"] for o in std),
        false_touchless=false_touchless,
        missed_automation=missed,
        by_item=by_item,
        actions_by_risk={k: dict(v) for k, v in sorted(actions_by_risk.items())},
        violations=await check_invariants(session),
        unresolved=unresolved,
        llm_calls=llm.calls,
        label_agreement=agreement / len(outcomes) if outcomes else 0.0,
        metrics=(await requests.touchless_metrics(session)).to_dict(),
        settings={
            "ai_shadow_mode": settings.ai_shadow_mode,
            "ai_confidence_threshold": settings.ai_confidence_threshold,
            "identity_standard_groups": list(settings.identity_standard_groups),
            "fulfilment_confidence": requests.FULFILMENT_CONFIDENCE,
        },
        outcomes=outcomes,
    )


async def run(
    records: list[EvalRecord], *, shadow_mode: bool = False, stack: str = "memory"
) -> AutomationReport:
    """Kayıtları ayrı bir bellek içi veritabanı ve sahte dizinle çalıştırır (genel durumu bozmaz).

    `stack` (`connectors.fake_stacks.STACKS`): `memory` (varsayılan sahte dizin), `entra` (Graph
    semantiği: bulutta kilit açma / paylaşılan posta kutusu yok), `keycloak` (Keycloak + Slack
    semantiği: kilit açma var, ikincil e-posta / posta / lisans yok). Aynı veri seti ve etiketler.
    """
    if stack not in STACKS:
        raise ValueError(f"Bilinmeyen yığın: {stack} (seçenekler: {', '.join(STACKS)})")
    from serviszeka import models  # noqa: F401  (tabloları kaydeder)
    from serviszeka.db import Base
    from serviszeka.tools.connectors import connectors

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    saved = connectors.snapshot()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        install_stack(connectors, build_stack(stack, build_directory(records)))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            report = await _evaluate(session, records, eval_settings(shadow_mode=shadow_mode),
                                     _NoLLM())
            report.settings["stack"] = stack
            return report
    finally:
        connectors.restore(saved)
        await engine.dispose()

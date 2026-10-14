"""Sorun yönetimi ajanı (ITIL 4 Problem Management): eğilim tespiti, RCA taslağı, kalıcı çözüm önerisi.

Ajan veritabanına **doğrudan yazmaz**: her yazma Faz 3 araç boru hattından (`tools.execute_tool`)
yapay zekâ aktörü `ai:problem` ile geçer; boru hattı `policy.evaluate` -> `AIDecision` -> `audit.record`
zincirini uygular, yalnızca AUTO_APPLIED ise çalıştırır, aksi halde insan onayı için `ActionRequest`
açar (bkz. docs/faz3.md). Ajan sorunu asla çözmez/kapatmaz, kesin RCA yazmaz, geçici çözümü
yayımlamaz (bunlar R2 ya da yalnızca insan; servis katmanı da `ai:` aktörü reddeder).

1. `detect_trends(session, window_days)` — deterministik, salt okuma. Son `window_days` gündeki
   olaylar (`kind=incident`, iptal hariç; kapanmamış bir soruna ya da bekleyen bir `problem.create`
   isteğine bağlı olanlar hariç; en yeni `MAX_INCIDENTS_SCANNED` olay) iki yoldan kümelenir:

   | Küme | Kural | Eşik | Güven |
   |---|---|---|---|
   | `ci_category` | aynı CI (`incident_cis`) + aynı kategori | ≥ `MIN_CI_CLUSTER` (3) olay | `0.70 + 0.05 × (boyut − 3)`, en fazla 0.95; kategorisiz −0.10 |
   | `similar_title` | başlık benzerliği ≥ `TITLE_SIMILARITY` (0.6); lider kümeleme (en eski olay lider, sıra created_at) | ≥ `MIN_TITLE_CLUSTER` (3) olay | `0.40 + 0.50 × ort. benzerlik`, en fazla 0.90 |

   Başlık benzerliği: bilgi bankası BM25 belirteçleyicisi (`knowledge.tokenize`: Türkçe küçük harf,
   noktalama ve durak kelimeleri atılır) + ilk `PREFIX_LEN` (5) harf kök yaklaşımı ile Jaccard.
   Olay kümesi bir CI kümesinin alt kümesi olan başlık kümesi atılır; aynı olay kümesi bir kez
   raporlanır. Önerilen öncelik kümedeki en acil olay önceliğidir.

2. `propose_problems(...)` — eşiği aşan her küme için `problem.create` (R1, politika
   `("problem", "create")`) çağrısı; idempotency anahtarı küme imzasıdır (aynı küme iki kez önerilmez).
3. `draft_rca(session, problem_id, llm)` — bağlı olayların **maskeli** metninden LLM ile 5 Neden
   taslağı (`serviszeka.llm`), `problem.record_rca` (R1) ile yalnızca `rca_draft`'a yazar.
4. `propose_fix(session, problem_id)` — `problem.propose_fix` (R1) ile değişiklik servisi üzerinden
   **taslak** RFC açar ve soruna bağlar. Güven: kesin kök neden 0.85, yalnızca taslak 0.60, yok 0.40.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import knowledge, taxonomy
from serviszeka.auth import ROLE_AI_AGENT, CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import (
    ActionRequestState,
    IncidentStatus,
    Priority,
    TicketKind,
)
from serviszeka.knowledge.search import PREFIX_LEN
from serviszeka.llm import LLMClient, LLMError
from serviszeka.models import ActionRequest, Incident, Problem
from serviszeka.privacy import mask_pii
from serviszeka.services import problems
from serviszeka.services.problem_schemas import RCA
from serviszeka.services.sla import ensure_utc

AGENT = "problem"
ACTOR = CurrentUser("ai:problem", frozenset({ROLE_AI_AGENT}))

DEFAULT_WINDOW_DAYS = 30
MIN_CI_CLUSTER = 3
MIN_TITLE_CLUSTER = 3
TITLE_SIMILARITY = 0.6
MAX_INCIDENTS_SCANNED = 2000
MAX_RCA_INCIDENTS = 20
MAX_TEXT = 500
TREND_MODEL = "deterministic-trends"
FIX_MODEL = "deterministic-fix"

# Fake LLM (llm/fake.py) bu başlığa bakarak RCA yanıtı üretir
RCA_SECTION = "Sorun kaydı (kök neden analizi taslağı):"

SYSTEM_PROMPT = (
    "Sen bir ITIL 4 sorun yönetimi uzmanısın. Verilen sorun kaydı ve bağlı olaylardan yola çıkarak "
    "Türkçe bir '5 Neden' kök neden analizi TASLAĞI hazırla. Yalnızca verilen bilgilere dayan; "
    "uydurma ayrıntı ekleme, emin olmadığın yerde bunu belirt. Kişisel veri yazma. "
    'Şema: {"method": "5_why", "steps": [{"question": "...", "answer": "..."}] (1-10 adım), '
    '"root_cause": "olası kök neden (tek cümle)", "confidence": 0.0-1.0}'
)


class RCADraftError(ValueError):
    """LLM RCA taslağı üretemedi (çağrı başarısız ya da şemaya uymayan yanıt); hiçbir şey yazılmaz."""


# --- benzerlik ----------------------------------------------------------------------------------


def title_stems(title: str) -> frozenset[str]:
    """Bilgi bankası belirteçleyicisi + önek kökü (ilk PREFIX_LEN harf)."""
    return frozenset(t[:PREFIX_LEN] for t in knowledge.tokenize(title or ""))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_similarity(a: str, b: str) -> float:
    return round(_jaccard(title_stems(a), title_stems(b)), 4)


# --- eğilim tespiti --------------------------------------------------------------------------------


@dataclass
class TrendCluster:
    kind: str  # ci_category / similar_title
    key: str
    title: str
    incident_ids: list[str]
    incident_numbers: list[str]
    ci_id: str | None = None
    category: str | None = None
    similarity: float = 1.0
    confidence: float = 0.0
    priority: Priority = Priority.P3
    descriptions: list[str] = field(default_factory=list, repr=False)

    @property
    def size(self) -> int:
        return len(self.incident_ids)

    def signature(self) -> str:
        raw = f"{self.kind}|{'|'.join(sorted(self.incident_ids))}"
        return hashlib.sha256(raw.encode()).hexdigest()[:40]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("descriptions", None)
        data["size"] = self.size
        data["priority"] = Priority(self.priority).value
        return data


def _rank(priority: Priority) -> int:
    return int(Priority(priority).value[1:])


def _most_urgent(incidents: list[Incident]) -> Priority:
    return min((Priority(i.priority) for i in incidents), key=_rank, default=Priority.P3)


async def _pending_proposal_incident_ids(session: AsyncSession) -> set[str]:
    rows = await session.scalars(
        select(ActionRequest).where(
            ActionRequest.tool == "problem.create",
            ActionRequest.state == ActionRequestState.PENDING,
        )
    )
    ids: set[str] = set()
    for ar in rows.all():
        ids.update(str(i) for i in (ar.args or {}).get("incident_ids") or [])
    return ids


async def _candidates(session: AsyncSession, since: datetime, now: datetime) -> list[Incident]:
    stmt = (
        select(Incident)
        .where(Incident.kind == TicketKind.INCIDENT, Incident.status != IncidentStatus.CANCELLED)
        .order_by(Incident.created_at.desc(), Incident.number.desc())
        .limit(MAX_INCIDENTS_SCANNED)
    )
    excluded = await problems.open_problem_incident_ids(session)
    excluded |= await _pending_proposal_incident_ids(session)
    rows = [
        i
        for i in (await session.scalars(stmt)).all()
        if i.id not in excluded
        and i.created_at is not None
        and since <= ensure_utc(i.created_at) <= now
    ]
    rows.sort(key=lambda i: (ensure_utc(i.created_at), i.number))
    return rows


def _ci_clusters(incidents: list[Incident], min_size: int) -> list[TrendCluster]:
    groups: dict[tuple[str, str | None], list[Incident]] = {}
    names: dict[str, str] = {}
    for inc in incidents:
        for ci in inc.affected_cis:
            names[ci.id] = ci.name
            groups.setdefault((ci.id, inc.category), []).append(inc)
    out: list[TrendCluster] = []
    seen: set[frozenset[str]] = set()
    for (ci_id, category), members in sorted(
        groups.items(), key=lambda kv: (-len(kv[1]), names[kv[0][0]], kv[0][1] or "")
    ):
        ids = frozenset(i.id for i in members)
        if len(members) < min_size or ids in seen:
            continue
        seen.add(ids)
        confidence = min(0.95, 0.70 + 0.05 * (len(members) - min_size))
        if category is None:
            confidence -= 0.10
        out.append(
            TrendCluster(
                kind="ci_category",
                key=f"ci:{ci_id}|category:{category or '-'}",
                title=f"Tekrarlayan olaylar: {names[ci_id]} ({category or 'kategorisiz'})"[:300],
                incident_ids=[i.id for i in members],
                incident_numbers=[i.number for i in members],
                ci_id=ci_id,
                category=category,
                similarity=1.0,
                confidence=round(confidence, 2),
                priority=_most_urgent(members),
            )
        )
    return out


def _title_clusters(
    incidents: list[Incident], min_size: int, threshold: float
) -> list[TrendCluster]:
    clusters: list[tuple[Incident, frozenset[str], list[tuple[Incident, float]]]] = []
    for inc in incidents:
        stems = title_stems(inc.title)
        if not stems:
            continue
        for leader, leader_stems, members in clusters:
            sim = _jaccard(leader_stems, stems)
            if sim >= threshold:
                members.append((inc, sim))
                break
        else:
            clusters.append((inc, stems, [(inc, 1.0)]))
    out: list[TrendCluster] = []
    for leader, _, members in clusters:
        if len(members) < min_size:
            continue
        others = [s for i, s in members if i is not leader]
        similarity = round(sum(others) / len(others), 4)
        incs = [i for i, _ in members]
        categories = {i.category for i in incs}
        out.append(
            TrendCluster(
                kind="similar_title",
                key=f"title:{leader.id}",
                title=f"Benzer olaylar: {leader.title}"[:300],
                incident_ids=[i.id for i in incs],
                incident_numbers=[i.number for i in incs],
                ci_id=None,
                category=categories.pop() if len(categories) == 1 else None,
                similarity=similarity,
                confidence=round(min(0.90, 0.40 + 0.50 * similarity), 2),
                priority=_most_urgent(incs),
            )
        )
    return out


async def detect_trends(
    session: AsyncSession,
    window_days: int = DEFAULT_WINDOW_DAYS,
    *,
    now: datetime | None = None,
    min_ci_cluster: int = MIN_CI_CLUSTER,
    min_title_cluster: int = MIN_TITLE_CLUSTER,
    similarity: float = TITLE_SIMILARITY,
) -> list[TrendCluster]:
    """Eşiği aşan olay kümeleri (deterministik, salt okuma; modül belgesine bakın)."""
    now = ensure_utc(now) if now else datetime.now(UTC)
    since = now - timedelta(days=max(1, int(window_days)))
    incidents = await _candidates(session, since, now)
    ci = _ci_clusters(incidents, max(2, min_ci_cluster))
    covered = [set(c.incident_ids) for c in ci]
    titles = [
        c
        for c in _title_clusters(incidents, max(2, min_title_cluster), similarity)
        if not any(set(c.incident_ids) <= s for s in covered)
    ]
    return sorted([*ci, *titles], key=lambda c: (-c.size, c.kind, c.key))


def _proposal_args(cluster: TrendCluster, window_days: int) -> dict[str, Any]:
    numbers = ", ".join(cluster.incident_numbers)
    basis = (
        "aynı CI ve kategori" if cluster.kind == "ci_category"
        else f"benzer başlık (ort. benzerlik {cluster.similarity:.2f})"
    )  # fmt: skip
    args: dict[str, Any] = {
        "title": cluster.title,
        "description": (
            f"Eğilim analizi: son {window_days} günde {cluster.size} olay ({basis}). "
            f"Olaylar: {numbers}."
        ),
        "priority": Priority(cluster.priority).value,
        "incident_ids": list(cluster.incident_ids),
        "ci_ids": [cluster.ci_id] if cluster.ci_id else [],
    }
    if cluster.category and cluster.category in taxonomy.allowed("category"):
        args["category"] = cluster.category
    return args


async def propose_problems(
    session: AsyncSession,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[Any]:
    """Her küme için `problem.create` (R1) önerir; `ToolResult` listesi döner (commit etmez)."""
    from serviszeka.tools.executor import execute_tool

    settings = settings or get_settings()
    results = []
    for cluster in await detect_trends(session, window_days, now=now):
        results.append(
            await execute_tool(
                session,
                ACTOR,
                "problem.create",
                _proposal_args(cluster, window_days),
                idempotency_key=f"problem-trend:{cluster.signature()}",
                reason=f"{cluster.kind} kümesi: {cluster.size} olay ({cluster.key})",
                confidence=cluster.confidence,
                model=TREND_MODEL,
                settings=settings,
                now=now,
            )
        )
    return results


# --- RCA taslağı (LLM) ---------------------------------------------------------------------------


def _clip(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


def rca_prompt(problem: Problem) -> str:
    """Sorun + bağlı olayların maskeli metni (LLM istemcisi ayrıca maskeler)."""
    lines = [
        RCA_SECTION,
        f"Numara: {problem.number}",
        f"Başlık: {problem.title}",
        f"Açıklama: {_clip(problem.description) or '-'}",
        f"Geçici çözüm: {_clip(problem.workaround) or '-'}",
        f"Etkilenen CI'lar: {', '.join(c.name for c in problem.affected_cis) or '-'}",
        "",
        "Bağlı olaylar:",
    ]
    incidents = list(problem.incidents)[:MAX_RCA_INCIDENTS]
    if not incidents:
        lines.append("- (bağlı olay yok)")
    for inc in incidents:
        lines.append(
            f"- {inc.number} | {inc.title} | kategori: {inc.category or '-'} | "
            f"açıklama: {_clip(inc.description) or '-'} | çözüm: {_clip(inc.resolution) or '-'}"
        )
    return mask_pii("\n".join(lines))


async def generate_rca(
    problem: Problem, llm: LLMClient, settings: Settings | None = None
) -> tuple[RCA, float, str]:
    """LLM'den 5 Neden taslağı; (RCA, güven, model). Hata -> `RCADraftError` (yazma yok)."""
    settings = settings or get_settings()
    model = settings.llm_reasoning_model
    try:
        raw = await llm.complete_json(system=SYSTEM_PROMPT, user=rca_prompt(problem), model=model)
    except LLMError as exc:
        raise RCADraftError(f"LLM çağrısı başarısız: {exc}") from exc
    if not isinstance(raw, dict):
        raise RCADraftError("LLM yanıtı nesne değil.")
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        confidence = 0.0
    try:
        rca = RCA.model_validate(
            {k: raw[k] for k in ("method", "steps", "root_cause") if k in raw}
        )
    except ValidationError as exc:
        details = json.loads(exc.json(include_url=False, include_input=False))
        raise RCADraftError(f"LLM RCA taslağı şemaya uymuyor: {details}") from exc
    return rca, confidence, getattr(llm, "model_name", None) or model


async def draft_rca(
    session: AsyncSession,
    problem_id: str,
    llm: LLMClient,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> Any:
    """RCA taslağını LLM ile üretir ve `problem.record_rca` (R1) ile `rca_draft`'a önerir."""
    from serviszeka.tools.executor import execute_tool

    settings = settings or get_settings()
    problem = await problems.get_problem(session, problem_id)
    if problem is None:
        raise problems.ProblemNotFoundError(f"Sorun bulunamadı: {problem_id}")
    rca, confidence, model = await generate_rca(problem, llm, settings)
    return await execute_tool(
        session,
        ACTOR,
        "problem.record_rca",
        {"problem_id": problem.id, "rca": rca.model_dump(mode="json", exclude_none=True)},
        reason="Bağlı olayların maskeli metninden 5 Neden taslağı (yalnızca taslak).",
        confidence=confidence,
        model=model,
        settings=settings,
        llm=llm,
        now=now,
    )


# --- kalıcı çözüm önerisi ------------------------------------------------------------------------


def fix_confidence(problem: Problem) -> float:
    if problem.root_cause:
        return 0.85
    if (problem.rca_draft or {}).get("root_cause"):
        return 0.60
    return 0.40


async def propose_fix(
    session: AsyncSession,
    problem_id: str,
    *,
    title: str | None = None,
    implementation_plan: str = "",
    backout_plan: str = "",
    test_plan: str = "",
    settings: Settings | None = None,
    now: datetime | None = None,
) -> Any:
    """Kalıcı çözüm için taslak RFC önerir (`problem.propose_fix` R1); durum geçişi yapmaz."""
    from serviszeka.tools.executor import execute_tool

    settings = settings or get_settings()
    problem = await problems.get_problem(session, problem_id)
    if problem is None:
        raise problems.ProblemNotFoundError(f"Sorun bulunamadı: {problem_id}")
    args: dict[str, Any] = {"problem_id": problem.id}
    if title:
        args["title"] = title
    for key, value in (
        ("implementation_plan", implementation_plan),
        ("backout_plan", backout_plan),
        ("test_plan", test_plan),
    ):
        if value:
            args[key] = value
    return await execute_tool(
        session,
        ACTOR,
        "problem.propose_fix",
        args,
        reason=f"{problem.number} için kalıcı çözüm taslak RFC önerisi.",
        confidence=fix_confidence(problem),
        model=FIX_MODEL,
        settings=settings,
        now=now,
    )

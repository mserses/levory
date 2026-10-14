"""Faz 4 — sürekli iyileştirme KPI'ları (tanımlar, formüller, pencere + eğilim).

Tüm KPI'lar yarı açık bir UTC penceresinde ``[start, end)`` hesaplanır ve aynı uzunluktaki önceki
pencereyle (``[start - (end - start), start)``) karşılaştırılır (``trend``: ``improved`` /
``worsened`` / ``flat`` / ``n/a``; yön KPI'nın ``better`` alanından gelir). Böylece "MTTR şu
dönemde %X düştü" doğrudan veriden hesaplanır (Faz 4 hedefi).

Zaman: tüm sınırlar UTC'ye çevrilir (saat dilimsiz girdi UTC kabul edilir). Süreler SQL'de
``epoch_seconds`` ile hesaplanır: PostgreSQL'de ``EXTRACT(EPOCH FROM timestamptz)`` (saat dilimine
duyarlı), SQLite'ta ``julianday`` (değerler UTC saklanır). Ortalama/medyan/p90 gibi sıra
istatistikleri taşınabilirlik için SQL'den gelen satır süreleri üzerinden Python'da hesaplanır;
sayım/oran KPI'ları SQL ``COUNT``/``SUM(CASE ...)`` ile gruplanır. Denetim günlüğüne (durum
geçmişi) dayanan KPI'lar (yeniden açılma, ilk temasta çözüm, yapay zekâ kabulü, bilinen hata
süresi) JSON ayrıntıyı taşınabilir biçimde Python'da yorumlar.

Tanımlar (``Olay`` = ``Incident.kind == incident``; ``etkin süre`` = duvar saati süresi eksi
``sla_paused_seconds``, yani arayan/tedarikçi beklemesinde duran SLA saati hariç):

- **mttr** (saat, düşük iyi): penceredede çözülen (``resolved_at`` ∈ W) olaylarda
  ``ortalama(resolved_at - created_at - sla_paused_seconds)``; ayrıca medyan ve p90; kırılım
  öncelik / çözüm grubu / kategori / hizmet. Yeniden açılan olayın ``resolved_at``'i sıfırlanır,
  son çözüm sayılır. İptal edilenler hariç.
- **mtta** (saat, düşük iyi): ``responded_at`` ∈ W olaylarda ``ortalama(responded_at - created_at)``
  (ilk yanıt = NEW durumundan ilk çıkış).
- **fcr_rate** (%, yüksek iyi) — ilk temasta çözüm vekili: W'de çözülen olaylardan hiç **yeniden
  atanmamış** (``assignment_group`` dolu bir değerden başka bir değere hiç değişmemiş) ve hiç
  **yeniden açılmamış** olanların oranı.
- **sla_response_compliance** / **sla_resolve_compliance** (%, yüksek iyi): W içinde "karara
  bağlanan" hedefler: (a) karşılanma anı W'de olanlar → ``karşılanma <= vade`` ise uyumlu;
  (b) hâlâ karşılanmamış ve vadesi W'de (ve şimdiden önce) geçmiş olanlar → ihlal (saat şu an
  duraklatılmışsa ve vade duraklamadan sonraysa sayılmaz). Uyum = uyumlu / (uyumlu + ihlal).
  Vadeler duraklatma kadar ötelendiği için duraklatılan süre uyumu bozmaz.
- **backlog** (adet, düşük iyi): pencere sonunda açık olay sayısı. ``T`` anında açık =
  ``created_at < T`` ve (``resolved_at > T`` ya da (``resolved_at`` boş ve durum kapalı/iptal
  değil)). Yaşlanma kovaları (0-1g, 1-3g, 3-7g, 7-30g, 30g+) ve öncelik/grup kırılımı. Not: iptal
  zamanı tutulmadığından iptal edilen olaylar geçmiş anlarda da açık sayılmaz; yeniden açılan
  olayın geçmişteki çözüm aralığı açık görünür (yaklaşık).
- **reopen_rate** (%, düşük iyi): W'deki denetim kayıtlarında ``status: resolved -> (kapalı
  olmayan)`` geçişi olan olay sayısı / W'de çözülen ya da yeniden açılan olay sayısı.
- **automation_rate** (%, yüksek iyi): ``fulfilled_at`` ∈ W hizmet taleplerinde ``touchless`` oranı
  (sıfır insan onayı + sıfır insan düzenlemesi, Faz 3 tanımı); kırılım katalog öğesi.
- **ai_triage_acceptance** (%, yüksek iyi): W'de üretilen ``service_desk.classify`` kararları
  içinde **kabul edilen** oranı. Kabul = karar uygulandı (``auto_applied`` ya da insan onayladı)
  **ve** sonrasında bir insan (``ai:`` olmayan aktör, onay güncellemesi hariç) kararın uyguladığı
  alanlardan birini (etki/aciliyet varsa öncelik dahil) değiştirmedi. ``details``: uygulanan,
  sonradan insanca düzeltilen (``overridden``), uygulanmayan (öneri/onay bekleyen/eskale/red).
  Not: gölge modda önerilip insanın elle aynen uyguladığı karar "uygulanmadı" sayılır.
- **change_success_rate** (%, yüksek iyi): uygulaması W'de biten (``actual_end`` ∈ W,
  ``implementation_outcome`` dolu) değişikliklerde ``succeeded`` oranı. Başarısız = ``failed``
  (geri alma ayrı bir durum olarak tutulmaz; geri alınan değişiklik ``failed`` ile kapanır).
- **emergency_change_share** (%, düşük iyi): W'de açılan değişikliklerde acil (``emergency``) payı.
- **problem_known_error_lead_time** (saat, düşük iyi): W'de bilinen hataya (``known_error``)
  geçen sorunlarda ``ortalama(geçiş anı - created_at)``; geçiş anı denetimdeki ilk
  ``problem.transition (to=known_error)`` ya da ``problem.known_error.publish (from=new|
  investigating)`` kaydıdır.

Bu modül yalnızca okur (yazma/flush/commit yok; kapı testi statik tarar).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement, FunctionElement

from serviszeka.domain.enums import (
    ChangeType,
    DecisionOutcome,
    IncidentStatus,
    ProblemState,
    TicketKind,
)
from serviszeka.models import (
    AIDecision,
    AuditLog,
    ChangeRequest,
    Incident,
    Problem,
    ServiceRequest,
)
from serviszeka.services.kpi_schemas import (
    KPIDefinitionRead,
    KPIGroupStat,
    KPIReportRead,
    KPIValueRead,
    KPIWindowRead,
)
from serviszeka.services.sla import ensure_utc

DEFAULT_DAYS = 30
MAX_DAYS = 366
# IN (...) listeleri için parça boyutu (SQLite değişken sınırı)
_CHUNK = 500
_HOUR = 3600.0

# --- taşınabilir süre ifadesi -----------------------------------------------------------------


class epoch_seconds(FunctionElement):
    """Zaman damgasının Unix epoch saniyesi (float); lehçeye göre derlenir."""

    type = Float()
    inherit_cache = True
    name = "sz_epoch_seconds"


@compiles(epoch_seconds)
def _epoch_default(element, compiler, **kw):  # PostgreSQL ve diğerleri: saat dilimine duyarlı
    arg = compiler.process(element.clauses, **kw)
    return f"CAST(EXTRACT(EPOCH FROM {arg}) AS DOUBLE PRECISION)"


@compiles(epoch_seconds, "sqlite")
def _epoch_sqlite(element, compiler, **kw):  # SQLite: değerler UTC metin olarak saklanır
    return f"((julianday({compiler.process(element.clauses, **kw)}) - 2440587.5) * 86400.0)"


def duration_seconds(end: Any, start: Any) -> ColumnElement[float]:
    """``end - start`` saniye cinsinden (SQL)."""
    return epoch_seconds(end) - epoch_seconds(start)


def effective_resolve_seconds() -> ColumnElement[float]:
    """Olay için ``resolved_at - created_at - sla_paused_seconds`` (SQL)."""
    return duration_seconds(Incident.resolved_at, Incident.created_at) - func.coalesce(
        Incident.sla_paused_seconds, 0
    )


# --- pencere ----------------------------------------------------------------------------------


def to_utc(value: datetime) -> datetime:
    """Saat dilimsiz değer UTC kabul edilir; diğerleri UTC'ye çevrilir."""
    return ensure_utc(value)


def _shift(value: datetime, delta: timedelta) -> datetime:
    """``value + delta``; takvim dışına taşarsa ValueError (API 422, 500 değil)."""
    try:
        return value + delta
    except OverflowError as exc:
        raise ValueError("Tarih desteklenen aralığın dışında.") from exc


@dataclass(frozen=True)
class Window:
    """Yarı açık UTC penceresi ``[start, end)``."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", to_utc(self.start))
        object.__setattr__(self, "end", to_utc(self.end))
        if self.end <= self.start:
            raise ValueError("Pencere sonu başlangıçtan sonra olmalı.")

    @classmethod
    def ending(cls, end: datetime | None = None, days: int = DEFAULT_DAYS) -> Window:
        if not 1 <= days <= MAX_DAYS:
            raise ValueError(f"Gün sayısı 1-{MAX_DAYS} aralığında olmalı.")
        end = to_utc(end or datetime.now(UTC))
        return cls(_shift(end, -timedelta(days=days)), end)

    def previous(self) -> Window:
        span = self.end - self.start
        return Window(_shift(self.start, -span), self.start)

    def contains(self, column: Any) -> ColumnElement[bool]:
        return and_(column >= self.start, column < self.end)

    def has(self, ts: datetime | None) -> bool:
        return ts is not None and self.start <= to_utc(ts) < self.end

    def read(self) -> KPIWindowRead:
        return KPIWindowRead(start=self.start, end=self.end)


# --- tanımlar ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class KPIDefinition:
    key: str
    name: str
    unit: str  # hours / percent / count
    better: str  # lower / higher
    formula: str

    def read(self) -> KPIDefinitionRead:
        return KPIDefinitionRead(
            key=self.key, name=self.name, unit=self.unit, better=self.better, formula=self.formula
        )


_DEFS: tuple[KPIDefinition, ...] = (
    KPIDefinition(
        "mttr", "Ortalama çözüm süresi (MTTR)", "hours", "lower",
        "ort(resolved_at - created_at - sla_paused_seconds), resolved_at ∈ W, tür=olay",
    ),
    KPIDefinition(
        "mtta", "Ortalama ilk yanıt süresi (MTTA)", "hours", "lower",
        "ort(responded_at - created_at), responded_at ∈ W, tür=olay, iptal hariç",
    ),
    KPIDefinition(
        "fcr_rate", "İlk temasta çözüm oranı (vekil)", "percent", "higher",
        "W'de çözülen olaylardan hiç yeniden atanmamış ve yeniden açılmamış olanlar / tümü",
    ),
    KPIDefinition(
        "sla_response_compliance", "Yanıt SLA uyumu", "percent", "higher",
        "vadesinde yanıtlanan / (W'de yanıtlanan + vadesi W'de geçen yanıtsız)",
    ),
    KPIDefinition(
        "sla_resolve_compliance", "Çözüm SLA uyumu", "percent", "higher",
        "vadesinde çözülen / (W'de çözülen + vadesi W'de geçen açık)",
    ),
    KPIDefinition(
        "backlog", "Açık iş yükü (backlog)", "count", "lower",
        "pencere sonunda açık olay sayısı; yaşlanma kovaları",
    ),
    KPIDefinition(
        "reopen_rate", "Yeniden açılma oranı", "percent", "lower",
        "W'de resolved -> açık geçişi olan olay / W'de çözülen ya da yeniden açılan olay",
    ),
    KPIDefinition(
        "automation_rate", "İnsansız talep oranı", "percent", "higher",
        "touchless / fulfilled_at ∈ W hizmet talepleri",
    ),
    KPIDefinition(
        "ai_triage_acceptance", "Yapay zekâ triage kabul oranı", "percent", "higher",
        "uygulanıp sonradan insanca değiştirilmeyen classify kararları / W'deki tüm kararlar",
    ),
    KPIDefinition(
        "change_success_rate", "Değişiklik başarı oranı", "percent", "higher",
        "succeeded / actual_end ∈ W ve sonucu belli değişiklikler",
    ),
    KPIDefinition(
        "emergency_change_share", "Acil değişiklik payı", "percent", "lower",
        "emergency / created_at ∈ W değişiklikler",
    ),
    KPIDefinition(
        "problem_known_error_lead_time", "Sorun → bilinen hata süresi", "hours", "lower",
        "ort(known_error geçiş anı - sorun created_at), geçiş ∈ W",
    ),
)  # fmt: skip
DEFINITIONS: dict[str, KPIDefinition] = {d.key: d for d in _DEFS}
KPI_KEYS: tuple[str, ...] = tuple(DEFINITIONS)
SLA_KPI_KEYS: tuple[str, ...] = ("sla_response_compliance", "sla_resolve_compliance")


def definitions() -> list[KPIDefinitionRead]:
    return [d.read() for d in _DEFS]


# --- yardımcılar ------------------------------------------------------------------------------


@dataclass
class KPIResult:
    """Tek pencere için KPI sonucu."""

    value: float | None
    count: int
    details: dict[str, Any] = field(default_factory=dict)
    breakdown: dict[str, list[KPIGroupStat]] = field(default_factory=dict)


def _hours(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds / _HOUR, 3)


def _pct(num: int, den: int) -> float | None:
    return None if den == 0 else round(100.0 * num / den, 2)


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Doğrusal ara değerli yüzdelik (0 <= q <= 1)."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def duration_stats(seconds: Iterable[float]) -> dict[str, float | int | None]:
    values = sorted(max(float(s), 0.0) for s in seconds)
    if not values:
        return {"count": 0, "mean_hours": None, "median_hours": None, "p90_hours": None}
    return {
        "count": len(values),
        "mean_hours": _hours(statistics.fmean(values)),
        "median_hours": _hours(statistics.median(values)),
        "p90_hours": _hours(_percentile(values, 0.9)),
    }


def _key(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _duration_breakdown(rows: Sequence[tuple[dict[str, Any], float]], dims: Sequence[str]):
    out: dict[str, list[KPIGroupStat]] = {}
    for dim in dims:
        groups: dict[str | None, list[float]] = defaultdict(list)
        for keys, secs in rows:
            groups[keys[dim]].append(max(secs, 0.0))
        out[dim] = [
            KPIGroupStat(key=k, value=_hours(statistics.fmean(v)), count=len(v))
            for k, v in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
        ]
    return out


def _ratio_breakdown(groups: dict[str | None, list[int]]) -> list[KPIGroupStat]:
    """groups: anahtar -> [pay, payda]."""
    return [
        KPIGroupStat(key=k, value=_pct(n, d), count=d)
        for k, (n, d) in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
    ]


def _chunks(items: Sequence[str]) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def _changes(details: Any) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    changes = details.get("changes")
    return changes if isinstance(changes, dict) else {}


def _pair(changes: dict[str, Any], name: str) -> tuple[Any, Any] | None:
    value = changes.get(name)
    if isinstance(value, list | tuple) and len(value) == 2:
        return value[0], value[1]
    return None


_TERMINAL = {IncidentStatus.CLOSED.value, IncidentStatus.RESOLVED.value}


def is_reopen(details: Any) -> bool:
    pair = _pair(_changes(details), "status")
    return (
        pair is not None and pair[0] == IncidentStatus.RESOLVED.value and pair[1] not in _TERMINAL
    )


def is_resolution(details: Any) -> bool:
    pair = _pair(_changes(details), "status")
    return pair is not None and pair[1] == IncidentStatus.RESOLVED.value


def is_reassignment(details: Any) -> bool:
    pair = _pair(_changes(details), "assignment_group")
    return pair is not None and pair[0] not in (None, "") and pair[0] != pair[1]


_IS_INCIDENT = Incident.kind == TicketKind.INCIDENT
_CLOSED_OR_CANCELLED = [IncidentStatus.CLOSED, IncidentStatus.CANCELLED]


# --- KPI hesaplayıcıları ----------------------------------------------------------------------


async def _mttr(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    stmt = select(
        Incident.priority,
        Incident.assignment_group,
        Incident.category,
        Incident.service,
        effective_resolve_seconds(),
    ).where(
        _IS_INCIDENT,
        Incident.status != IncidentStatus.CANCELLED,
        Incident.resolved_at.is_not(None),
        w.contains(Incident.resolved_at),
    )
    rows = [
        (
            {"priority": _key(p), "assignment_group": g, "category": c, "service": s},
            float(secs or 0.0),
        )
        for p, g, c, s, secs in (await session.execute(stmt)).all()
    ]
    stats = duration_stats(secs for _, secs in rows)
    return KPIResult(
        value=stats["mean_hours"],  # type: ignore[arg-type]
        count=len(rows),
        details=stats,
        breakdown=_duration_breakdown(
            rows, ("priority", "assignment_group", "category", "service")
        ),
    )


async def _mtta(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    stmt = select(
        Incident.priority,
        Incident.assignment_group,
        duration_seconds(Incident.responded_at, Incident.created_at),
    ).where(
        _IS_INCIDENT,
        # MTTR ile tutarlı: iptal edilen olay (yanlış/yinelenen kayıt) ortalamayı bozmaz
        Incident.status != IncidentStatus.CANCELLED,
        Incident.responded_at.is_not(None),
        w.contains(Incident.responded_at),
    )
    rows = [
        ({"priority": _key(p), "assignment_group": g}, float(secs or 0.0))
        for p, g, secs in (await session.execute(stmt)).all()
    ]
    stats = duration_stats(secs for _, secs in rows)
    return KPIResult(
        value=stats["mean_hours"],  # type: ignore[arg-type]
        count=len(rows),
        details=stats,
        breakdown=_duration_breakdown(rows, ("priority", "assignment_group")),
    )


async def _incident_history(
    session: AsyncSession, incident_ids: Sequence[str]
) -> dict[str, list[AuditLog]]:
    history: dict[str, list[AuditLog]] = defaultdict(list)
    for part in _chunks(list(incident_ids)):
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.entity_type == "incident",
                AuditLog.action == "incident.update",
                AuditLog.entity_id.in_(list(part)),
            )
            .order_by(AuditLog.seq)
        )
        for row in (await session.scalars(stmt)).all():
            history[row.entity_id].append(row)
    return history


# Diğer modüller (agents/improvement) için salt okuma geçmiş erişimi
incident_history = _incident_history


# --- denetim geçmişi yüklemleri SQL'de (is_reassignment / is_reopen ile aynı kural) ------------------


def _change_value(field_name: str, index: int) -> ColumnElement[Any]:
    """`details.changes.<alan>[i]` metin değeri (SQLite json_extract / PostgreSQL #>>)."""
    return AuditLog.details[("changes", field_name, index)].as_string()


def reassignment_clause() -> ColumnElement[bool]:
    """`is_reassignment` SQL karşılığı: dolu bir gruptan farklı bir değere (boş dahil) geçiş."""
    old, new = _change_value("assignment_group", 0), _change_value("assignment_group", 1)
    return and_(old.is_not(None), old != "", or_(new.is_(None), old != new))


def reopen_clause() -> ColumnElement[bool]:
    """`is_reopen` SQL karşılığı: `resolved` -> kapalı/çözülmüş olmayan durum."""
    old, new = _change_value("status", 0), _change_value("status", 1)
    return and_(
        old == IncidentStatus.RESOLVED.value,
        or_(new.is_(None), new.not_in(sorted(_TERMINAL))),
    )


async def not_first_contact_ids(session: AsyncSession, incident_ids: Sequence[str]) -> set[str]:
    """Yeniden atanmış ya da yeniden açılmış olaylar (ilk temasta çözüm sayılmaz).

    Denetim satırları Python'a okunmaz: yüklem SQL'de (JSON yol ifadesi) değerlendirilir, yalnızca
    eşleşen olay kimlikleri döner. Sonuç `is_reassignment` / `is_reopen` ile aynıdır
    (tests/test_faz4_open_items.py).
    """
    found: set[str] = set()
    for part in _chunks(sorted(set(incident_ids))):
        stmt = (
            select(AuditLog.entity_id)
            .where(
                AuditLog.entity_type == "incident",
                AuditLog.action == "incident.update",
                AuditLog.entity_id.in_(list(part)),
                or_(reassignment_clause(), reopen_clause()),
            )
            .distinct()
        )
        found.update((await session.scalars(stmt)).all())
    return found


async def _fcr(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    stmt = select(Incident.id, Incident.priority).where(
        _IS_INCIDENT,
        Incident.status != IncidentStatus.CANCELLED,
        Incident.resolved_at.is_not(None),
        w.contains(Incident.resolved_at),
    )
    rows = (await session.execute(stmt)).all()
    history = await _incident_history(session, [r[0] for r in rows])
    by_priority: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    first = reassigned = reopened = 0
    for incident_id, priority in rows:
        events = history.get(incident_id, [])
        was_reassigned = any(is_reassignment(e.details) for e in events)
        was_reopened = any(is_reopen(e.details) for e in events)
        reassigned += was_reassigned
        reopened += was_reopened
        ok = not (was_reassigned or was_reopened)
        first += ok
        bucket = by_priority[_key(priority)]
        bucket[0] += ok
        bucket[1] += 1
    return KPIResult(
        value=_pct(first, len(rows)),
        count=len(rows),
        details={"first_contact": first, "reassigned": reassigned, "reopened": reopened},
        breakdown={"priority": _ratio_breakdown(by_priority)},
    )


def _compliance_stmt(w: Window, now: datetime, done_col: Any, due_col: Any):
    """(öncelik, grup, kategori, uyumlu mu) satırları: W'de karşılanan + vadesi W'de geçen açık."""
    horizon = min(w.end, now)
    decided_in_window = and_(done_col.is_not(None), w.contains(done_col))
    overdue_open = and_(
        done_col.is_(None),
        Incident.status.not_in(_CLOSED_OR_CANCELLED),
        due_col >= w.start,
        due_col < horizon,
        # Saat şu an duruyorsa duraklamadan sonraki vade henüz ihlal değildir
        or_(Incident.sla_paused_at.is_(None), due_col < Incident.sla_paused_at),
    )
    met = case((and_(done_col.is_not(None), done_col <= due_col), 1), else_=0)
    return (
        select(
            Incident.priority,
            Incident.assignment_group,
            Incident.category,
            func.count().label("n"),
            func.sum(met).label("met"),
        )
        .where(
            _IS_INCIDENT,
            Incident.status != IncidentStatus.CANCELLED,
            due_col.is_not(None),
            or_(decided_in_window, overdue_open),
        )
        .group_by(Incident.priority, Incident.assignment_group, Incident.category)
    )


async def _compliance(
    session: AsyncSession, w: Window, now: datetime, done_col: Any, due_col: Any
) -> KPIResult:
    rows = (await session.execute(_compliance_stmt(w, now, done_col, due_col))).all()
    by_priority: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    by_group: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    by_category: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    total = met_total = 0
    for priority, group, category, n, met in rows:
        n, met = int(n or 0), int(met or 0)
        total += n
        met_total += met
        for bucket in (by_priority[_key(priority)], by_group[group], by_category[category]):
            bucket[0] += met
            bucket[1] += n
    return KPIResult(
        value=_pct(met_total, total),
        count=total,
        details={"met": met_total, "breached": total - met_total},
        breakdown={
            "priority": _ratio_breakdown(by_priority),
            "assignment_group": _ratio_breakdown(by_group),
            "category": _ratio_breakdown(by_category),
        },
    )


async def _incident_dims(
    session: AsyncSession, incident_ids: Iterable[str]
) -> dict[str, tuple[str | None, str | None]]:
    """Olay kimliği -> (çözüm grubu, kategori); oran KPI'larının kırılımı için."""
    out: dict[str, tuple[str | None, str | None]] = {}
    for part in _chunks(sorted(set(incident_ids))):
        stmt = select(Incident.id, Incident.assignment_group, Incident.category).where(
            Incident.id.in_(list(part))
        )
        for incident_id, group, category in (await session.execute(stmt)).all():
            out[incident_id] = (group, category)
    return out


def _dim_breakdown(
    ids: Iterable[str], hits: set[str], dims: dict[str, tuple[str | None, str | None]]
) -> dict[str, list[KPIGroupStat]]:
    by_group: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    by_category: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    for incident_id in ids:
        group, category = dims.get(incident_id, (None, None))
        for bucket in (by_group[group], by_category[category]):
            bucket[0] += incident_id in hits
            bucket[1] += 1
    return {
        "assignment_group": _ratio_breakdown(by_group),
        "category": _ratio_breakdown(by_category),
    }


async def _sla_response(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    return await _compliance(session, w, now, Incident.responded_at, Incident.response_due_at)


async def _sla_resolve(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    return await _compliance(session, w, now, Incident.resolved_at, Incident.resolve_due_at)


def open_at(t: datetime) -> ColumnElement[bool]:
    """``t`` anında açık olay koşulu (bkz. modül belgesi)."""
    return and_(
        Incident.created_at < t,
        Incident.status != IncidentStatus.CANCELLED,
        or_(
            Incident.resolved_at > t,
            and_(Incident.resolved_at.is_(None), Incident.status.not_in(_CLOSED_OR_CANCELLED)),
        ),
    )


AGING_BUCKETS: tuple[tuple[str, float], ...] = (
    ("0-1g", 1.0),
    ("1-3g", 3.0),
    ("3-7g", 7.0),
    ("7-30g", 30.0),
    ("30g+", math.inf),
)


def aging_bucket(age_seconds: float) -> str:
    days = max(age_seconds, 0.0) / 86400.0
    for label, upper in AGING_BUCKETS:
        if days < upper:
            return label
    return AGING_BUCKETS[-1][0]  # pragma: no cover


async def _backlog(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    t = min(w.end, now)
    stmt = select(
        Incident.priority,
        Incident.assignment_group,
        literal(t.timestamp()) - epoch_seconds(Incident.created_at),
    ).where(_IS_INCIDENT, open_at(t))
    rows = (await session.execute(stmt)).all()
    aging = {label: 0 for label, _ in AGING_BUCKETS}
    by_priority: dict[str | None, int] = defaultdict(int)
    by_group: dict[str | None, int] = defaultdict(int)
    ages: list[float] = []
    for priority, group, age in rows:
        age = float(age or 0.0)
        ages.append(age)
        aging[aging_bucket(age)] += 1
        by_priority[_key(priority)] += 1
        by_group[group] += 1

    def counts(groups: dict[str | None, int]) -> list[KPIGroupStat]:
        return [
            KPIGroupStat(key=k, value=float(v), count=v)
            for k, v in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
        ]

    return KPIResult(
        value=float(len(rows)),
        count=len(rows),
        details={
            "at": t.isoformat(),
            "aging": aging,
            "mean_age_hours": _hours(statistics.fmean(ages)) if ages else None,
            "oldest_age_hours": _hours(max(ages)) if ages else None,
        },
        breakdown={"priority": counts(by_priority), "assignment_group": counts(by_group)},
    )


async def _reopen(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    stmt = (
        select(AuditLog.entity_id, AuditLog.details)
        .where(
            AuditLog.entity_type == "incident",
            AuditLog.action == "incident.update",
            w.contains(AuditLog.occurred_at),
        )
        .order_by(AuditLog.seq)
    )
    resolved: set[str] = set()
    reopened: set[str] = set()
    for entity_id, details in (await session.execute(stmt)).all():
        if is_resolution(details):
            resolved.add(entity_id)
        elif is_reopen(details):
            reopened.add(entity_id)
    population = resolved | reopened
    dims = await _incident_dims(session, population)
    return KPIResult(
        value=_pct(len(reopened), len(population)),
        count=len(population),
        details={"reopened": len(reopened), "resolved": len(resolved)},
        breakdown=_dim_breakdown(population, reopened, dims) if population else {},
    )


async def _automation(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    touchless = case((ServiceRequest.touchless.is_(True), 1), else_=0)
    stmt = (
        select(ServiceRequest.item_key, func.count(), func.sum(touchless))
        .where(ServiceRequest.fulfilled_at.is_not(None), w.contains(ServiceRequest.fulfilled_at))
        .group_by(ServiceRequest.item_key)
    )
    groups: dict[str | None, list[int]] = {}
    total = hit = 0
    for item, n, t in (await session.execute(stmt)).all():
        n, t = int(n or 0), int(t or 0)
        groups[item] = [t, n]
        total += n
        hit += t
    return KPIResult(
        value=_pct(hit, total),
        count=total,
        details={"touchless": hit, "fulfilled": total},
        breakdown={"item_key": _ratio_breakdown(groups)},
    )


_TRIAGE_SOURCES = frozenset({"ai_triage", "ai_triage_approval"})


def _decision_fields(decision: AIDecision) -> set[str]:
    output = decision.output or {}
    if decision.outcome == DecisionOutcome.AUTO_APPLIED:
        fields = set(output.get("applied_fields") or [])
    else:
        fields = set((output.get("proposed") or {}).keys())
    if fields & {"impact", "urgency"}:
        fields.add("priority")
    return fields


async def _ai_acceptance(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    stmt = (
        select(AIDecision)
        .where(
            AIDecision.agent == "service_desk",
            AIDecision.action == "classify",
            AIDecision.incident_id.is_not(None),
            w.contains(AIDecision.created_at),
        )
        .order_by(AIDecision.created_at)
    )
    decisions = list((await session.scalars(stmt)).all())
    history = await _incident_history(session, sorted({d.incident_id for d in decisions}))
    outcomes: dict[str, int] = defaultdict(int)
    applied = overridden = accepted = 0
    accepted_ids: set[str] = set()
    for d in decisions:
        outcomes[DecisionOutcome(d.outcome).value] += 1
        is_applied = d.outcome == DecisionOutcome.AUTO_APPLIED or d.approved_by is not None
        if not is_applied:
            continue
        applied += 1
        fields = _decision_fields(d)
        since = to_utc(d.approved_at or d.created_at)
        human_edit = any(
            not str(e.actor).startswith("ai:")
            and (e.details or {}).get("source") not in _TRIAGE_SOURCES
            and to_utc(e.occurred_at) >= since
            and fields & set(_changes(e.details))
            for e in history.get(d.incident_id, [])
        )
        if human_edit:
            overridden += 1
        else:
            accepted += 1
            accepted_ids.add(d.id)
    dims = await _incident_dims(session, {d.incident_id for d in decisions})
    by_category: dict[str | None, list[int]] = defaultdict(lambda: [0, 0])
    for d in decisions:
        bucket = by_category[dims.get(d.incident_id, (None, None))[1]]
        bucket[0] += d.id in accepted_ids
        bucket[1] += 1
    return KPIResult(
        value=_pct(accepted, len(decisions)),
        count=len(decisions),
        details={
            "applied": applied,
            "accepted": accepted,
            "overridden": overridden,
            "not_applied": len(decisions) - applied,
            "override_rate": _pct(overridden, applied),
            "outcomes": dict(sorted(outcomes.items())),
        },
        breakdown={"category": _ratio_breakdown(by_category)} if decisions else {},
    )


async def _change_success(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    ok = case((ChangeRequest.implementation_outcome == "succeeded", 1), else_=0)
    stmt = (
        select(ChangeRequest.change_type, func.count(), func.sum(ok))
        .where(
            ChangeRequest.implementation_outcome.is_not(None),
            ChangeRequest.actual_end.is_not(None),
            w.contains(ChangeRequest.actual_end),
        )
        .group_by(ChangeRequest.change_type)
    )
    groups: dict[str | None, list[int]] = {}
    total = good = 0
    for change_type, n, s in (await session.execute(stmt)).all():
        n, s = int(n or 0), int(s or 0)
        groups[_key(change_type)] = [s, n]
        total += n
        good += s
    return KPIResult(
        value=_pct(good, total),
        count=total,
        details={"succeeded": good, "failed": total - good},
        breakdown={"change_type": _ratio_breakdown(groups)},
    )


async def _emergency_share(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    em = case((ChangeRequest.change_type == ChangeType.EMERGENCY, 1), else_=0)
    stmt = select(func.count(), func.sum(em)).where(w.contains(ChangeRequest.created_at))
    n, e = (await session.execute(stmt)).one()
    n, e = int(n or 0), int(e or 0)
    return KPIResult(value=_pct(e, n), count=n, details={"emergency": e, "total": n})


def _known_error_event(action: str, details: Any) -> bool:
    if not isinstance(details, dict):
        return False
    if action == "problem.transition":
        return details.get("to") == ProblemState.KNOWN_ERROR.value
    if action == "problem.known_error.publish":
        return details.get("from") in (ProblemState.NEW.value, ProblemState.INVESTIGATING.value)
    return False


async def _problem_ke(session: AsyncSession, w: Window, now: datetime) -> KPIResult:
    stmt = (
        select(AuditLog.entity_id, AuditLog.action, AuditLog.details, AuditLog.occurred_at)
        .where(
            AuditLog.entity_type == "problem",
            AuditLog.action.in_(["problem.transition", "problem.known_error.publish"]),
            w.contains(AuditLog.occurred_at),
        )
        .order_by(AuditLog.seq)
    )
    first: dict[str, datetime] = {}
    for entity_id, action, details, occurred in (await session.execute(stmt)).all():
        if _known_error_event(action, details) and entity_id not in first:
            first[entity_id] = to_utc(occurred)
    created: dict[str, datetime] = {}
    for part in _chunks(sorted(first)):
        rows = await session.execute(
            select(Problem.id, Problem.created_at).where(Problem.id.in_(list(part)))
        )
        created.update({pid: to_utc(ts) for pid, ts in rows.all()})
    leads = [(first[p] - created[p]).total_seconds() for p in first if p in created]
    stats = duration_stats(leads)
    return KPIResult(value=stats["mean_hours"], count=len(leads), details=stats)  # type: ignore[arg-type]


Calculator = Callable[[AsyncSession, Window, datetime], Awaitable[KPIResult]]
CALCULATORS: dict[str, Calculator] = {
    "mttr": _mttr,
    "mtta": _mtta,
    "fcr_rate": _fcr,
    "sla_response_compliance": _sla_response,
    "sla_resolve_compliance": _sla_resolve,
    "backlog": _backlog,
    "reopen_rate": _reopen,
    "automation_rate": _automation,
    "ai_triage_acceptance": _ai_acceptance,
    "change_success_rate": _change_success,
    "emergency_change_share": _emergency_share,
    "problem_known_error_lead_time": _problem_ke,
}
assert set(CALCULATORS) == set(DEFINITIONS)


# --- rapor ------------------------------------------------------------------------------------


def trend(better: str, current: float | None, previous: float | None) -> str:
    if current is None or previous is None:
        return "n/a"
    if math.isclose(current, previous, abs_tol=1e-9):
        return "flat"
    lower_is_better = better == "lower"
    return "improved" if (current < previous) == lower_is_better else "worsened"


def compare(defn: KPIDefinition, cur: KPIResult, prev: KPIResult) -> KPIValueRead:
    delta = delta_pct = None
    if cur.value is not None and prev.value is not None:
        delta = round(cur.value - prev.value, 3)
        if prev.value != 0:
            delta_pct = round(100.0 * (cur.value - prev.value) / abs(prev.value), 2)
    return KPIValueRead(
        key=defn.key,
        name=defn.name,
        unit=defn.unit,  # type: ignore[arg-type]
        better=defn.better,  # type: ignore[arg-type]
        value=cur.value,
        count=cur.count,
        previous=prev.value,
        previous_count=prev.count,
        delta=delta,
        delta_pct=delta_pct,
        trend=trend(defn.better, cur.value, prev.value),  # type: ignore[arg-type]
        details=cur.details,
        breakdown=cur.breakdown,
    )


async def compute(
    session: AsyncSession, key: str, window: Window, now: datetime | None = None
) -> KPIResult:
    """Tek bir KPI'yı tek pencerede hesaplar."""
    if key not in CALCULATORS:
        raise ValueError(f"Bilinmeyen KPI: {key}")
    return await CALCULATORS[key](session, window, to_utc(now or datetime.now(UTC)))


async def report(
    session: AsyncSession,
    *,
    end: datetime | None = None,
    days: int = DEFAULT_DAYS,
    keys: Sequence[str] | None = None,
    now: datetime | None = None,
) -> KPIReportRead:
    """KPI raporu: geçerli pencere ``[end - days, end)`` ve önceki eşit pencereyle eğilim."""
    now = to_utc(now or datetime.now(UTC))
    window = Window.ending(end or now, days)
    previous = window.previous()
    wanted = list(dict.fromkeys(keys)) if keys else list(KPI_KEYS)
    unknown = [k for k in wanted if k not in DEFINITIONS]
    if unknown:
        raise ValueError(f"Bilinmeyen KPI: {', '.join(unknown)}")
    kpis = []
    for key in wanted:
        cur = await compute(session, key, window, now)
        prev = await compute(session, key, previous, now)
        kpis.append(compare(DEFINITIONS[key], cur, prev))
    return KPIReportRead(
        window=window.read(), previous_window=previous.read(), generated_at=now, kpis=kpis
    )


async def sla_report(
    session: AsyncSession,
    *,
    end: datetime | None = None,
    days: int = DEFAULT_DAYS,
    now: datetime | None = None,
) -> KPIReportRead:
    """Yanıt + çözüm SLA uyumu (öncelik ve grup kırılımlı)."""
    return await report(session, end=end, days=days, keys=SLA_KPI_KEYS, now=now)

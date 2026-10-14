"""CMDB servis katmanı: yapılandırma öğeleri (CI), ilişkiler ve etki analizi.

- CI sahibi (owner_group) taxonomy.json `assignment_group` anahtarı olmalıdır.
- Aynı ortamda aynı adlı iki CI olamaz.
- İlişki: kaynak <tür> hedef; öz döngü (kaynak == hedef) ve aynı (kaynak, hedef, tür) reddedilir.
- Etki analizi "bu CI değişirse kim bozulur" sorusunu yanıtlar (yukarı yönlü bağımlılar):
  depends_on / runs_on / connects_to'da hedef değişince kaynak, part_of'ta parça (kaynak)
  değişince bütün (hedef) etkilenir. Döngüler güvenlidir (her CI bir kez, en kısa yoldan).
- Fonksiyonlar flush eder, commit etmez; her yazma denetim zincirine kaydedilir.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, taxonomy
from serviszeka.domain.enums import CIClass, CIEnvironment, CIRelationshipType, CIStatus
from serviszeka.models import CIRelationship, ConfigurationItem
from serviszeka.services.cmdb_schemas import CICreate, CIUpdate

R = CIRelationshipType

# İlişki türü -> etki yönü. "reverse": hedef değişince kaynak etkilenir; "forward": tersi.
_REVERSE_TYPES: frozenset[CIRelationshipType] = frozenset({R.DEPENDS_ON, R.RUNS_ON, R.CONNECTS_TO})
_FORWARD_TYPES: frozenset[CIRelationshipType] = frozenset({R.PART_OF})

MAX_IMPACT_DEPTH = 20
# Etki analizinin raporladığı en fazla etkilenen CI (büyük grafikte bellek/sorgu sınırı).
# Sınıra ulaşılırsa `truncated=True`; risk hesabı görülmeyenleri en kritik kabul eder.
MAX_IMPACT_NODES = 1000
_NON_NULLABLE = frozenset(
    {"name", "ci_class", "environment", "criticality", "status", "description", "attributes",
     "external_refs"}
)


class CMDBError(Exception):
    """CMDB servis hatalarının tabanı."""


class CINotFoundError(CMDBError, LookupError):
    def __init__(self, ci_id: str) -> None:
        super().__init__(f"Yapılandırma öğesi bulunamadı: {ci_id}")
        self.ci_id = ci_id


class RelationshipNotFoundError(CMDBError, LookupError):
    def __init__(self, rel_id: str) -> None:
        super().__init__(f"İlişki bulunamadı: {rel_id}")
        self.rel_id = rel_id


class CMDBValidationError(CMDBError, ValueError):
    """Geçersiz CI/ilişki verisi (öz döngü, taksonomi dışı sahip ekip, yinelenen ad...)."""


class DuplicateRelationshipError(CMDBError):
    def __init__(self) -> None:
        super().__init__("Bu ilişki zaten tanımlı.")


class VersionConflictError(CMDBError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Sürüm çakışması: beklenen {expected}, güncel {actual}. Kaydı yenileyip tekrar deneyin."
        )
        self.expected = expected
        self.actual = actual


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _check_owner_group(owner_group: str | None) -> None:
    if owner_group is not None and owner_group not in taxonomy.allowed("assignment_group"):
        raise CMDBValidationError(
            f"Sahip ekip (owner_group) sınıf listesinde yok: {owner_group}"
        )


async def _check_unique_name(
    session: AsyncSession, name: str, environment: CIEnvironment, exclude_id: str | None = None
) -> None:
    stmt = select(ConfigurationItem.id).where(
        ConfigurationItem.name == name, ConfigurationItem.environment == environment
    )
    if exclude_id is not None:
        stmt = stmt.where(ConfigurationItem.id != exclude_id)
    if await session.scalar(stmt.limit(1)) is not None:
        raise CMDBValidationError(
            f"'{name}' adlı yapılandırma öğesi {CIEnvironment(environment).value} ortamında zaten var."
        )


# --- CI CRUD ------------------------------------------------------------------------


async def create_ci(session: AsyncSession, data: CICreate, actor: str) -> ConfigurationItem:
    _check_owner_group(data.owner_group)
    await _check_unique_name(session, data.name, data.environment)
    now = _now()
    ci = ConfigurationItem(**data.model_dump(), version=1, created_at=now, updated_at=now)
    session.add(ci)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="cmdb.ci.create",
        entity_type="configuration_item",
        entity_id=ci.id,
        details={
            "name": ci.name,
            "ci_class": ci.ci_class.value,
            "environment": ci.environment.value,
            "criticality": ci.criticality,
            "version": ci.version,
        },
    )
    return ci


async def get_ci(session: AsyncSession, ci_id: str) -> ConfigurationItem | None:
    return await session.get(ConfigurationItem, ci_id)


async def get_cis(session: AsyncSession, ci_ids: Iterable[str]) -> list[ConfigurationItem]:
    """Verilen id'lerin hepsini (sırasını koruyarak, yinelenenler atılarak) döndürür."""
    wanted = list(dict.fromkeys(ci_ids))
    if not wanted:
        return []
    rows = (
        await session.scalars(select(ConfigurationItem).where(ConfigurationItem.id.in_(wanted)))
    ).all()
    by_id = {r.id: r for r in rows}
    for ci_id in wanted:
        if ci_id not in by_id:
            raise CINotFoundError(ci_id)
    return [by_id[i] for i in wanted]


async def list_cis(
    session: AsyncSession,
    *,
    ci_class: CIClass | str | None = None,
    environment: CIEnvironment | str | None = None,
    status: CIStatus | str | None = None,
    owner_group: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ConfigurationItem]:
    stmt = select(ConfigurationItem)
    if ci_class is not None:
        stmt = stmt.where(ConfigurationItem.ci_class == CIClass(ci_class))
    if environment is not None:
        stmt = stmt.where(ConfigurationItem.environment == CIEnvironment(environment))
    if status is not None:
        stmt = stmt.where(ConfigurationItem.status == CIStatus(status))
    if owner_group is not None:
        stmt = stmt.where(ConfigurationItem.owner_group == owner_group)
    if q:
        stmt = stmt.where(func.lower(ConfigurationItem.name).contains(q.lower()))
    stmt = stmt.order_by(ConfigurationItem.name, ConfigurationItem.environment)
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


async def update_ci(
    session: AsyncSession,
    ci_id: str,
    data: CIUpdate,
    actor: str,
    expected_version: int | None = None,
) -> ConfigurationItem:
    ci = await get_ci(session, ci_id)
    if ci is None:
        raise CINotFoundError(ci_id)
    db_version = await session.scalar(
        select(ConfigurationItem.version).where(ConfigurationItem.id == ci_id).with_for_update()
    )
    if db_version != ci.version:
        await session.refresh(ci)
    if expected_version is not None and expected_version != ci.version:
        raise VersionConflictError(expected_version, ci.version)

    changes = {
        k: v
        for k, v in data.model_dump(exclude_unset=True).items()
        if not (v is None and k in _NON_NULLABLE) and k != "expected_version"
    }
    if "owner_group" in changes:
        _check_owner_group(changes["owner_group"])
    if "name" in changes or "environment" in changes:
        await _check_unique_name(
            session,
            changes.get("name", ci.name),
            changes.get("environment", ci.environment),
            exclude_id=ci.id,
        )
    diff: dict[str, list[Any]] = {}
    for name, value in changes.items():
        old = getattr(ci, name)
        if old != value:
            diff[name] = [_jsonable(old), _jsonable(value)]
            setattr(ci, name, value)
    if not diff:
        return ci
    ci.version = (ci.version or 0) + 1
    ci.updated_at = _now()
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="cmdb.ci.update",
        entity_type="configuration_item",
        entity_id=ci.id,
        details={"changes": diff, "version": ci.version},
    )
    return ci


# --- ilişkiler ------------------------------------------------------------------------


async def add_relationship(
    session: AsyncSession,
    source_id: str,
    target_id: str,
    rel_type: CIRelationshipType | str,
    actor: str,
) -> CIRelationship:
    rel_type = CIRelationshipType(rel_type)
    if source_id == target_id:
        raise CMDBValidationError("Bir yapılandırma öğesi kendisiyle ilişkilendirilemez.")
    await get_cis(session, [source_id, target_id])
    existing = await session.scalar(
        select(CIRelationship.id).where(
            CIRelationship.source_id == source_id,
            CIRelationship.target_id == target_id,
            CIRelationship.type == rel_type,
        )
    )
    if existing is not None:
        raise DuplicateRelationshipError()
    rel = CIRelationship(
        source_id=source_id, target_id=target_id, type=rel_type, created_by=actor,
        created_at=_now(),
    )
    session.add(rel)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="cmdb.relationship.create",
        entity_type="ci_relationship",
        entity_id=rel.id,
        details={"source_id": source_id, "target_id": target_id, "type": rel_type.value},
    )
    return rel


async def remove_relationship(session: AsyncSession, rel_id: str, actor: str) -> None:
    rel = await session.get(CIRelationship, rel_id)
    if rel is None:
        raise RelationshipNotFoundError(rel_id)
    details = {"source_id": rel.source_id, "target_id": rel.target_id, "type": rel.type.value}
    await session.delete(rel)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="cmdb.relationship.delete",
        entity_type="ci_relationship",
        entity_id=rel_id,
        details=details,
    )


async def list_relationships(session: AsyncSession, ci_id: str) -> list[CIRelationship]:
    """CI'ın kaynak ya da hedef olduğu tüm ilişkiler."""
    if await get_ci(session, ci_id) is None:
        raise CINotFoundError(ci_id)
    stmt = (
        select(CIRelationship)
        .where(or_(CIRelationship.source_id == ci_id, CIRelationship.target_id == ci_id))
        .order_by(CIRelationship.created_at, CIRelationship.id)
    )
    return list((await session.scalars(stmt)).all())


# --- etki analizi ---------------------------------------------------------------------


@dataclass
class ImpactedCI:
    ci: ConfigurationItem
    depth: int
    path: list[str]  # kök CI id'sinden bu CI'a (ikisi dahil)
    relationship: CIRelationshipType


@dataclass
class ImpactAnalysis:
    root_ci_ids: list[str]
    roots: list[ConfigurationItem]
    affected: list[ImpactedCI] = field(default_factory=list)
    highest_criticality: int | None = None
    business_services: list[ConfigurationItem] = field(default_factory=list)
    # MAX_IMPACT_NODES sınırında kesildi mi (etkilenenler eksik olabilir)
    truncated: bool = False


async def _dependents(
    session: AsyncSession, frontier: Sequence[str]
) -> list[tuple[str, str, CIRelationshipType]]:
    """(değişen, etkilenen, ilişki) üçlüleri: frontier'daki CI'lar değişirse etkilenenler."""
    stmt = select(CIRelationship).where(
        or_(
            CIRelationship.target_id.in_(frontier) & CIRelationship.type.in_(list(_REVERSE_TYPES)),
            CIRelationship.source_id.in_(frontier) & CIRelationship.type.in_(list(_FORWARD_TYPES)),
        )
    )
    edges: list[tuple[str, str, CIRelationshipType]] = []
    for rel in (await session.scalars(stmt)).all():
        rel_type = CIRelationshipType(rel.type)
        if rel_type in _REVERSE_TYPES:
            edges.append((rel.target_id, rel.source_id, rel_type))
        else:
            edges.append((rel.source_id, rel.target_id, rel_type))
    # Deterministik sıra: aynı derinlikte en kısa/ilk yol seçilsin
    return sorted(edges, key=lambda e: (e[0], e[1], e[2].value))


async def impact_analysis(
    session: AsyncSession, ci_ids: Sequence[str], max_depth: int = 5
) -> ImpactAnalysis:
    """Verilen CI'lar değişirse etkilenecek CI'lar (genişlik öncelikli, döngü güvenli).

    Her etkilenen CI en yakın kökten, en kısa yoldan bir kez raporlanır; kökler etkilenen
    listesinde yer almaz. `highest_criticality` kökler + etkilenenler içindeki en küçük sayıdır.
    """
    if not 1 <= max_depth <= MAX_IMPACT_DEPTH:
        raise CMDBValidationError(f"max_depth 1 ile {MAX_IMPACT_DEPTH} arasında olmalı.")
    roots = await get_cis(session, ci_ids)
    root_ids = [r.id for r in roots]
    paths: dict[str, list[str]] = {r: [r] for r in root_ids}
    found: list[tuple[str, int, list[str], CIRelationshipType]] = []
    frontier = list(root_ids)
    depth = 0
    limit = MAX_IMPACT_NODES
    truncated = False
    while frontier and depth < max_depth and not truncated:
        depth += 1
        next_frontier: list[str] = []
        for changed, affected, rel_type in await _dependents(session, frontier):
            if affected in paths:  # ziyaret edildi (kök ya da daha kısa yol) — döngü güvenliği
                continue
            if len(found) >= limit:
                truncated = True
                break
            path = [*paths[changed], affected]
            paths[affected] = path
            found.append((affected, depth, path, rel_type))
            next_frontier.append(affected)
        frontier = next_frontier

    cis = {c.id: c for c in await get_cis(session, [f[0] for f in found])}
    affected_items = [ImpactedCI(cis[i], d, p, t) for i, d, p, t in found]
    everything = [*roots, *(a.ci for a in affected_items)]
    return ImpactAnalysis(
        root_ci_ids=root_ids,
        roots=roots,
        affected=affected_items,
        highest_criticality=min((c.criticality for c in everything), default=None),
        business_services=[c for c in everything if c.ci_class == CIClass.SERVICE],
        truncated=truncated,
    )

"""ServiceNow CMDB çekimi: /api/now/table/cmdb_ci (seçili sınıflar) ve cmdb_rel_ci.

- Uzak CI'lar `sys_id` ile yerel ConfigurationItem'a eşlenir (`cmdb_external_refs`, tekil);
  eşleme yoksa aynı ad + ortamdaki, başka bir ServiceNow kaydına bağlı olmayan yerel CI
  sahiplenilir; yoksa yeni CI açılır. Yazmalar `services.cmdb` üzerinden yapılır (doğrulama,
  sürüm, denetim kaydı).
- Artımlı: her varlık türü için en son içe alınan `sys_updated_on` filigrandır; sorgu `>=`
  kullanır (aynı saniyedeki kayıtlar kaçmasın), değişmemiş kayıt sürüm artırmaz. `full=True`
  filigranı yok sayar.
- İlişkiler, iki ucu da eşlenmiş CI'lar arasında ve desteklenen türdeyse içe alınır.
- Silme yayılmaz: ServiceNow'da emekliye ayrılan CI (install_status 7) yerelde `retired` olur;
  uzakta silinen ilişki yerelde kalır (bkz. docs/faz2.md).
- Fonksiyonlar flush eder, commit etmez.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, taxonomy
from serviszeka.domain.enums import (
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    CIStatus,
    ExternalSystem,
)
from serviszeka.integrations.base import ConnectorError
from serviszeka.integrations.cmdb_base import (
    CMDBSourceConnector,
    RemoteCI,
    RemoteCIRelationship,
)
from serviszeka.models import CIRelationship, CMDBExternalRef, ConfigurationItem
from serviszeka.services import cmdb
from serviszeka.services.cmdb_schemas import CICreate, CIUpdate

CI_TABLE_PATH = "/api/now/table/cmdb_ci"
REL_TABLE_PATH = "/api/now/table/cmdb_rel_ci"
SYNC_ACTOR = "system:cmdb_sync"
ENTITY_CI = "ci"
ENTITY_RELATIONSHIP = "relationship"

# Seçili ServiceNow CI sınıfları -> CIClass
CLASS_MAP: dict[str, CIClass] = {
    "cmdb_ci_service": CIClass.SERVICE,
    "cmdb_ci_service_business": CIClass.SERVICE,
    "cmdb_ci_appl": CIClass.APPLICATION,
    "cmdb_ci_server": CIClass.SERVER,
    "cmdb_ci_linux_server": CIClass.SERVER,
    "cmdb_ci_win_server": CIClass.SERVER,
    "cmdb_ci_unix_server": CIClass.SERVER,
    "cmdb_ci_db_instance": CIClass.DATABASE,
    "cmdb_ci_db_mssql_instance": CIClass.DATABASE,
    "cmdb_ci_db_ora_instance": CIClass.DATABASE,
    "cmdb_ci_db_mysql_instance": CIClass.DATABASE,
    "cmdb_ci_db_postgresql_instance": CIClass.DATABASE,
    "cmdb_ci_netgear": CIClass.NETWORK_DEVICE,
    "cmdb_ci_ip_router": CIClass.NETWORK_DEVICE,
    "cmdb_ci_ip_switch": CIClass.NETWORK_DEVICE,
    "cmdb_ci_ip_firewall": CIClass.NETWORK_DEVICE,
    "cmdb_ci_vm_instance": CIClass.CLOUD_RESOURCE,
    "cmdb_ci_cloud_service_account": CIClass.CLOUD_RESOURCE,
}

# install_status -> CIStatus (1 Installed, 2 On Order, 3 In Maintenance, 4 Pending Install,
# 5 Pending Repair, 6 In Stock, 7 Retired, 8 Stolen, 100 Absent)
INSTALL_STATUS_MAP: dict[str, CIStatus] = {
    "1": CIStatus.ACTIVE,
    "2": CIStatus.PLANNED,
    "3": CIStatus.MAINTENANCE,
    "4": CIStatus.PLANNED,
    "5": CIStatus.MAINTENANCE,
    "6": CIStatus.PLANNED,
    "7": CIStatus.RETIRED,
    "8": CIStatus.RETIRED,
    "100": CIStatus.RETIRED,
}

# used_for / environment -> CIEnvironment; bilinmeyen değer ihtiyatla prod sayılır
ENVIRONMENT_MAP: dict[str, CIEnvironment] = {
    "production": CIEnvironment.PROD,
    "prod": CIEnvironment.PROD,
    "disaster recovery": CIEnvironment.PROD,
    "staging": CIEnvironment.TEST,
    "qa": CIEnvironment.TEST,
    "test": CIEnvironment.TEST,
    "demonstration": CIEnvironment.TEST,
    "training": CIEnvironment.TEST,
    "development": CIEnvironment.DEV,
    "dev": CIEnvironment.DEV,
}

# cmdb_rel_type "Ebeveyn tanımı::Çocuk tanımı" -> (yerel tür, kaynak ebeveyn mi?)
# ServiceNow'da "ebeveyn <tanım> çocuk": "A Depends on B" -> A depends_on B;
# "A Contains B" -> B part_of A (kaynak çocuk).
RELATIONSHIP_TYPE_MAP: dict[str, tuple[CIRelationshipType, bool]] = {
    "depends on::used by": (CIRelationshipType.DEPENDS_ON, True),
    "uses::used by": (CIRelationshipType.DEPENDS_ON, True),
    "runs on::runs": (CIRelationshipType.RUNS_ON, True),
    "hosted on::hosts": (CIRelationshipType.RUNS_ON, True),
    "virtualized by::virtualizes": (CIRelationshipType.RUNS_ON, True),
    "connects to::connected by": (CIRelationshipType.CONNECTS_TO, True),
    "contains::contained by": (CIRelationshipType.PART_OF, False),
    "members::member of": (CIRelationshipType.PART_OF, False),
}

_CI_FIELDS = (
    "sys_id,name,sys_class_name,install_status,used_for,environment,business_criticality,"
    "busines_criticality,short_description,support_group,sys_updated_on"
)
_REL_FIELDS = "sys_id,parent,child,type,sys_updated_on"
_CRIT_RE = re.compile(r"^\s*([1-4])")


def _value(field: Any) -> str | None:
    v = field.get("value") if isinstance(field, dict) else field
    return None if v in (None, "") else str(v)


def _display(field: Any) -> str | None:
    v = (field.get("display_value") or field.get("value")) if isinstance(field, dict) else field
    return None if v in (None, "") else str(v)


def _criticality(rec: dict[str, Any]) -> int:
    # cmdb_ci_service alanının ServiceNow'daki adı "busines_criticality" (tek s)
    for key in ("busines_criticality", "business_criticality"):
        for raw in (_value(rec.get(key)), _display(rec.get(key))):
            if raw and (m := _CRIT_RE.match(raw)):
                return int(m.group(1))
    return 3


def _environment(rec: dict[str, Any]) -> CIEnvironment:
    for key in ("environment", "used_for"):
        for raw in (_value(rec.get(key)), _display(rec.get(key))):
            if raw and raw.strip().lower() in ENVIRONMENT_MAP:
                return ENVIRONMENT_MAP[raw.strip().lower()]
    return CIEnvironment.PROD


def record_to_ci(rec: dict[str, Any]) -> RemoteCI | None:
    """cmdb_ci kaydı -> RemoteCI; seçili sınıflardan değilse None."""
    cls = _value(rec.get("sys_class_name"))
    ci_class = CLASS_MAP.get(cls or "")
    if ci_class is None:
        return None
    status = INSTALL_STATUS_MAP.get(_value(rec.get("install_status")) or "", CIStatus.ACTIVE)
    group_display = _display(rec.get("support_group"))
    groups = taxonomy.allowed("assignment_group")
    owner_group = next(
        (g for g in (group_display, _value(rec.get("support_group"))) if g and g in groups), None
    )
    attributes: dict[str, Any] = {"servicenow_class": cls}
    if group_display:
        attributes["servicenow_support_group"] = group_display
    return RemoteCI(
        external_id=_value(rec.get("sys_id")) or "",
        name=(_display(rec.get("name")) or "").strip()[:200],
        ci_class=ci_class,
        environment=_environment(rec),
        criticality=_criticality(rec),
        status=status,
        owner_group=owner_group,
        description=_display(rec.get("short_description")) or "",
        attributes=attributes,
        updated_at=_value(rec.get("sys_updated_on")),
        raw=rec,
    )


def record_to_relationship(rec: dict[str, Any]) -> RemoteCIRelationship:
    """cmdb_rel_ci kaydı -> RemoteCIRelationship (desteklenmeyen türde type=None)."""
    parent = _value(rec.get("parent")) or ""
    child = _value(rec.get("child")) or ""
    remote_type = _display(rec.get("type")) or ""
    mapped = RELATIONSHIP_TYPE_MAP.get(remote_type.strip().lower())
    rel_type, source_is_parent = mapped if mapped else (None, True)
    source, target = (parent, child) if source_is_parent else (child, parent)
    return RemoteCIRelationship(
        external_id=_value(rec.get("sys_id")) or "",
        source_external_id=source,
        target_external_id=target,
        type=rel_type,
        remote_type=remote_type,
        updated_at=_value(rec.get("sys_updated_on")),
        raw=rec,
    )


class ServiceNowCMDBConnector:
    """Salt okunur ServiceNow CMDB bağlayıcısı (Table API, temel kimlik doğrulama)."""

    system = ExternalSystem.SERVICENOW

    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        classes: tuple[str, ...] = tuple(CLASS_MAP),
        page_size: int = 200,
        timeout: float = 30.0,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.classes = classes
        self.page_size = page_size
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.instance_url,
            auth=httpx.BasicAuth(username, password),
            headers={"Accept": "application/json"},
            transport=transport,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        try:
            resp = await self.client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"ServiceNow'a ulaşılamadı: {exc}") from exc
        if resp.status_code >= 400:
            raise ConnectorError(
                f"ServiceNow GET {path} başarısız: {resp.status_code} {resp.text[:300]}"
            )
        return list(resp.json().get("result", []))

    async def _list(
        self, path: str, fields: str, filters: list[str], since: str | None
    ) -> list[dict[str, Any]]:
        parts = list(filters)
        if since:
            # >=: aynı saniyede güncellenen kayıtlar kaçmasın; tekrarlar upsert'te etkisizdir
            parts.append(f"sys_updated_on>={since}")
        query = "^".join([*parts, "ORDERBYsys_updated_on"])
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            batch = await self._get(
                path,
                {
                    "sysparm_query": query,
                    "sysparm_fields": fields,
                    "sysparm_display_value": "all",
                    "sysparm_exclude_reference_link": "true",
                    "sysparm_limit": str(self.page_size),
                    "sysparm_offset": str(offset),
                },
            )
            rows.extend(batch)
            if len(batch) < self.page_size:
                return rows
            offset += self.page_size

    async def list_cis_updated_since(self, since: str | None) -> list[RemoteCI]:
        rows = await self._list(
            CI_TABLE_PATH, _CI_FIELDS, [f"sys_class_nameIN{','.join(self.classes)}"], since
        )
        return [ci for r in rows if (ci := record_to_ci(r)) is not None]

    async def list_relationships_updated_since(
        self, since: str | None
    ) -> list[RemoteCIRelationship]:
        rows = await self._list(REL_TABLE_PATH, _REL_FIELDS, [], since)
        return [record_to_relationship(r) for r in rows]


# --- çekim (upsert) ---------------------------------------------------------------------


async def _watermark(session: AsyncSession, system: ExternalSystem, entity_type: str) -> str | None:
    return await session.scalar(
        select(func.max(CMDBExternalRef.remote_updated)).where(
            CMDBExternalRef.system == system, CMDBExternalRef.entity_type == entity_type
        )
    )


async def _get_ref(
    session: AsyncSession, system: ExternalSystem, entity_type: str, external_id: str
) -> CMDBExternalRef | None:
    return await session.scalar(
        select(CMDBExternalRef).where(
            CMDBExternalRef.system == system,
            CMDBExternalRef.entity_type == entity_type,
            CMDBExternalRef.external_id == external_id,
        )
    )


async def _save_ref(
    session: AsyncSession,
    ref: CMDBExternalRef | None,
    *,
    system: ExternalSystem,
    entity_type: str,
    external_id: str,
    entity_id: str,
    remote_updated: str | None,
) -> None:
    now = datetime.now(UTC)
    if ref is None:
        session.add(
            CMDBExternalRef(
                system=system,
                entity_type=entity_type,
                external_id=external_id,
                entity_id=entity_id,
                remote_updated=remote_updated,
                last_synced_at=now,
            )
        )
    else:
        ref.entity_id = entity_id
        ref.remote_updated = remote_updated
        ref.last_synced_at = now
    await session.flush()


async def _upsert_ci(
    session: AsyncSession, system: ExternalSystem, remote: RemoteCI, actor: str
) -> tuple[str, str | None]:
    """('created'|'updated'|'unchanged', yerel id) döndürür; çakışmada CMDBValidationError."""
    key = system.value
    ref = await _get_ref(session, system, ENTITY_CI, remote.external_id)
    ci = await session.get(ConfigurationItem, ref.entity_id) if ref else None
    if ci is None:
        # Eşlenmemiş yerel CI (aynı ad + ortam) sahiplenilir; başka uzak kayda bağlıysa çakışma
        ci = await session.scalar(
            select(ConfigurationItem).where(
                ConfigurationItem.name == remote.name,
                ConfigurationItem.environment == remote.environment,
            )
        )
        if ci is not None and (ci.external_refs or {}).get(key) not in (None, remote.external_id):
            raise cmdb.CMDBValidationError(
                f"'{remote.name}' adlı CI başka bir ServiceNow kaydına "
                f"({ci.external_refs[key]}) bağlı; içe alınmadı."
            )
    if ci is None:
        created = await cmdb.create_ci(
            session,
            CICreate(
                name=remote.name,
                ci_class=remote.ci_class,
                environment=remote.environment,
                criticality=remote.criticality,
                owner_group=remote.owner_group,
                status=remote.status,
                description=remote.description,
                attributes=remote.attributes,
                external_refs={key: remote.external_id},
            ),
            actor,
        )
        outcome, ci_id = "created", created.id
    else:
        fields: dict[str, Any] = {
            "name": remote.name,
            "ci_class": remote.ci_class,
            "environment": remote.environment,
            "criticality": remote.criticality,
            "status": remote.status,
            "description": remote.description,
            # Yerel öznitelikler korunur, uzak olanlar güncellenir
            "attributes": {**(ci.attributes or {}), **remote.attributes},
            "external_refs": {**(ci.external_refs or {}), key: remote.external_id},
        }
        if remote.owner_group is not None:  # eşlenemeyen destek grubu yerel sahibi silmez
            fields["owner_group"] = remote.owner_group
        before = ci.version
        updated = await cmdb.update_ci(session, ci.id, CIUpdate(**fields), actor)
        outcome = "updated" if updated.version != before else "unchanged"
        ci_id = updated.id
    await _save_ref(
        session,
        ref,
        system=system,
        entity_type=ENTITY_CI,
        external_id=remote.external_id,
        entity_id=ci_id,
        remote_updated=remote.updated_at,
    )
    return outcome, ci_id


async def _upsert_relationship(
    session: AsyncSession, system: ExternalSystem, remote: RemoteCIRelationship, actor: str
) -> str:
    """'created'|'updated'|'unchanged'|'skipped'."""
    if remote.type is None:
        return "skipped"
    src = await _get_ref(session, system, ENTITY_CI, remote.source_external_id)
    tgt = await _get_ref(session, system, ENTITY_CI, remote.target_external_id)
    if src is None or tgt is None or src.entity_id == tgt.entity_id:
        return "skipped"
    desired = (src.entity_id, tgt.entity_id, remote.type)
    ref = await _get_ref(session, system, ENTITY_RELATIONSHIP, remote.external_id)
    rel = await session.get(CIRelationship, ref.entity_id) if ref else None
    if rel is not None and (rel.source_id, rel.target_id, CIRelationshipType(rel.type)) == desired:
        outcome, rel_id = "unchanged", rel.id
    else:
        if rel is not None:
            await cmdb.remove_relationship(session, rel.id, actor)
        existing = await session.scalar(
            select(CIRelationship).where(
                CIRelationship.source_id == desired[0],
                CIRelationship.target_id == desired[1],
                CIRelationship.type == desired[2],
            )
        )
        if existing is not None:
            rel_id = existing.id
        else:
            rel_id = (await cmdb.add_relationship(session, *desired, actor=actor)).id
        outcome = "updated" if rel is not None else ("unchanged" if existing else "created")
    await _save_ref(
        session,
        ref,
        system=system,
        entity_type=ENTITY_RELATIONSHIP,
        external_id=remote.external_id,
        entity_id=rel_id,
        remote_updated=remote.updated_at,
    )
    return outcome


def _counts(counter: Counter[str]) -> dict[str, int]:
    return {k: counter.get(k, 0) for k in ("created", "updated", "unchanged", "skipped")}


async def pull_cmdb(
    session: AsyncSession,
    connector: CMDBSourceConnector,
    *,
    actor: str,
    full: bool = False,
    sync_actor: str = SYNC_ACTOR,
) -> dict[str, Any]:
    """Uzak CMDB'den CI'ları ve ilişkileri içe alır (önce CI'lar). ConnectorError yayılır.

    `actor` çekimi başlatan kullanıcıdır (özet denetim kaydı); kayıt düzeyindeki yazmalar
    `sync_actor` adına yapılır.
    """
    system = connector.system
    since_ci = None if full else await _watermark(session, system, ENTITY_CI)
    since_rel = None if full else await _watermark(session, system, ENTITY_RELATIONSHIP)
    remote_cis = await connector.list_cis_updated_since(since_ci)
    remote_rels = await connector.list_relationships_updated_since(since_rel)

    ci_counts: Counter[str] = Counter()
    rel_counts: Counter[str] = Counter()
    errors: list[dict[str, str]] = []
    for remote in remote_cis:
        if not remote.external_id or not remote.name:
            ci_counts["skipped"] += 1
            continue
        try:
            outcome, _ = await _upsert_ci(session, system, remote, sync_actor)
        except cmdb.CMDBError as exc:
            ci_counts["skipped"] += 1
            errors.append({"entity_type": ENTITY_CI, "external_id": remote.external_id,
                           "message": str(exc)})
            continue
        ci_counts[outcome] += 1

    for remote_rel in remote_rels:
        try:
            outcome = await _upsert_relationship(session, system, remote_rel, sync_actor)
        except cmdb.CMDBError as exc:
            outcome = "skipped"
            errors.append({"entity_type": ENTITY_RELATIONSHIP,
                           "external_id": remote_rel.external_id, "message": str(exc)})
        rel_counts[outcome] += 1

    result: dict[str, Any] = {
        "system": system.value,
        "full": full,
        "since": {ENTITY_CI: since_ci, ENTITY_RELATIONSHIP: since_rel},
        "cis": _counts(ci_counts),
        "relationships": _counts(rel_counts),
        "errors": errors,
    }
    await audit.record(
        session,
        actor=actor,
        action="integration.cmdb_pull",
        entity_type="integration",
        entity_id=system.value,
        details={k: result[k] for k in ("full", "since", "cis", "relationships")}
        | {"error_count": len(errors)},
    )
    return result


__all__ = [
    "CI_TABLE_PATH",
    "CLASS_MAP",
    "RELATIONSHIP_TYPE_MAP",
    "REL_TABLE_PATH",
    "ServiceNowCMDBConnector",
    "pull_cmdb",
    "record_to_ci",
    "record_to_relationship",
]

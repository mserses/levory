"""Harici CMDB kaynakları için ortak tipler: normalize CI/ilişki ve bağlayıcı protokolü.

ServiceNow (`servicenow_cmdb.py`) bu protokolü uygular. Jira Service Management tarafında
CMDB (Assets) ücretli bir eklentidir; yalnızca arayüz taslağı vardır (`jira_assets.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from serviszeka.domain.enums import (
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    CIStatus,
    ExternalSystem,
)


@dataclass
class RemoteCI:
    """Harici CMDB'deki CI'ın ServisZeka alanlarına normalize edilmiş hali."""

    external_id: str  # ServiceNow sys_id
    name: str
    ci_class: CIClass
    environment: CIEnvironment = CIEnvironment.PROD
    criticality: int = 3
    status: CIStatus = CIStatus.ACTIVE
    # Yalnızca taksonomi assignment_group anahtarıyla eşleşirse dolu
    owner_group: str | None = None
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None  # uzak sistemin kendi zaman damgası (olduğu gibi)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteCIRelationship:
    """Normalize ilişki: kaynak <type> hedef (yerel etki yönü kuralına göre çevrilmiş)."""

    external_id: str
    source_external_id: str
    target_external_id: str
    type: CIRelationshipType | None  # None: desteklenmeyen uzak ilişki türü
    remote_type: str = ""
    updated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CMDBSourceConnector(Protocol):
    """Salt okunur CMDB kaynağı: `since` (dahil) sonrasında güncellenen kayıtlar, eskiden yeniye."""

    system: ExternalSystem

    async def list_cis_updated_since(self, since: str | None) -> list[RemoteCI]: ...

    async def list_relationships_updated_since(
        self, since: str | None
    ) -> list[RemoteCIRelationship]: ...

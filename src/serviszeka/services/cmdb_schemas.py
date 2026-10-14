"""CMDB API/servis şemaları."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from serviszeka.domain.enums import CIClass, CIEnvironment, CIRelationshipType, CIStatus
from serviszeka.domain.types import UTCDateTime


class CICreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ci_class: CIClass
    environment: CIEnvironment = CIEnvironment.PROD
    criticality: int = Field(default=3, ge=1, le=4, description="1 = en kritik, 4 = en az kritik")
    owner_group: str | None = Field(default=None, max_length=100)
    status: CIStatus = CIStatus.ACTIVE
    description: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    external_refs: dict[str, Any] = Field(default_factory=dict)


class CIUpdate(BaseModel):
    """Kısmi güncelleme: yalnızca gönderilen alanlar uygulanır (exclude_unset)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    ci_class: CIClass | None = None
    environment: CIEnvironment | None = None
    criticality: int | None = Field(default=None, ge=1, le=4)
    owner_group: str | None = Field(default=None, max_length=100)
    status: CIStatus | None = None
    description: str | None = None
    attributes: dict[str, Any] | None = None
    external_refs: dict[str, Any] | None = None


class CIPatch(CIUpdate):
    """PATCH gövdesi: If-Match başlığı yerine sürüm gövdede de verilebilir."""

    expected_version: int | None = Field(default=None, ge=1)


class CIRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    ci_class: CIClass
    environment: CIEnvironment
    criticality: int
    owner_group: str | None
    status: CIStatus
    description: str
    attributes: dict[str, Any]
    external_refs: dict[str, Any]
    version: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class CIRelationshipCreate(BaseModel):
    source_id: str = Field(min_length=1, max_length=36)
    target_id: str = Field(min_length=1, max_length=36)
    type: CIRelationshipType


class CIRelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    target_id: str
    type: CIRelationshipType
    created_by: str
    created_at: UTCDateTime


class ImpactRequest(BaseModel):
    ci_ids: list[str] = Field(min_length=1, max_length=200)
    max_depth: int = Field(default=5, ge=1, le=20)


class ImpactedCIRead(BaseModel):
    ci: CIRead
    depth: int = Field(description="Değişen CI'a uzaklık (1 = doğrudan etkilenen)")
    path: list[str] = Field(description="Kök CI'dan bu CI'a giden CI id yolu (kök dahil)")
    relationship: CIRelationshipType = Field(description="Bu CI'a ulaştıran son ilişki türü")


class ImpactRead(BaseModel):
    root_ci_ids: list[str]
    affected: list[ImpactedCIRead]
    highest_criticality: int | None = Field(
        description="Kökler + etkilenenler içindeki en yüksek kritiklik (en küçük sayı)"
    )
    business_services: list[CIRead] = Field(
        description="Kökler ve etkilenenler arasındaki iş hizmetleri (ci_class=service)"
    )
    truncated: bool = Field(
        default=False,
        description="Etkilenen CI sınırına ulaşıldı; liste eksik olabilir (risk temkinli hesaplanır)",
    )

    @classmethod
    def from_analysis(cls, result: Any) -> ImpactRead:
        """`services.cmdb.ImpactAnalysis` -> ImpactRead (API ve araçlar ortak kullanır)."""
        return cls(
            root_ci_ids=result.root_ci_ids,
            affected=[
                ImpactedCIRead(
                    ci=CIRead.model_validate(a.ci),
                    depth=a.depth,
                    path=a.path,
                    relationship=a.relationship,
                )
                for a in result.affected
            ],
            highest_criticality=result.highest_criticality,
            business_services=[CIRead.model_validate(c) for c in result.business_services],
            truncated=result.truncated,
        )

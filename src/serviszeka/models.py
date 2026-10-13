"""Çekirdek ORM modelleri. Tüm modüller bu tabloları paylaşır."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from serviszeka.db import Base
from serviszeka.domain.enums import (
    ApprovalDecision,
    ChangeState,
    ChangeType,
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    CIStatus,
    DecisionOutcome,
    ExternalSystem,
    Impact,
    IncidentStatus,
    OnHoldReason,
    Priority,
    RiskLevel,
    TicketKind,
    Urgency,
)


def _uuid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def _str_enum(enum_cls: type, length: int = 30) -> Enum:
    """Faz 2 enum sütunları: PostgreSQL'de adlı tip yerine VARCHAR (göçte tip yönetimi yok)."""
    return Enum(enum_cls, native_enum=False, length=length)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # İnsan tarafından okunur numara, örn. INC0000042
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    kind: Mapped[TicketKind] = mapped_column(Enum(TicketKind), default=TicketKind.INCIDENT)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    # Self-servis "kayıtlarım" ve ?mine=true süzgeci için indeksli
    caller: Mapped[str] = mapped_column(String(200), default="", index=True)
    category: Mapped[str | None] = mapped_column(String(100))
    service: Mapped[str | None] = mapped_column(String(100))
    impact: Mapped[Impact] = mapped_column(Enum(Impact), default=Impact.LOW)
    urgency: Mapped[Urgency] = mapped_column(Enum(Urgency), default=Urgency.LOW)
    priority: Mapped[Priority] = mapped_column(Enum(Priority), default=Priority.P5)
    status: Mapped[IncidentStatus] = mapped_column(Enum(IncidentStatus), default=IncidentStatus.NEW)
    assignment_group: Mapped[str | None] = mapped_column(String(100))
    assignee: Mapped[str | None] = mapped_column(String(200))
    resolution: Mapped[str | None] = mapped_column(Text)
    # SLA hedefleri (öncelikten hesaplanır)
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolve_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bekleme nedeni ve SLA duraklatma (yalnızca arayan/tedarikçi beklemesi saati durdurur)
    on_hold_reason: Mapped[OnHoldReason | None] = mapped_column(Enum(OnHoldReason))
    sla_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_paused_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Son yapay zekâ sınıflandırmasının güven skoru (0-1)
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    # İyimser kilit / senkron çakışma tespiti için sürüm
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    decisions: Mapped[list["AIDecision"]] = relationship(back_populates="incident")
    external_links: Mapped[list["ExternalLink"]] = relationship(back_populates="incident")
    # Etkilenen yapılandırma öğeleri (CMDB); triage'da etki kanıtı olarak kullanılır
    affected_cis: Mapped[list["ConfigurationItem"]] = relationship(
        secondary="incident_cis", lazy="selectin", order_by="ConfigurationItem.name"
    )

    def __init__(self, **kwargs: Any) -> None:
        # Yeni olayın CI bağı yoktur; boş koleksiyon async oturumda tembel yüklemeyi önler
        kwargs.setdefault("affected_cis", [])
        super().__init__(**kwargs)

    @property
    def ci_ids(self) -> list[str]:
        return [ci.id for ci in self.affected_cis]


class AIDecision(Base):
    """Her yapay zekâ kararının kaydı: denetim ve KVKK için kritik."""

    __tablename__ = "ai_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    # Değişiklik kararları (ör. change.assess_risk) olay yerine değişikliğe bağlanır
    change_id: Mapped[str | None] = mapped_column(ForeignKey("change_requests.id"), index=True)
    agent: Mapped[str] = mapped_column(String(50))  # örn. "service_desk"
    action: Mapped[str] = mapped_column(String(100))  # örn. "classify", "suggest_resolution"
    input_summary: Mapped[str] = mapped_column(Text)  # maskelenmiş girdi
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)  # bilgi bankası makale id'leri
    model: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float)
    # Olaylar arası onay kutusu (GET /incidents/decisions) için indeksli
    outcome: Mapped[DecisionOutcome] = mapped_column(Enum(DecisionOutcome), index=True)
    # Kararın dayandığı olay sürümü (kendi kısmi uygulamasından sonraki); bayat onayı engeller
    incident_version: Mapped[int | None] = mapped_column(Integer)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[Incident | None] = relationship(back_populates="decisions")


class AuditLog(Base):
    """Değiştirilemez denetim günlüğü: her kayıt bir öncekinin hash'ine zincirlenir."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(200))  # kullanıcı veya "ai:<ajan>"
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Benzersiz: eşzamanlı iki yazıcı aynı önceki kayda bağlanıp zinciri çatallayamaz
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # KB0000001
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    # Bilinen hata (KEDB) kaydı mı?
    is_known_error: Mapped[bool] = mapped_column(default=False)
    workaround: Mapped[str | None] = mapped_column(Text)
    # Taslak / yayında / arşiv
    state: Mapped[str] = mapped_column(String(20), default="published")
    # Embedding vektörü (JSON olarak; PostgreSQL'de pgvector'e taşınabilir)
    embedding: Mapped[list[float] | None] = mapped_column(JSON)
    source_incident_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExternalLink(Base):
    """ServisZeka kaydı ile harici ITSM kaydı (ServiceNow / Jira SM) arasındaki bağ."""

    __tablename__ = "external_links"
    __table_args__ = (UniqueConstraint("system", "external_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    system: Mapped[ExternalSystem] = mapped_column(Enum(ExternalSystem))
    external_id: Mapped[str] = mapped_column(String(100))  # sys_id veya Jira issue key
    external_number: Mapped[str | None] = mapped_column(String(50))  # INC..., SD-123
    # Son başarılı senkronda iki tarafın durumu (çakışma tespiti için)
    last_synced_local_version: Mapped[int] = mapped_column(Integer, default=0)
    last_synced_remote_updated: Mapped[str | None] = mapped_column(String(64))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[Incident] = relationship(back_populates="external_links")


class SyncEvent(Base):
    """Her senkron denemesinin kaydı (başarılı, çakışma, hata)."""

    __tablename__ = "sync_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    link_id: Mapped[str | None] = mapped_column(ForeignKey("external_links.id"), index=True)
    system: Mapped[ExternalSystem] = mapped_column(Enum(ExternalSystem))
    direction: Mapped[str] = mapped_column(String(10))  # inbound / outbound
    status: Mapped[str] = mapped_column(String(20))  # ok / conflict / error / skipped
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- CMDB -----------------------------------------------------------------------------


class ConfigurationItem(Base):
    """Yapılandırma öğesi (CI): uygulama, iş hizmeti, sunucu, veritabanı, ağ cihazı, bulut kaynağı."""

    __tablename__ = "configuration_items"
    __table_args__ = (UniqueConstraint("name", "environment"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), index=True)
    ci_class: Mapped[CIClass] = mapped_column(_str_enum(CIClass), index=True)
    environment: Mapped[CIEnvironment] = mapped_column(
        _str_enum(CIEnvironment), default=CIEnvironment.PROD
    )
    # 1 = en kritik, 4 = en az kritik
    criticality: Mapped[int] = mapped_column(Integer, default=3)
    # Sahip ekip: taxonomy.json assignment_group anahtarı
    owner_group: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[CIStatus] = mapped_column(_str_enum(CIStatus), default=CIStatus.ACTIVE)
    description: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Harici kayıt referansları, örn. {"servicenow": "<sys_id>"}
    external_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CIRelationship(Base):
    """CI ilişkisi: kaynak <tür> hedef (örn. uygulama depends_on veritabanı)."""

    __tablename__ = "ci_relationships"
    __table_args__ = (UniqueConstraint("source_id", "target_id", "type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("configuration_items.id"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("configuration_items.id"), index=True)
    type: Mapped[CIRelationshipType] = mapped_column(_str_enum(CIRelationshipType))
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


incident_cis = Table(
    "incident_cis",
    Base.metadata,
    Column("incident_id", ForeignKey("incidents.id"), primary_key=True),
    Column("ci_id", ForeignKey("configuration_items.id"), primary_key=True, index=True),
)


class CMDBExternalRef(Base):
    """Harici CMDB kaydı (ör. ServiceNow sys_id) ile yerel CI/ilişki eşlemesi.

    (system, entity_type, external_id) tekildir: aynı uzak kayıt iki kez içe alınamaz.
    `remote_updated` uzak sistemin son güncelleme zamanıdır (artımlı çekim filigranı).
    """

    __tablename__ = "cmdb_external_refs"
    __table_args__ = (UniqueConstraint("system", "entity_type", "external_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    system: Mapped[ExternalSystem] = mapped_column(_str_enum(ExternalSystem))
    entity_type: Mapped[str] = mapped_column(String(20))  # "ci" / "relationship"
    external_id: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    remote_updated: Mapped[str | None] = mapped_column(String(64))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Değişiklik yönetimi ---------------------------------------------------------------

change_affected_cis = Table(
    "change_affected_cis",
    Base.metadata,
    Column("change_id", ForeignKey("change_requests.id"), primary_key=True),
    Column("ci_id", ForeignKey("configuration_items.id"), primary_key=True, index=True),
)


class StandardChangeTemplate(Base):
    """Önceden onaylı standart değişiklik katalog öğesi."""

    __tablename__ = "standard_change_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Bu riskin üzerindeki değişiklik CAB'a (normal) gider
    max_risk: Mapped[RiskLevel] = mapped_column(_str_enum(RiskLevel), default=RiskLevel.LOW)
    # İzinli CI sınıfları (CIClass değerleri); boş = hiçbir CI'a uygulanamaz
    allowed_ci_classes: Mapped[list[str]] = mapped_column(JSON, default=list)
    implementation_plan: Mapped[str] = mapped_column(Text, default="")
    backout_plan: Mapped[str] = mapped_column(Text, default="")
    test_plan: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # CHG0000001
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    change_type: Mapped[ChangeType] = mapped_column(_str_enum(ChangeType))
    state: Mapped[ChangeState] = mapped_column(
        _str_enum(ChangeState), default=ChangeState.DRAFT, index=True
    )
    risk_level: Mapped[RiskLevel | None] = mapped_column(_str_enum(RiskLevel))
    risk_score: Mapped[int | None] = mapped_column(Integer)  # 0-100
    implementation_plan: Mapped[str] = mapped_column(Text, default="")
    backout_plan: Mapped[str] = mapped_column(Text, default="")
    test_plan: Mapped[str] = mapped_column(Text, default="")
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[str] = mapped_column(String(200), index=True)
    assignment_group: Mapped[str | None] = mapped_column(String(100))
    assignee: Mapped[str | None] = mapped_column(String(200))
    template_id: Mapped[str | None] = mapped_column(ForeignKey("standard_change_templates.id"))
    linked_incident_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # succeeded / failed (uygulama sonucu)
    implementation_outcome: Mapped[str | None] = mapped_column(String(20))
    # Uygulama sonrası gözden geçirme (PIR) notu; acil ve başarısız değişiklikte kapanış için zorunlu
    review_notes: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    affected_cis: Mapped[list[ConfigurationItem]] = relationship(
        secondary=change_affected_cis, lazy="selectin", order_by=ConfigurationItem.name
    )
    template: Mapped[StandardChangeTemplate | None] = relationship(lazy="selectin")

    @property
    def affected_ci_ids(self) -> list[str]:
        return [ci.id for ci in self.affected_cis]


class ChangeApproval(Base):
    """CAB/ECAB kararı; her onaylayan bir değişiklik için yalnızca bir kez karar verir."""

    __tablename__ = "change_approvals"
    __table_args__ = (UniqueConstraint("change_id", "approver"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    change_id: Mapped[str] = mapped_column(ForeignKey("change_requests.id"), index=True)
    approver: Mapped[str] = mapped_column(String(200))
    # Karar anındaki roller (ECAB: change_manager onayı aranır)
    approver_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    decision: Mapped[ApprovalDecision] = mapped_column(_str_enum(ApprovalDecision))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChangeFreezeWindow(Base):
    """Değişiklik dondurma penceresi; acil değişiklik dışında bu aralığa zamanlama yapılamaz."""

    __tablename__ = "change_freeze_windows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    # CIEnvironment değerleri; boş = tüm ortamlar
    environments: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

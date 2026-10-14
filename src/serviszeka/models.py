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
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from serviszeka.db import Base
from serviszeka.domain.enums import (
    ActionRequestState,
    ApprovalDecision,
    BusinessApprovalState,
    CatalogApprovalPolicy,
    ChangeState,
    ChangeType,
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    CIStatus,
    DecisionOutcome,
    EventGroupState,
    EventSeverity,
    EventStatus,
    ExternalSystem,
    Impact,
    ImprovementSource,
    ImprovementState,
    IncidentStatus,
    NotificationState,
    OnHoldReason,
    Priority,
    ProblemState,
    RiskClass,
    RiskLevel,
    ServiceRequestState,
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
    # Faz 4: KPI pencere sorguları (MTTA/MTTR, SLA uyumu) için indeksli (göç 0011)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
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
    # Faz 3: araç çağrısı kararının açtığı eylem isteği (onay /actions'ta; olay karar kutusunda
    # görünmez). SQLite'ta tablo kopyalamamak için FK'siz, indeksli.
    action_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    incident: Mapped[Incident | None] = relationship(back_populates="decisions")


class AuditLog(Base):
    """Değiştirilemez denetim günlüğü: her kayıt bir öncekinin hash'ine zincirlenir."""

    __tablename__ = "audit_log"
    # Kayıt geçmişi okumaları (KPI / CSI: olay başına incident.update satırları) indeksli (göç 0014)
    __table_args__ = (Index("ix_audit_log_entity", "entity_type", "entity_id"),)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Faz 4: KPI geçmiş okumaları (yeniden açılma, FCR, AI kabulü) pencereyle süzer (göç 0011)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
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


# --- Faz 3: otomasyon (araç yürütme, eylem istekleri) --------------------------------------


class ActionRequest(Base):
    """İnsan onayı bekleyen araç çağrısı (R2/R3 ya da politikanın otomatik uygulamadığı R1).

    `args` ham argümanlardır ve yalnızca yürütmede kullanılır (API'de asla dönmez);
    `args_display` gösterim için maskelenmiş kopyadır. Gizli bilgi argüman olmamalıdır.
    """

    __tablename__ = "action_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # ACT0000001
    tool: Mapped[str] = mapped_column(String(100), index=True)
    args: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    args_display: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    args_hash: Mapped[str] = mapped_column(String(64))
    risk_class: Mapped[RiskClass] = mapped_column(_str_enum(RiskClass, 10))
    state: Mapped[ActionRequestState] = mapped_column(
        _str_enum(ActionRequestState), default=ActionRequestState.PENDING, index=True
    )
    # R3: 2 (ya da CAB onaylı bağlı değişiklikle 1); diğerleri 1
    required_approvals: Mapped[int] = mapped_column(Integer, default=1)
    # "ai:<ajan>" ya da insan kullanıcı adı
    requested_by: Mapped[str] = mapped_column(String(200), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    change_id: Mapped[str | None] = mapped_column(ForeignKey("change_requests.id"), index=True)
    # Hizmet talebi (service_requests.id); FK'siz indeksli sütun (göç 0008 indeksi ekler)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # İzleme uyarı grubu (event_groups.id); FK'siz indeksli sütun (göç 0014): olay ajanının
    # bekleyen/reddedilen istek aramaları JSON `args` taraması yerine indeksli SQL
    event_group_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # Yapay zekâ isteğinde policy.evaluate kararı (AIDecision.id)
    decision_id: Mapped[str | None] = mapped_column(String(36))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    executed_by: Mapped[str | None] = mapped_column(String(200))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ActionApproval(Base):
    """Eylem isteği kararı; her kişi (büyük/küçük harf duyarsız) bir istek için bir kez karar verir."""

    __tablename__ = "action_approvals"
    __table_args__ = (UniqueConstraint("action_request_id", "approver_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    action_request_id: Mapped[str] = mapped_column(ForeignKey("action_requests.id"), index=True)
    approver: Mapped[str] = mapped_column(String(200))
    # Kimlik karşılaştırma anahtarı (kırpılmış, casefold): aynı kişi iki kez sayılamaz
    approver_key: Mapped[str] = mapped_column(String(200))
    approver_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    decision: Mapped[ApprovalDecision] = mapped_column(_str_enum(ApprovalDecision))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolInvocation(Base):
    """Idempotency kaydı: aynı aktör + anahtar + argümanlar -> önceki sonuç; farklı argüman -> 409."""

    __tablename__ = "tool_invocations"
    __table_args__ = (UniqueConstraint("actor", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    tool: Mapped[str] = mapped_column(String(100))
    # Araç adı + kanonik argümanların SHA-256 özeti
    args_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    action_request_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogItem(Base):
    """Hizmet kataloğu öğesi: form şeması, iş onayı politikası ve karşılama planı.

    `form_schema` JSON şeması alt kümesidir (string / enum / boolean / kullanıcı seçici
    `format: user` / grup seçici `format: group`). `fulfilment_plan` sıralı adımlardır:
    `{"tool": "identity.add_to_group", "title": "...", "args": {"user": "{{requester}}",
    "group": "{{form.group}}"}}`. Adımlar `tools.execute_tool` ile `ai:fulfilment` aktörüyle
    çalışır (services/requests). `standard`: önceden onaylı, tekrarlanabilir talep (Faz 3 kapısı
    "standart taleplerin %50'si insansız çözülüyor" bunlar üzerinden ölçülür).
    """

    __tablename__ = "catalog_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Kararlı anahtar (ör. password_reset); tohumlama ve URL için
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(100), index=True)
    form_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approval_policy: Mapped[CatalogApprovalPolicy] = mapped_column(
        _str_enum(CatalogApprovalPolicy), default=CatalogApprovalPolicy.NONE
    )
    # approval_policy=group iken onaylayan dizin grubu (kimlik/ad)
    approval_group: Mapped[str | None] = mapped_column(String(200))
    fulfilment_plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    standard: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ServiceRequest(Base):
    """Hizmet talebi (REQ0000001): katalog öğesi + form değerleri + karşılama durumu.

    Plan, açılışta katalog öğesinden kopyalanır (`plan`): öğe sonradan değişse de çalışan talep
    aynı adımları izler. `step_results` her adımın sonucunu (yürütüldü / onay bekliyor / başarısız,
    `action_request_id`) taşır; bağlı eylem istekleri `action_requests.request_id` üzerindendir.
    `touchless`: talep insan onayı ve insan düzenlemesi olmadan karşılandı mı
    (`human_approvals == 0 and human_edits == 0`); değilse `touchless_reason`.
    """

    __tablename__ = "service_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # REQ0000001
    catalog_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    # Açılış anındaki öğe kopyası (anahtar, ad, standart bayrağı, onay politikası)
    item_key: Mapped[str] = mapped_column(String(100), index=True)
    item_name: Mapped[str] = mapped_column(String(200))
    standard: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Talep kimin için (self-servis: belirteçteki kullanıcı); opened_by: kaydı açan aktör
    requester: Mapped[str] = mapped_column(String(200), index=True)
    opened_by: Mapped[str] = mapped_column(String(200))
    form_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[ServiceRequestState] = mapped_column(
        _str_enum(ServiceRequestState), default=ServiceRequestState.SUBMITTED, index=True
    )
    # İş onayı (yönetici / grup) — risk onayından (ActionRequest) ayrı ve önce
    approval_policy: Mapped[CatalogApprovalPolicy] = mapped_column(
        _str_enum(CatalogApprovalPolicy), default=CatalogApprovalPolicy.NONE
    )
    approval_state: Mapped[BusinessApprovalState] = mapped_column(
        _str_enum(BusinessApprovalState), default=BusinessApprovalState.NOT_REQUIRED
    )
    # Beklenen onaylayan: yöneticinin kimliği (UPN) ya da grup
    approver: Mapped[str | None] = mapped_column(String(200), index=True)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_comment: Mapped[str] = mapped_column(Text, default="")
    # Karşılama ilerlemesi
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    step_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Bekleyen adımın eylem isteği (ActionRequest.id); yoksa null
    waiting_action_id: Mapped[str | None] = mapped_column(String(36), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    # İnsansız çözüm ölçümü
    human_approvals: Mapped[int] = mapped_column(Integer, default=0)
    human_edits: Mapped[int] = mapped_column(Integer, default=0)
    # İnsan dokunuşları: [{"kind": "business_approval|action_approval|edit|retry|complete",
    # "actor", "at", "detail"}] — touchless_reason buradan üretilir
    touches: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    touchless: Mapped[bool | None] = mapped_column(Boolean, index=True)
    touchless_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Faz 3: sorun yönetimi (ITIL 4 Problem Management) -----------------------------------------

problem_incidents = Table(
    "problem_incidents",
    Base.metadata,
    Column("problem_id", ForeignKey("problems.id"), primary_key=True),
    Column("incident_id", ForeignKey("incidents.id"), primary_key=True, index=True),
)

problem_cis = Table(
    "problem_cis",
    Base.metadata,
    Column("problem_id", ForeignKey("problems.id"), primary_key=True),
    Column("ci_id", ForeignKey("configuration_items.id"), primary_key=True, index=True),
)

# Kalıcı çözüm için açılan değişiklikler (RFC)
problem_changes = Table(
    "problem_changes",
    Base.metadata,
    Column("problem_id", ForeignKey("problems.id"), primary_key=True),
    Column("change_id", ForeignKey("change_requests.id"), primary_key=True, index=True),
)


class Problem(Base):
    """Sorun kaydı: tekrarlayan olayların ortak kök nedeni (PRB0000001).

    `rca` insanın kabul ettiği kök neden analizidir; `rca_draft` yapay zekânın (ya da aracın)
    yazdığı taslaktır ve yalnızca insan kabulüyle `rca` + `root_cause` olur. Bilinen hata
    yayımlanınca `known_error_article_id` KEDB makalesini (KnowledgeArticle.is_known_error) gösterir.
    """

    __tablename__ = "problems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[ProblemState] = mapped_column(
        _str_enum(ProblemState), default=ProblemState.NEW, index=True
    )
    priority: Mapped[Priority] = mapped_column(_str_enum(Priority, 10), default=Priority.P3)
    category: Mapped[str | None] = mapped_column(String(100))
    assignment_group: Mapped[str | None] = mapped_column(String(100))
    root_cause: Mapped[str | None] = mapped_column(Text)
    # {"method": "5_why" | "timeline", "steps": [...], "root_cause": "..."}
    rca: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rca_draft: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    workaround: Mapped[str | None] = mapped_column(Text)
    known_error_article_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_articles.id"), index=True
    )
    # Tespit eden: "ai:problem" (eğilim ajanı) ya da insan; created_by kaydı açan aktördür
    detected_by: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(200), default="")
    # Kapanış/çözüm notu
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    incidents: Mapped[list[Incident]] = relationship(
        secondary=problem_incidents, lazy="selectin", order_by=Incident.number
    )
    affected_cis: Mapped[list[ConfigurationItem]] = relationship(
        secondary=problem_cis, lazy="selectin", order_by=ConfigurationItem.name
    )
    changes: Mapped[list[ChangeRequest]] = relationship(
        secondary=problem_changes, lazy="selectin", order_by=ChangeRequest.number
    )

    def __init__(self, **kwargs: Any) -> None:
        # Yeni kaydın bağları boştur; async oturumda tembel yüklemeyi önler
        for key in ("incidents", "affected_cis", "changes"):
            kwargs.setdefault(key, [])
        super().__init__(**kwargs)

    @property
    def incident_ids(self) -> list[str]:
        return [i.id for i in self.incidents]

    @property
    def affected_ci_ids(self) -> list[str]:
        return [ci.id for ci in self.affected_cis]

    @property
    def change_ids(self) -> list[str]:
        return [c.id for c in self.changes]


# --- Faz 4: kalıcı bildirim kuyruğu ve zamanlayıcı kiraları -----------------------------------


class NotificationOutbox(Base):
    """Kalıcı bildirim kuyruğu (transactional outbox).

    `notifications.stage` satırı kaydı oluşturan işlemle **aynı işlemde** yazar: rollback olursa
    bildirim de yoktur. Gönderici (`notifications.dispatch_due` / `send_pending`) yalnızca commit
    edilmiş satırları en az bir kez gönderir; `idempotency_key` alıcıya iletilir (tekrar gönderimde
    alıcı yinelemeyi ayıklar) ve tekildir (SLA uyarısında olay+durum başına tek kayıt). `payload`
    PII maskeli, `last_error` gizli bilgisiz (URL'ler ayıklanır).
    """

    __tablename__ = "notification_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    event: Mapped[str] = mapped_column(String(100), index=True)
    # Alıcı türü (bugün yalnızca nöbetçi: notifications.get_notifier)
    target: Mapped[str] = mapped_column(String(50), default="oncall")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Gönderim sonrası denetim kaydı bilgileri
    actor: Mapped[str] = mapped_column(String(200))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(100), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[NotificationState] = mapped_column(
        _str_enum(NotificationState, 20), default=NotificationState.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchedulerLease(Base):
    """Zamanlayıcı iş kirası: birden fazla kopyada her işi tek kopya çalıştırır.

    Kira, `lease_until` karşılaştırmalı güncellemeyle (compare-and-set) alınır: yalnızca süresi
    dolmuş kirayı tek bir kopya güncelleyebilir (PostgreSQL ve SQLite'ta aynı). İş bitince
    `lease_until` bir sonraki çalışma zamanına çekilir (aralık da kira ile korunur).
    """

    __tablename__ = "scheduler_leases"

    job: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner: Mapped[str] = mapped_column(String(200))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)
    runs: Mapped[int] = mapped_column(Integer, default=0)


# --- Faz 4: izleme ve olay (event) yönetimi ----------------------------------------------------


class EventGroup(Base):
    """Bağıntılı uyarı grubu: kök CI'daki (bağımlılık) uyarı, bağımlılarındaki uyarıları açıklar.

    Grup başına en fazla bir olay (`incident_id`) açılır; aynı gruba gelen yeni/çözülen uyarılar
    olaya çalışma notu olarak eklenir (`noted_at` son not). Bkz. services/events.py.
    """

    __tablename__ = "event_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # EVG0000001
    state: Mapped[EventGroupState] = mapped_column(
        _str_enum(EventGroupState, 20), default=EventGroupState.OPEN, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    # Kök CI (CMDB); CI çözülemeyen uyarılarda boş, grup parmak iziyle tutulur
    root_ci_id: Mapped[str | None] = mapped_column(ForeignKey("configuration_items.id"), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(200), index=True)
    # Bastırılmamış uyarılar içindeki en yüksek önem
    severity: Mapped[EventSeverity] = mapped_column(
        _str_enum(EventSeverity, 20), default=EventSeverity.INFO
    )
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Bastırılmamış bir üyenin son anlamlı değişikliği (yeni uyarı, çözülme, önem); not tetikler
    last_change_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flapping: Mapped[bool] = mapped_column(Boolean, default=False)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    noted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_into_id: Mapped[str | None] = mapped_column(String(36))
    acknowledged_by: Mapped[str | None] = mapped_column(String(200))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    silenced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    silenced_by: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Event(Base):
    """İzleme uyarısı (Prometheus Alertmanager ya da genel JSON webhook).

    (source, fingerprint, starts_at) tekildir: aynı uyarının tekrar gönderimi yeni satır açmaz
    (`received_count` artar), çözülen uyarı yeniden "firing" olamaz (yeniden ateşleme yeni
    `starts_at` ile yeni satırdır). Etiket/açıklamalar PII maskeli, gizli görünen anahtarlar
    gizlenmiş saklanır. `suppressed`: `flapping` / `silenced` (olay açma ve not tetiklemez).
    """

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source", "fingerprint", "starts_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(50))
    fingerprint: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[EventStatus] = mapped_column(_str_enum(EventStatus, 20), index=True)
    severity: Mapped[EventSeverity] = mapped_column(_str_enum(EventSeverity, 20))
    title: Mapped[str] = mapped_column(String(300))
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ci_id: Mapped[str | None] = mapped_column(ForeignKey("configuration_items.id"), index=True)
    # Hangi eşleme kuralı CI'ı buldu (ör. "instance:name")
    ci_rule: Mapped[str | None] = mapped_column(String(100))
    group_id: Mapped[str | None] = mapped_column(ForeignKey("event_groups.id"), index=True)
    suppressed: Mapped[str | None] = mapped_column(String(20))
    received_count: Mapped[int] = mapped_column(Integer, default=1)
    # Son anlamlı değişiklik (açılış, çözülme, önem); yalnızca tekrar gönderim değiştirmez
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EventDelivery(Base):
    """Webhook teslimatı tekilleştirme (yeniden oynatma koruması): `dedup_key` tekildir."""

    __tablename__ = "event_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(50))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


# --- Faz 4: sürekli iyileştirme (ITIL 4 CSI kaydı) ---------------------------------------------


class Improvement(Base):
    """Sürekli iyileştirme kaydı (CSI0000001): fikir -> onay -> uygulama -> ölçüm -> kapanış.

    Bağlı KPI (`kpi_key`, isteğe bağlı kırılım `kpi_dimension` = `kpi_dimension_value`) için taban
    değeri kayıtta `[baseline_start, baseline_end)` penceresinden hesaplanır; uygulama bitince
    (`implemented_at`) `[implemented_at, implemented_at + measure_days)` penceresi ölçülür ve
    `measured_value`, `target_met`, `measurement` (önce/sonra pencereleri, fark) yazılır.
    Yapay zekâ (`ai:csi`) yalnızca fikir açar ve ölçer; onay, başlatma, kapatma ve red insandır.
    """

    __tablename__ = "improvements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[ImprovementSource] = mapped_column(
        _str_enum(ImprovementSource, 20), default=ImprovementSource.MANUAL, index=True
    )
    state: Mapped[ImprovementState] = mapped_column(
        _str_enum(ImprovementState, 20), default=ImprovementState.IDEA, index=True
    )
    kpi_key: Mapped[str | None] = mapped_column(String(64), index=True)
    kpi_dimension: Mapped[str | None] = mapped_column(String(50))
    kpi_dimension_value: Mapped[str | None] = mapped_column(String(100))
    baseline_value: Mapped[float | None] = mapped_column(Float)
    baseline_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baseline_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_value: Mapped[float | None] = mapped_column(Float)
    measured_value: Mapped[float | None] = mapped_column(Float)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_met: Mapped[bool | None] = mapped_column(Boolean)
    measurement: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    measure_days: Mapped[int] = mapped_column(Integer, default=30)
    owner: Mapped[str | None] = mapped_column(String(200), index=True)
    # Fayda ve efor 1-5; öncelik puanı = fayda / efor * 20 (4-100)
    benefit_estimate: Mapped[int] = mapped_column(Integer, default=3)
    effort: Mapped[int] = mapped_column(Integer, default=3)
    priority_score: Mapped[float] = mapped_column(Float, default=20.0)
    problem_id: Mapped[str | None] = mapped_column(ForeignKey("problems.id"), index=True)
    change_id: Mapped[str | None] = mapped_column(ForeignKey("change_requests.id"), index=True)
    # Sinyal kanıtı (deterministik sayılar) ve tekilleştirme anahtarı (CSI ajanı)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    signal_key: Mapped[str | None] = mapped_column(String(200), index=True)
    detected_by: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(200), default="")
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    implemented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(200))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Kapanış ya da red notu
    resolution: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

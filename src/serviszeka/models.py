"""Çekirdek ORM modelleri. Tüm modüller bu tabloları paylaşır."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from serviszeka.db import Base
from serviszeka.domain.enums import (
    DecisionOutcome,
    ExternalSystem,
    Impact,
    IncidentStatus,
    OnHoldReason,
    Priority,
    TicketKind,
    Urgency,
)


def _uuid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # İnsan tarafından okunur numara, örn. INC0000042
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    kind: Mapped[TicketKind] = mapped_column(Enum(TicketKind), default=TicketKind.INCIDENT)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    caller: Mapped[str] = mapped_column(String(200), default="")
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


class AIDecision(Base):
    """Her yapay zekâ kararının kaydı: denetim ve KVKK için kritik."""

    __tablename__ = "ai_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    agent: Mapped[str] = mapped_column(String(50))  # örn. "service_desk"
    action: Mapped[str] = mapped_column(String(100))  # örn. "classify", "suggest_resolution"
    input_summary: Mapped[str] = mapped_column(Text)  # maskelenmiş girdi
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)  # bilgi bankası makale id'leri
    model: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float)
    outcome: Mapped[DecisionOutcome] = mapped_column(Enum(DecisionOutcome))
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

"""İki yönlü ITSM senkron motoru (ServiceNow / Jira SM <-> ServisZeka).

Kurallar:
- Giden (push): bağ yoksa uzakta oluşturur; varsa yalnızca incident.version >
  link.last_synced_local_version olduğunda günceller.
- Gelen (pull): son imleçten beri değişen uzak kayıtlar yerelde oluşturulur/güncellenir.
- Çakışma: son senkrondan beri hem yerel sürüm hem uzak updated_at değiştiyse
  SyncEvent 'conflict' yazılır; çözüm politikası 'manual' (varsayılan), 'remote_wins', 'local_wins'.
- Eşzamanlılık: gelen güncelleme uygulanmadan hemen önce satırın sürümü veritabanından
  yeniden okunur (PostgreSQL'de SELECT ... FOR UPDATE, SQLite'ta koşullu UPDATE ...
  WHERE version = :beklenen). Okumadan sonra yerel sürüm değiştiyse kayıt yenilenir ve
  yerel değişiklik sayılır (çakışma yolu); bir kez yeniden denenir, ezilmez.
- Döngü önleme: gelen güncellemeden sonra link.last_synced_local_version yeni yerel
  sürüme çekilir, böylece aynı değişiklik geri gönderilmez.
- Her senkron bir SyncEvent yazar. Motor commit etmez (flush eder).
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.domain.enums import (
    ExternalSystem,
    Impact,
    IncidentStatus,
    Priority,
    SyncDirection,
    Urgency,
)
from serviszeka.domain.priority import calculate_priority
from serviszeka.integrations.base import (
    ConnectorError,
    ITSMConnector,
    RemoteTicket,
    incident_to_fields,
)
from serviszeka.models import ExternalLink, Incident, SyncEvent
from serviszeka.services.incidents import apply_sla, update_sla_pause
from serviszeka.services.incidents import next_number as next_incident_number

ConflictPolicy = Literal["manual", "remote_wins", "local_wins"]
CONFLICT_POLICIES: tuple[str, ...] = ("manual", "remote_wins", "local_wins")

STATUS_OK = "ok"
STATUS_CONFLICT = "conflict"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"
STATUS_RESOLVED = "resolved"

_DONE_STATUSES = frozenset({IncidentStatus.RESOLVED, IncidentStatus.CLOSED})


def _stamp_status(incident: Incident, old: IncidentStatus | None, now: datetime) -> None:
    """Uzaktan gelen durum için SLA zaman damgaları (ilk yanıt / çözüm)."""
    new = IncidentStatus(incident.status)
    if new != IncidentStatus.NEW and incident.responded_at is None:
        incident.responded_at = now
    if new in _DONE_STATUSES:
        if old not in _DONE_STATUSES or incident.resolved_at is None:
            incident.resolved_at = now
    elif new != IncidentStatus.CANCELLED:
        incident.resolved_at = None  # yeniden açıldı


def _ticket_snapshot(t: RemoteTicket) -> dict[str, Any]:
    d = asdict(t)
    d.pop("raw", None)
    return {k: (str(v) if v is not None else None) for k, v in d.items()}


class SyncEngine:
    def __init__(
        self,
        session: AsyncSession,
        connectors: Mapping[ExternalSystem, ITSMConnector] | Iterable[ITSMConnector],
        *,
        conflict_policy: ConflictPolicy = "manual",
        actor: str = "system:sync",
    ) -> None:
        if conflict_policy not in CONFLICT_POLICIES:
            raise ValueError(f"Geçersiz çakışma politikası: {conflict_policy}")
        self.session = session
        if isinstance(connectors, Mapping):
            self.connectors = dict(connectors)
        else:
            self.connectors = {c.system: c for c in connectors}
        self.conflict_policy = conflict_policy
        self.actor = actor

    # --- yardımcılar ---

    def _connector(self, system: ExternalSystem) -> ITSMConnector:
        try:
            return self.connectors[ExternalSystem(system)]
        except KeyError as exc:
            raise ConnectorError(f"{system} için bağlayıcı yapılandırılmamış") from exc

    async def _event(
        self,
        *,
        system: ExternalSystem,
        direction: SyncDirection,
        status: str,
        message: str = "",
        link: ExternalLink | None = None,
        payload: dict[str, Any] | None = None,
    ) -> SyncEvent:
        ev = SyncEvent(
            link_id=link.id if link else None,
            system=system,
            direction=direction.value,
            status=status,
            message=message,
            payload=payload or {},
        )
        self.session.add(ev)
        await self.session.flush()
        return ev

    async def _find_link(
        self, system: ExternalSystem, *, incident_id: str | None = None,
        external_id: str | None = None,
    ) -> ExternalLink | None:
        stmt = select(ExternalLink).where(ExternalLink.system == system)
        if incident_id is not None:
            stmt = stmt.where(ExternalLink.incident_id == incident_id)
        if external_id is not None:
            stmt = stmt.where(ExternalLink.external_id == external_id)
        return (await self.session.scalars(stmt.limit(1))).first()

    def _mark_synced(self, link: ExternalLink, incident: Incident, remote: RemoteTicket) -> None:
        link.last_synced_local_version = incident.version
        link.last_synced_remote_updated = remote.updated_at
        link.last_synced_at = datetime.now(UTC)
        if remote.number:
            link.external_number = remote.number

    def _apply_remote(
        self, incident: Incident, remote: RemoteTicket
    ) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
        """Uzak alanları yerel kayda uygular; değişiklik varsa sürümü artırır."""
        changes: dict[str, Any] = {}
        old_status = IncidentStatus(incident.status) if incident.status else None

        def setf(name: str, value: Any) -> None:
            if value is not None and getattr(incident, name) != value:
                changes[name] = {"old": str(getattr(incident, name)), "new": str(value)}
                setattr(incident, name, value)

        setf("title", remote.title or None)
        setf("description", remote.description)
        setf("status", remote.status)
        setf("impact", remote.impact)
        setf("urgency", remote.urgency)
        if remote.priority is not None:
            setf("priority", remote.priority)
        elif "impact" in changes or "urgency" in changes:
            setf("priority", calculate_priority(incident.impact, incident.urgency))
        setf("assignee", remote.assignee)
        if "priority" in changes:
            # Öncelik değişti: SLA vadeleri yeni önceliğe göre yeniden hesaplanır
            apply_sla(incident)
        sla_events: list[tuple[str, dict[str, Any]]] = []
        if "status" in changes:
            now = datetime.now(UTC)
            sla_events = self._sync_hold_state(incident, changes, now)
            _stamp_status(incident, old_status, now)
        if changes:
            incident.version = (incident.version or 1) + 1
        return changes, sla_events

    @staticmethod
    def _sync_hold_state(
        incident: Incident, changes: dict[str, Any], now: datetime
    ) -> list[tuple[str, dict[str, Any]]]:
        """Beklemeden çıkan kayıtta nedeni temizler, duran SLA saatini sürdürür.

        Uzak sistem bekleme nedeni taşımaz: senkronla beklemeye alınan kayıt saati durdurmaz.
        Çözüm damgasından önce çalışır (beklemeden doğrudan çözülende de vade ötelenir).
        """
        new_status = IncidentStatus(incident.status)
        previous_reason = incident.on_hold_reason

        def setf(name: str, value: Any) -> None:
            old = getattr(incident, name)
            if old != value:
                changes[name] = {
                    "old": str(old) if old is not None else None,
                    "new": str(value) if value is not None else None,
                }
                setattr(incident, name, value)

        if new_status != IncidentStatus.ON_HOLD:
            setf("on_hold_reason", None)
        return update_sla_pause(incident, new_status, previous_reason, setf, now)

    # --- giden ---

    async def push(
        self, incident: Incident, systems: Iterable[ExternalSystem] | None = None
    ) -> list[SyncEvent]:
        targets = [ExternalSystem(s) for s in systems] if systems else list(self.connectors)
        events = []
        for system in targets:
            events.append(await self._push_one(incident, system))
        return events

    async def _push_one(self, incident: Incident, system: ExternalSystem) -> SyncEvent:
        out = SyncDirection.OUTBOUND
        link = await self._find_link(system, incident_id=incident.id)
        try:
            connector = self._connector(system)
            fields = incident_to_fields(incident)
            if link is None:
                remote = await connector.create(fields)
                link = ExternalLink(
                    incident_id=incident.id,
                    system=system,
                    external_id=remote.external_id,
                    external_number=remote.number,
                )
                self.session.add(link)
                self._mark_synced(link, incident, remote)
                await self.session.flush()
                await audit.record(
                    self.session, actor=self.actor, action="sync.remote_created",
                    entity_type="incident", entity_id=incident.id,
                    details={"system": system.value, "external_id": remote.external_id},
                )
                return await self._event(
                    system=system, direction=out, status=STATUS_OK, link=link,
                    message=f"Uzak kayıt oluşturuldu: {remote.number or remote.external_id}",
                    payload={"incident_id": incident.id, "version": incident.version},
                )

            if incident.version <= link.last_synced_local_version:
                return await self._event(
                    system=system, direction=out, status=STATUS_SKIPPED, link=link,
                    message="Yerel değişiklik yok",
                    payload={"incident_id": incident.id, "version": incident.version},
                )

            current = await connector.get(link.external_id)
            if current.updated_at != link.last_synced_remote_updated:
                # Her iki taraf da değişmiş
                return await self._handle_conflict(incident, link, current, out)

            remote = await connector.update(link.external_id, fields)
            self._mark_synced(link, incident, remote)
            await self.session.flush()
            return await self._event(
                system=system, direction=out, status=STATUS_OK, link=link,
                message="Uzak kayıt güncellendi",
                payload={"incident_id": incident.id, "version": incident.version},
            )
        except ConnectorError as exc:
            return await self._event(
                system=system, direction=out, status=STATUS_ERROR, link=link,
                message=str(exc), payload={"incident_id": incident.id},
            )

    # --- gelen ---

    async def last_cursor(self, system: ExternalSystem) -> str | None:
        ev = (
            await self.session.scalars(
                select(SyncEvent)
                .where(
                    SyncEvent.system == system,
                    SyncEvent.direction == SyncDirection.INBOUND.value,
                    SyncEvent.link_id.is_(None),
                    SyncEvent.status == STATUS_OK,
                )
                .order_by(SyncEvent.created_at.desc())
                .limit(1)
            )
        ).first()
        return (ev.payload or {}).get("cursor") if ev else None

    async def pull(self, system: ExternalSystem) -> dict[str, Any]:
        system = ExternalSystem(system)
        inbound = SyncDirection.INBOUND
        cursor = await self.last_cursor(system)
        try:
            tickets = await self._connector(system).list_updated_since(cursor)
        except ConnectorError as exc:
            await self._event(system=system, direction=inbound, status=STATUS_ERROR,
                              message=str(exc), payload={"cursor": cursor})
            return {"system": system.value, "error": str(exc), "cursor": cursor}

        counts = {"created": 0, "updated": 0, "conflicts": 0, "skipped": 0, "errors": 0}
        new_cursor = cursor
        for ticket in tickets:
            result = await self._pull_one(system, ticket)
            counts[result] += 1
            if ticket.updated_at and (new_cursor is None or ticket.updated_at > new_cursor):
                new_cursor = ticket.updated_at

        await self._event(
            system=system, direction=inbound, status=STATUS_OK,
            message=f"Çekme tamamlandı: {len(tickets)} kayıt",
            payload={"cursor": new_cursor, "previous_cursor": cursor, **counts},
        )
        return {"system": system.value, "cursor": new_cursor, "fetched": len(tickets), **counts}

    async def _pull_one(self, system: ExternalSystem, remote: RemoteTicket) -> str:
        inbound = SyncDirection.INBOUND
        link = await self._find_link(system, external_id=remote.external_id)
        if link is None:
            impact = remote.impact or Impact.LOW
            urgency = remote.urgency or Urgency.LOW
            priority = remote.priority or (
                calculate_priority(impact, urgency)
                if remote.impact and remote.urgency else Priority.P5
            )
            incident = Incident(
                created_at=datetime.now(UTC),
                number=await next_incident_number(self.session),
                title=remote.title or remote.number or remote.external_id,
                description=remote.description or "",
                caller="",
                impact=impact,
                urgency=urgency,
                priority=priority,
                assignee=remote.assignee,
                version=1,
            )
            if remote.status is not None:
                incident.status = remote.status
                _stamp_status(incident, None, incident.created_at)
            apply_sla(incident)  # içe alınan kayıt da SLA vadesi alır
            self.session.add(incident)
            await self.session.flush()
            link = ExternalLink(
                incident_id=incident.id, system=system,
                external_id=remote.external_id, external_number=remote.number,
            )
            self.session.add(link)
            self._mark_synced(link, incident, remote)
            await self.session.flush()
            await audit.record(
                self.session, actor=self.actor, action="sync.incident_imported",
                entity_type="incident", entity_id=incident.id,
                details={"system": system.value, "external_id": remote.external_id},
            )
            await self._event(
                system=system, direction=inbound, status=STATUS_OK, link=link,
                message=f"Yerel kayıt oluşturuldu: {incident.number}",
                payload={"incident_id": incident.id, "remote_updated_at": remote.updated_at},
            )
            return "created"

        if remote.updated_at == link.last_synced_remote_updated:
            return "skipped"  # zaten işlenmiş (yankı ya da >= imleç tekrarı)

        incident = await self.session.get(Incident, link.incident_id)
        assert incident is not None
        for _attempt in range(2):  # eşzamanlı değişiklikte bir kez yeniden dene
            if incident.version > link.last_synced_local_version:
                ev = await self._handle_conflict(incident, link, remote, inbound)
                return "conflicts" if ev.status == STATUS_CONFLICT else (
                    "errors" if ev.status == STATUS_ERROR else "updated"
                )
            if await self._lock_version(incident):
                await self._apply_inbound(incident, link, remote, inbound)
                return "updated"
            # Okuma ile yazma arasında yerel kayıt değişti: güncel hâli yükle
            await self.session.refresh(incident)
            await self.session.refresh(link)

        # İki denemede de sürüm kilitlenemedi: üzerine yazmak yerine çakışma say
        ev = await self._handle_conflict(incident, link, remote, inbound)
        return "conflicts" if ev.status == STATUS_CONFLICT else (
            "errors" if ev.status == STATUS_ERROR else "updated"
        )

    async def _lock_version(self, incident: Incident) -> bool:
        """İyimser kilit: bellekteki sürüm veritabanındakiyle hâlâ aynıysa satırı kilitler.

        PostgreSQL'de SELECT ... FOR UPDATE satırı commit'e kadar kilitler; SQLite'ta
        koşullu (etkisiz) UPDATE yazma kilidini alır ve eşleşme yoksa False döner.
        """
        expected = incident.version
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            db_version = await self.session.scalar(
                select(Incident.version).where(Incident.id == incident.id).with_for_update()
            )
            return db_version == expected
        result = await self.session.execute(
            update(Incident)
            .where(Incident.id == incident.id, Incident.version == expected)
            # updated_at açıkça kendine atanır: aksi halde onupdate sessizce değiştirir
            .values(version=Incident.version, updated_at=Incident.updated_at)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def _apply_inbound(
        self, incident: Incident, link: ExternalLink, remote: RemoteTicket,
        direction: SyncDirection, message: str = "Yerel kayıt güncellendi",
    ) -> SyncEvent:
        changes, sla_events = self._apply_remote(incident, remote)
        # Döngü önleme: yeni yerel sürüm "senkronlanmış" sayılır, geri gönderilmez.
        self._mark_synced(link, incident, remote)
        await self.session.flush()
        if changes:
            await audit.record(
                self.session, actor=self.actor, action="sync.incident_updated",
                entity_type="incident", entity_id=incident.id,
                details={"system": link.system.value, "changes": changes},
            )
        for action, details in sla_events:
            await audit.record(
                self.session, actor=self.actor, action=action,
                entity_type="incident", entity_id=incident.id, details=details,
            )
        return await self._event(
            system=link.system, direction=direction, status=STATUS_OK, link=link,
            message=message,
            payload={"incident_id": incident.id, "version": incident.version,
                     "changes": changes, "remote_updated_at": remote.updated_at},
        )

    async def _apply_outbound(
        self, incident: Incident, link: ExternalLink, direction: SyncDirection,
        message: str = "Uzak kayıt güncellendi",
    ) -> SyncEvent:
        remote = await self._connector(link.system).update(
            link.external_id, incident_to_fields(incident)
        )
        self._mark_synced(link, incident, remote)
        await self.session.flush()
        return await self._event(
            system=link.system, direction=direction, status=STATUS_OK, link=link,
            message=message,
            payload={"incident_id": incident.id, "version": incident.version},
        )

    # --- çakışma ---

    async def _handle_conflict(
        self, incident: Incident, link: ExternalLink, remote: RemoteTicket,
        direction: SyncDirection,
    ) -> SyncEvent:
        if self.conflict_policy == "remote_wins":
            return await self._apply_inbound(
                incident, link, remote, direction, message="Çakışma: uzak kayıt kazandı"
            )
        if self.conflict_policy == "local_wins":
            try:
                return await self._apply_outbound(
                    incident, link, direction, message="Çakışma: yerel kayıt kazandı"
                )
            except ConnectorError as exc:
                return await self._event(
                    system=link.system, direction=direction, status=STATUS_ERROR, link=link,
                    message=str(exc), payload={"incident_id": incident.id},
                )

        # manual: aynı uzak sürüm için açık çakışma zaten varsa tekrar yazma
        existing = (
            await self.session.scalars(
                select(SyncEvent).where(
                    SyncEvent.link_id == link.id, SyncEvent.status == STATUS_CONFLICT
                )
            )
        ).all()
        for ev in existing:
            if (ev.payload or {}).get("remote_updated_at") == remote.updated_at and (
                ev.payload or {}
            ).get("local_version") == incident.version:
                return ev
        return await self._event(
            system=link.system, direction=direction, status=STATUS_CONFLICT, link=link,
            message="Çakışma: kayıt iki tarafta da değişti, manuel çözüm gerekli",
            payload={
                "incident_id": incident.id,
                "local_version": incident.version,
                "last_synced_local_version": link.last_synced_local_version,
                "remote_updated_at": remote.updated_at,
                "last_synced_remote_updated": link.last_synced_remote_updated,
                "local": {k: (str(v) if v is not None else None)
                          for k, v in incident_to_fields(incident).items()},
                "remote": _ticket_snapshot(remote),
            },
        )

    async def list_conflicts(self) -> list[SyncEvent]:
        return list(
            (
                await self.session.scalars(
                    select(SyncEvent)
                    .where(SyncEvent.status == STATUS_CONFLICT)
                    .order_by(SyncEvent.created_at.asc())
                )
            ).all()
        )

    async def resolve_conflict(
        self, event_id: str, winner: Literal["local", "remote"], actor: str | None = None
    ) -> SyncEvent:
        if winner not in ("local", "remote"):
            raise ValueError("winner 'local' veya 'remote' olmalı")
        ev = await self.session.get(SyncEvent, event_id)
        if ev is None or ev.status != STATUS_CONFLICT or ev.link_id is None:
            raise LookupError("Açık çakışma kaydı bulunamadı")
        link = await self.session.get(ExternalLink, ev.link_id)
        assert link is not None
        incident = await self.session.get(Incident, link.incident_id)
        assert incident is not None
        connector = self._connector(link.system)
        if winner == "remote":
            remote = await connector.get(link.external_id)
            result = await self._apply_inbound(
                incident, link, remote, SyncDirection.INBOUND,
                message="Çakışma manuel çözüldü: uzak kayıt kazandı",
            )
        else:
            result = await self._apply_outbound(
                incident, link, SyncDirection.OUTBOUND,
                message="Çakışma manuel çözüldü: yerel kayıt kazandı",
            )
        ev.status = STATUS_RESOLVED
        # Aynı bağdaki diğer açık çakışmalar da artık geçersiz
        others = (
            await self.session.scalars(
                select(SyncEvent).where(
                    SyncEvent.link_id == link.id, SyncEvent.status == STATUS_CONFLICT
                )
            )
        ).all()
        for o in others:
            o.status = STATUS_RESOLVED
        await self.session.flush()
        await audit.record(
            self.session, actor=actor or self.actor, action="sync.conflict_resolved",
            entity_type="incident", entity_id=incident.id,
            details={"event_id": event_id, "winner": winner, "system": link.system.value},
        )
        return result

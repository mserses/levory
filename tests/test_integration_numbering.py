"""Modüller arası tutarlılık: ortak olay numaralandırma ve içe alınan kayıtta SLA."""

from serviszeka.domain.enums import ExternalSystem, Impact, IncidentStatus, Priority, Urgency
from serviszeka.integrations import sync
from serviszeka.integrations.base import RemoteTicket
from serviszeka.integrations.sync import SyncEngine
from serviszeka.services import incidents
from serviszeka.services.schemas import IncidentCreate


class _OneTicket:
    system = ExternalSystem.SERVICENOW

    async def list_updated_since(self, since):
        return [
            RemoteTicket(
                external_id="x1", number="INC0099999", title="Uzak", description="",
                status=IncidentStatus.NEW, priority=Priority.P2, impact=Impact.HIGH,
                urgency=Urgency.MEDIUM, updated_at="2024-01-01 00:00:00",
            )
        ]


def test_single_numbering_helper():
    assert sync.next_incident_number is incidents.next_number


async def test_local_and_imported_numbers_are_sequential(session):
    first = await incidents.create_incident(
        session, IncidentCreate(title="Yerel", description="d", caller="ali"), actor="t"
    )
    await SyncEngine(session, [_OneTicket()]).pull(ExternalSystem.SERVICENOW)
    second = await incidents.create_incident(
        session, IncidentCreate(title="Yerel 2", description="d", caller="ali"), actor="t"
    )
    imported = next(i for i in await incidents.list_incidents(session) if i.title == "Uzak")
    assert [first.number, imported.number, second.number] == [
        "INC0000001", "INC0000002", "INC0000003",
    ]
    assert imported.response_due_at is not None and imported.resolve_due_at is not None


async def test_read_schema_returns_utc_datetimes(session):
    from serviszeka.services.schemas import IncidentRead

    inc = await incidents.create_incident(
        session, IncidentCreate(title="Saat", description="", caller="ali"), actor="t"
    )
    await session.commit()
    await session.refresh(inc)  # SQLite'tan saat dilimsiz değer okunur
    data = IncidentRead.model_validate(inc).model_dump(mode="json")
    assert data["created_at"].endswith("Z") or data["created_at"].endswith("+00:00")

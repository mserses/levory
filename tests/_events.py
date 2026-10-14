"""İzleme / olay yönetimi testleri için yardımcılar (Alertmanager gövdesi, imza, CMDB zinciri)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from serviszeka.config import Settings
from serviszeka.domain.enums import CIClass, CIEnvironment, CIRelationshipType
from serviszeka.services import cmdb, events
from serviszeka.services.cmdb_schemas import CICreate

SECRET = "olay-gizli-anahtar-123"
T0 = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
EV_SETTINGS = Settings(events_webhook_secret=SECRET)


def am_alert(
    fingerprint: str,
    *,
    status: str = "firing",
    severity: str = "critical",
    alertname: str = "HighErrorRate",
    starts: datetime = T0,
    ends: datetime | None = None,
    **labels: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "labels": {"alertname": alertname, "severity": severity, **labels},
        "annotations": {"summary": f"{alertname} uyarısı"},
        "startsAt": starts.strftime("%Y-%m-%dT%H:%M:%S.123456789Z"),
        "endsAt": (ends.isoformat().replace("+00:00", "Z") if ends else "0001-01-01T00:00:00Z"),
        "generatorURL": "http://prometheus.local/graph",
        "fingerprint": fingerprint,
    }


def am_payload(*alerts: dict[str, Any], status: str = "firing") -> dict[str, Any]:
    return {
        "version": "4",
        "groupKey": '{}:{alertname="x"}',
        "truncatedAlerts": 0,
        "status": status,
        "receiver": "serviszeka",
        "groupLabels": {},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager.local",
        "alerts": list(alerts),
    }


def signed_headers(body: bytes, *, ts: int | None = None, secret: str = SECRET) -> dict[str, str]:
    ts = int(datetime.now(UTC).timestamp()) if ts is None else ts
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-ServisZeka-Timestamp": str(ts),
        "X-ServisZeka-Signature": f"sha256={sig}",
        "Content-Type": "application/json",
    }


def token_headers(secret: str = SECRET) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def body_of(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


async def ci(session, name: str, ci_class: CIClass, criticality: int = 3, **kw) -> str:
    item = await cmdb.create_ci(
        session,
        CICreate(
            name=name, ci_class=ci_class, environment=CIEnvironment.PROD,
            criticality=criticality, **kw,
        ),
        "mudur",
    )  # fmt: skip
    return item.id


async def chain(session) -> dict[str, str]:
    """İş hizmeti (kritiklik 1) -> uygulama -> veritabanı -> sunucu zinciri (depends_on/runs_on)."""
    ids = {
        "svc": await ci(session, "odeme-hizmeti", CIClass.SERVICE, 1),
        "app": await ci(session, "odeme-api", CIClass.APPLICATION, 2),
        "db": await ci(session, "odeme-db", CIClass.DATABASE, 2),
        "host": await ci(session, "db-host-01", CIClass.SERVER, 3),
        "other": await ci(session, "yazici-sunucu", CIClass.SERVER, 4),
    }
    R = CIRelationshipType
    await cmdb.add_relationship(session, ids["svc"], ids["app"], R.DEPENDS_ON, "mudur")
    await cmdb.add_relationship(session, ids["app"], ids["db"], R.DEPENDS_ON, "mudur")
    await cmdb.add_relationship(session, ids["db"], ids["host"], R.RUNS_ON, "mudur")
    return ids


async def seed_p1_group(session, settings: Settings = EV_SETTINGS) -> str:
    """Kritiklik 1 CI'da kritik uyarı: açılacak olay P1 (event.create_incident R2)."""
    await ci(session, "core-db", CIClass.DATABASE, 1)
    alerts = events.parse_alertmanager(am_payload(am_alert("fp-core", ci="core-db")))
    res = await events.ingest(session, alerts, settings=settings, now=T0 + timedelta(seconds=5))
    await session.commit()
    return res.group_ids[0]


async def seed_low_group(session, settings: Settings = EV_SETTINGS) -> str:
    """Kritiklik 4 CI'da yüksek önemli uyarı: açılacak olay P4 (R1)."""
    await ci(session, "test-yazici", CIClass.SERVER, 4)
    alerts = events.parse_alertmanager(
        am_payload(am_alert("fp-yazici", severity="error", ci="test-yazici"))
    )
    res = await events.ingest(session, alerts, settings=settings, now=T0 + timedelta(seconds=5))
    await session.commit()
    return res.group_ids[0]

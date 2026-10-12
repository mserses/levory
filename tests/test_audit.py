"""Hash zincirli denetim günlüğü testleri."""

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update

from serviszeka import audit
from serviszeka.api.audit import router
from serviszeka.db import get_session
from serviszeka.models import AuditLog


async def _seed(session, n=3):
    rows = []
    for i in range(n):
        rows.append(
            await audit.record(
                session,
                actor="ai:service_desk" if i % 2 else "ayse",
                action="incident.update",
                entity_type="incident",
                entity_id=f"inc-{i}",
                details={"alan": "öncelik", "değer": i},
            )
        )
    return rows


async def test_empty_chain_is_valid(session):
    assert await audit.verify_chain(session) is True


async def test_genesis_and_linkage(session):
    rows = await _seed(session, 3)
    assert rows[0].prev_hash == audit.GENESIS_HASH
    assert rows[1].prev_hash == rows[0].hash
    assert rows[2].prev_hash == rows[1].hash
    assert all(len(r.hash) == 64 for r in rows)
    assert await audit.verify_chain(session) is True


async def test_chain_valid_after_commit_and_reload(session):
    await _seed(session, 3)
    await session.commit()
    session.expire_all()
    assert await audit.verify_chain(session) is True
    await audit.record(session, actor="x", action="a", entity_type="t", entity_id="1")
    await session.commit()
    session.expire_all()
    assert await audit.verify_chain(session) is True


async def test_tampered_details_detected(session):
    rows = await _seed(session, 3)
    await session.commit()
    await session.execute(
        update(AuditLog).where(AuditLog.seq == rows[1].seq).values(details={"alan": "sahte"})
    )
    await session.commit()
    session.expire_all()
    assert await audit.verify_chain(session) is False


async def test_tampered_hash_breaks_linkage(session):
    rows = await _seed(session, 3)
    await session.commit()
    # Satırın hash'i yeniden hesaplanıp yazılsa bile sonraki satırın bağı kopar.
    row = await session.get(AuditLog, rows[0].seq)
    row.actor = "saldirgan"
    row.hash = audit._row_hash(row)
    await session.commit()
    session.expire_all()
    assert await audit.verify_chain(session) is False


async def test_deleted_row_detected(session):
    rows = await _seed(session, 3)
    await session.commit()
    await session.delete(await session.get(AuditLog, rows[1].seq))
    await session.commit()
    session.expire_all()
    assert await audit.verify_chain(session) is False


async def test_api_list_and_verify(session):
    await _seed(session, 5)
    await session.commit()

    app = FastAPI()
    app.include_router(router)

    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/audit", params={"limit": 2, "offset": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 5
        seqs = [i["seq"] for i in body["items"]]
        all_seqs = sorted(
            (await session.scalars(select(AuditLog.seq))).all(), reverse=True
        )
        assert seqs == all_seqs[1:3]

        r = await client.get("/audit/verify")
        assert r.json() == {"valid": True}

        await session.execute(update(AuditLog).values(entity_id="degisti"))
        await session.commit()
        session.expire_all()
        r = await client.get("/audit/verify")
        assert r.json() == {"valid": False}

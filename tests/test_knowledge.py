"""Bilgi yönetimi modülü testleri."""

import httpx
import pytest
from fastapi import FastAPI

from serviszeka import audit, db, knowledge
from serviszeka.knowledge import tokenize, turkish_casefold


async def _seed(session):
    vpn = await knowledge.create_article(
        session,
        title="VPN bağlantı sorunu",
        body="Uzaktan erişimde VPN istemcisi bağlanmıyorsa sertifikayı yenileyin ve "
        "FortiClient uygulamasını yeniden başlatın.",
        category="network",
    )
    mail = await knowledge.create_article(
        session,
        title="Outlook e-posta senkronizasyonu",
        body="Outlook gelen kutusu güncellenmiyorsa profili onarın.",
        category="email",
    )
    printer = await knowledge.create_article(
        session,
        title="Yazıcı kağıt sıkışması",
        body="Yazıcı tepsisini açın ve sıkışan kağıdı çıkarın.",
        category="hardware",
    )
    return vpn, mail, printer


def test_turkish_casefold_and_tokenize():
    assert turkish_casefold("IŞIK İSTANBUL") == "ışık istanbul"
    assert tokenize("VPN'e bağlanamıyorum, ne yapmalıyım?") == ["vpn", "e", "bağlanamıyorum", "yapmalıyım"]
    assert "ve" not in tokenize("yazıcı ve tarayıcı")


async def test_numbering_increments(session):
    a = await knowledge.create_article(session, title="A", body="a")
    b = await knowledge.create_article(session, title="B", body="b", state="draft")
    c = await knowledge.create_article(session, title="C", body="c")
    assert [a.number, b.number, c.number] == ["KB0000001", "KB0000002", "KB0000003"]


async def test_vpn_query_finds_vpn_article(session):
    vpn, _, _ = await _seed(session)
    hits = await knowledge.search(session, "VPN bağlanamıyorum")
    assert hits and hits[0].article_id == vpn.id
    assert hits[0].number == vpn.number
    assert "VPN" in hits[0].snippet
    assert all(h.score > 0 for h in hits)
    assert hits == sorted(hits, key=lambda h: -h.score)


async def test_turkish_dotted_i_query(session):
    _, _, printer = await _seed(session)
    hits = await knowledge.search(session, "YAZICI KAĞIT SIKIŞMASI")
    assert hits[0].article_id == printer.id


async def test_prefix_matching_without_exact_term(session):
    vpn, _, _ = await _seed(session)
    # 'bağlantısı' tam olarak geçmiyor; önek eşleşmesiyle bulunmalı
    hits = await knowledge.search(session, "bağlantısı kopuyor")
    assert hits and hits[0].article_id == vpn.id


async def test_draft_and_retired_not_searchable(session):
    draft = await knowledge.create_article(
        session, title="VPN taslak", body="VPN taslak içerik", state="draft"
    )
    assert await knowledge.search(session, "VPN") == []
    await knowledge.publish_article(session, draft.id)
    assert [h.article_id for h in await knowledge.search(session, "VPN")] == [draft.id]
    await knowledge.retire_article(session, draft.id)
    assert await knowledge.search(session, "VPN") == []


async def test_known_error_boost(session):
    normal = await knowledge.create_article(
        session, title="SAP oturum hatası", body="SAP girişinde zaman aşımı oluşuyor."
    )
    known = await knowledge.create_article(
        session,
        title="SAP oturum hatası",
        body="SAP girişinde zaman aşımı oluşuyor.",
        is_known_error=True,
        workaround="Önbelleği temizleyin.",
    )
    hits = await knowledge.search(session, "SAP oturum hatası")
    assert [h.article_id for h in hits[:2]] == [known.id, normal.id]
    assert hits[0].score > hits[1].score


async def test_irrelevant_query_and_empty_query(session):
    await _seed(session)
    assert await knowledge.search(session, "kuantum fiziği") == []
    assert await knowledge.search(session, "ve ile") == []


async def test_hybrid_embedding(session):
    class FakeEmbedder:
        async def embed(self, text: str) -> list[float]:
            return [0.0, 1.0]

    a = await knowledge.create_article(
        session, title="Parola sıfırlama", body="Parola portalını kullanın.", embedding=[1.0, 0.0]
    )
    b = await knowledge.create_article(
        session, title="Parola politikası", body="Parola en az 12 karakter.", embedding=[0.0, 1.0]
    )
    plain = await knowledge.search(session, "parola")
    assert {h.article_id for h in plain} == {a.id, b.id}
    hybrid = await knowledge.search(session, "parola", embedder=FakeEmbedder())
    assert hybrid[0].article_id == b.id
    assert isinstance(FakeEmbedder(), knowledge.Embedder)


async def test_update_list_and_audit(session):
    art = await knowledge.create_article(session, title="Eski", body="gövde", state="draft")
    await knowledge.update_article(session, art.id, {"title": "Yeni"}, actor="ayse")
    assert (await knowledge.get_article(session, art.id)).title == "Yeni"
    with pytest.raises(ValueError):
        await knowledge.update_article(session, art.id, {"number": "KB9"})
    with pytest.raises(knowledge.ArticleNotFoundError):
        await knowledge.update_article(session, "yok", {"title": "x"})
    assert len(await knowledge.list_articles(session, state="draft")) == 1
    assert await knowledge.list_articles(session, state="published") == []
    await knowledge.retire_article(session, art.id)
    assert await audit.verify_chain(session)


@pytest.fixture
async def client(session):
    from serviszeka.api.knowledge import router

    app = FastAPI()
    app.include_router(router)

    async def _override():
        async with db.get_sessionmaker()() as s:
            yield s

    app.dependency_overrides[db.get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_api_flow(client):
    r = await client.post(
        "/knowledge/articles",
        json={"title": "VPN bağlantı sorunu", "body": "VPN istemcisini yeniden başlatın."},
        headers={"X-Actor": "mehmet"},
    )
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["number"] == "KB0000001" and art["state"] == "draft"

    r = await client.get("/knowledge/search", params={"q": "VPN bağlanamıyorum"})
    assert r.json()["hits"] == []

    r = await client.post(f"/knowledge/articles/{art['id']}/publish")
    assert r.status_code == 200 and r.json()["state"] == "published"

    r = await client.get("/knowledge/search", params={"q": "VPN bağlanamıyorum"})
    hits = r.json()["hits"]
    assert hits[0]["article_id"] == art["id"]

    r = await client.patch(f"/knowledge/articles/{art['id']}", json={"category": "network"})
    assert r.json()["category"] == "network"

    r = await client.get(f"/knowledge/articles/{art['id']}")
    assert r.status_code == 200 and r.json()["category"] == "network"

    r = await client.get("/knowledge/articles", params={"state": "published"})
    assert len(r.json()) == 1

    assert (await client.get("/knowledge/articles/yok")).status_code == 404

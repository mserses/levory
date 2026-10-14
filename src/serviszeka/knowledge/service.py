"""Bilgi yönetimi (ITIL 4 Knowledge Management + KEDB) servis katmanı.

Fonksiyonlar flush eder, commit etmez; işlem sınırı çağırana aittir.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.models import KnowledgeArticle

STATE_DRAFT = "draft"
STATE_PUBLISHED = "published"
STATE_RETIRED = "retired"
STATES = frozenset({STATE_DRAFT, STATE_PUBLISHED, STATE_RETIRED})

NUMBER_PREFIX = "KB"
NUMBER_WIDTH = 7

_UPDATABLE = frozenset(
    {
        "title",
        "body",
        "category",
        "is_known_error",
        "workaround",
        "source_incident_ids",
        "valid_until",
        "embedding",
    }
)


# None ile temizlenemeyen alanlar (PATCH'te null gönderilirse yok sayılır)
_NON_NULLABLE = frozenset({"title", "body", "is_known_error", "source_incident_ids"})


class ArticleNotFoundError(LookupError):
    pass


class InvalidStateTransitionError(ValueError):
    pass


async def _next_number(session: AsyncSession) -> str:
    last = await session.scalar(
        select(func.max(KnowledgeArticle.number)).where(
            KnowledgeArticle.number.like(f"{NUMBER_PREFIX}%")
        )
    )
    n = 0
    if last:
        try:
            n = int(last[len(NUMBER_PREFIX) :])
        except ValueError:
            n = 0
    return f"{NUMBER_PREFIX}{n + 1:0{NUMBER_WIDTH}d}"


async def create_article(
    session: AsyncSession,
    *,
    title: str,
    body: str,
    category: str | None = None,
    is_known_error: bool = False,
    workaround: str | None = None,
    source_incident_ids: list[str] | None = None,
    state: str = STATE_PUBLISHED,
    embedding: list[float] | None = None,
    valid_until: datetime | None = None,
    actor: str = "system",
) -> KnowledgeArticle:
    """Yeni makale oluşturur (KB0000001, KB0000002, ...)."""
    if state not in STATES:
        raise ValueError(f"Geçersiz durum: {state}")
    article = KnowledgeArticle(
        number=await _next_number(session),
        title=title,
        body=body,
        category=category,
        is_known_error=is_known_error,
        workaround=workaround,
        source_incident_ids=list(source_incident_ids or []),
        state=state,
        embedding=embedding,
        valid_until=valid_until,
    )
    session.add(article)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="knowledge.create",
        entity_type="knowledge_article",
        entity_id=article.id,
        details={"number": article.number, "state": state, "is_known_error": is_known_error},
    )
    return article


async def get_article(session: AsyncSession, article_id: str) -> KnowledgeArticle | None:
    return await session.get(KnowledgeArticle, article_id)


async def _require(session: AsyncSession, article_id: str) -> KnowledgeArticle:
    article = await get_article(session, article_id)
    if article is None:
        raise ArticleNotFoundError(f"Makale bulunamadı: {article_id}")
    return article


async def list_articles(
    session: AsyncSession,
    *,
    state: str | None = None,
    category: str | None = None,
    is_known_error: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[KnowledgeArticle]:
    stmt = select(KnowledgeArticle)
    if state is not None:
        stmt = stmt.where(KnowledgeArticle.state == state)
    if category is not None:
        stmt = stmt.where(KnowledgeArticle.category == category)
    if is_known_error is not None:
        stmt = stmt.where(KnowledgeArticle.is_known_error == is_known_error)
    stmt = stmt.order_by(KnowledgeArticle.number).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def update_article(
    session: AsyncSession,
    article_id: str,
    data: dict[str, Any] | Any,
    actor: str = "system",
) -> KnowledgeArticle:
    """Makale alanlarını günceller. `data` dict veya pydantic modeli olabilir."""
    if hasattr(data, "model_dump"):
        data = data.model_dump(exclude_unset=True)
    article = await _require(session, article_id)
    changed: dict[str, Any] = {}
    for key, value in data.items():
        if key not in _UPDATABLE:
            raise ValueError(f"Güncellenemez alan: {key}")
        if value is None and key in _NON_NULLABLE:
            continue
        if getattr(article, key) != value:
            setattr(article, key, value)
            changed[key] = value if key != "embedding" else "<vector>"
    if changed:
        await session.flush()
        await audit.record(
            session,
            actor=actor,
            action="knowledge.update",
            entity_type="knowledge_article",
            entity_id=article.id,
            details={"fields": sorted(changed)},
        )
    return article


async def _transition(
    session: AsyncSession, article_id: str, target: str, allowed_from: set[str], actor: str
) -> KnowledgeArticle:
    article = await _require(session, article_id)
    if article.state == target:
        return article
    if article.state not in allowed_from:
        raise InvalidStateTransitionError(f"{article.state} -> {target} geçişine izin verilmez")
    previous = article.state
    article.state = target
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action=f"knowledge.{'publish' if target == STATE_PUBLISHED else 'retire'}",
        entity_type="knowledge_article",
        entity_id=article.id,
        details={"from": previous, "to": target},
    )
    return article


async def publish_article(
    session: AsyncSession, article_id: str, actor: str = "system"
) -> KnowledgeArticle:
    """Taslak (veya arşivlenmiş) makaleyi yayına alır."""
    return await _transition(
        session, article_id, STATE_PUBLISHED, {STATE_DRAFT, STATE_RETIRED}, actor
    )


async def retire_article(
    session: AsyncSession, article_id: str, actor: str = "system"
) -> KnowledgeArticle:
    """Makaleyi arşive kaldırır; aramada artık görünmez."""
    return await _transition(
        session, article_id, STATE_RETIRED, {STATE_DRAFT, STATE_PUBLISHED}, actor
    )

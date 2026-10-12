"""Bilgi bankası (Knowledge Management + KEDB) uç noktaları."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import knowledge
from serviszeka.auth import AgentUser, ReaderUser, get_current_user
from serviszeka.db import get_session
from serviszeka.knowledge.schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleState,
    ArticleUpdate,
    SearchHitOut,
    SearchResponse,
)

router = APIRouter(
    prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(get_current_user)]
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _get_or_404(session: AsyncSession, article_id: str):
    article = await knowledge.get_article(session, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Makale bulunamadı")
    return article


@router.post("/articles", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
async def create_article(
    data: ArticleCreate,
    session: SessionDep,
    user: AgentUser,
) -> ArticleOut:
    article = await knowledge.create_article(session, **data.model_dump(), actor=user.username)
    await session.commit()
    await session.refresh(article)
    return ArticleOut.model_validate(article)


@router.get("/articles", response_model=list[ArticleOut])
async def list_articles(
    session: SessionDep,
    _user: ReaderUser,
    state: ArticleState | None = None,
    category: str | None = None,
    is_known_error: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ArticleOut]:
    rows = await knowledge.list_articles(
        session,
        state=state,
        category=category,
        is_known_error=is_known_error,
        limit=limit,
        offset=offset,
    )
    return [ArticleOut.model_validate(r) for r in rows]


@router.get("/search", response_model=SearchResponse)
async def search_articles(
    session: SessionDep,
    _user: ReaderUser,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    category: str | None = None,
) -> SearchResponse:
    hits = await knowledge.search(session, q, limit, category=category)
    return SearchResponse(query=q, hits=[SearchHitOut.model_validate(h) for h in hits])


@router.get("/articles/{article_id}", response_model=ArticleOut)
async def get_article(article_id: str, session: SessionDep, _user: ReaderUser) -> ArticleOut:
    return ArticleOut.model_validate(await _get_or_404(session, article_id))


@router.patch("/articles/{article_id}", response_model=ArticleOut)
async def update_article(
    article_id: str,
    data: ArticleUpdate,
    session: SessionDep,
    user: AgentUser,
) -> ArticleOut:
    await _get_or_404(session, article_id)
    article = await knowledge.update_article(session, article_id, data, actor=user.username)
    await session.commit()
    await session.refresh(article)
    return ArticleOut.model_validate(article)


async def _transition(session: AsyncSession, article_id: str, fn, actor: str) -> ArticleOut:
    await _get_or_404(session, article_id)
    try:
        article = await fn(session, article_id, actor=actor)
    except knowledge.InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await session.refresh(article)
    return ArticleOut.model_validate(article)


@router.post("/articles/{article_id}/publish", response_model=ArticleOut)
async def publish_article(
    article_id: str,
    session: SessionDep,
    user: AgentUser,
) -> ArticleOut:
    return await _transition(session, article_id, knowledge.publish_article, user.username)


@router.post("/articles/{article_id}/retire", response_model=ArticleOut)
async def retire_article(
    article_id: str,
    session: SessionDep,
    user: AgentUser,
) -> ArticleOut:
    return await _transition(session, article_id, knowledge.retire_article, user.username)

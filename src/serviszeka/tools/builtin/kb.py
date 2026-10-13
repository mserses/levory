"""Bilgi araçları: kb.* ve known_error.create (serviszeka.knowledge üzerinden)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from serviszeka import knowledge
from serviszeka.domain.enums import RiskClass
from serviszeka.knowledge.schemas import ArticleOut, SearchHitOut
from serviszeka.models import KnowledgeArticle
from serviszeka.tools.builtin._common import READERS, WRITERS, Args, Id
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolExecutionError, ToolNotFoundError, ToolSpec


async def _require(ctx: ToolContext, article_id: str) -> KnowledgeArticle:
    article = await knowledge.get_article(ctx.session, article_id)
    if article is None:
        raise ToolNotFoundError(f"Makale bulunamadı: {article_id}")
    return article


async def _read(ctx: ToolContext, article: KnowledgeArticle) -> dict[str, Any]:
    await ctx.session.refresh(article)
    return ArticleOut.model_validate(article).model_dump(mode="json")


class _Ref(Args):
    article_id: Id = Field(description="Makale kimliği (id)")


class SearchArgs(Args):
    query: str = Field(min_length=1, max_length=500, description="Arama metni")
    limit: int = Field(default=5, ge=1, le=20)
    category: str | None = Field(default=None, max_length=100)


async def search(ctx: ToolContext, args: SearchArgs) -> dict[str, Any]:
    hits = await knowledge.search(ctx.session, args.query, args.limit, category=args.category)
    return {
        "query": args.query,
        "hits": [SearchHitOut.model_validate(h).model_dump(mode="json") for h in hits],
    }


async def get(ctx: ToolContext, args: _Ref) -> dict[str, Any]:
    return await _read(ctx, await _require(ctx, args.article_id))


class DraftArgs(Args):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=50000)
    category: str | None = Field(default=None, max_length=100)
    source_incident_ids: list[Id] = Field(default_factory=list, max_length=50)


async def draft_article(ctx: ToolContext, args: DraftArgs) -> dict[str, Any]:
    article = await knowledge.create_article(
        ctx.session, **args.model_dump(), state=knowledge.STATE_DRAFT, actor=ctx.actor.username
    )
    return await _read(ctx, article)


class UpdateDraftArgs(_Ref):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=50000)
    category: str | None = Field(default=None, max_length=100)
    workaround: str | None = Field(default=None, max_length=20000)
    source_incident_ids: list[Id] | None = Field(default=None, max_length=50)


async def update_draft(ctx: ToolContext, args: UpdateDraftArgs) -> dict[str, Any]:
    article = await _require(ctx, args.article_id)
    if article.state != knowledge.STATE_DRAFT:
        # Yayındaki içeriği değiştirmek taslak düzenleme değildir
        raise ToolExecutionError(f"Yalnızca taslak makale düzenlenebilir (durum: {article.state}).")
    data = args.model_dump(exclude={"article_id"}, exclude_unset=True)
    article = await knowledge.update_article(
        ctx.session, article.id, data, actor=ctx.actor.username
    )
    return await _read(ctx, article)


async def publish(ctx: ToolContext, args: _Ref) -> dict[str, Any]:
    await _require(ctx, args.article_id)
    article = await knowledge.publish_article(
        ctx.session, args.article_id, actor=ctx.actor.username
    )
    return await _read(ctx, article)


async def retire(ctx: ToolContext, args: _Ref) -> dict[str, Any]:
    await _require(ctx, args.article_id)
    article = await knowledge.retire_article(ctx.session, args.article_id, actor=ctx.actor.username)
    return await _read(ctx, article)


class KnownErrorArgs(Args):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=50000, description="Belirtiler ve kök neden")
    workaround: str = Field(min_length=1, max_length=20000, description="Geçici çözüm")
    category: str | None = Field(default=None, max_length=100)
    source_incident_ids: list[Id] = Field(default_factory=list, max_length=50)


async def create_known_error(ctx: ToolContext, args: KnownErrorArgs) -> dict[str, Any]:
    article = await knowledge.create_article(
        ctx.session,
        **args.model_dump(),
        is_known_error=True,
        state=knowledge.STATE_DRAFT,
        actor=ctx.actor.username,
    )
    return await _read(ctx, article)


def register_all() -> None:
    R0, R1, R2 = RiskClass.R0, RiskClass.R1, RiskClass.R2
    for spec in (
        ToolSpec("kb.search", "Yayındaki bilgi bankası makalelerinde arar (bilinen hatalar öne çıkar).",
                 R0, SearchArgs, search, READERS),
        ToolSpec("kb.get", "Bir bilgi makalesini getirir.", R0, _Ref, get, READERS),
        ToolSpec("kb.draft_article", "Taslak bilgi makalesi oluşturur (yayımlanmaz).", R1, DraftArgs,
                 draft_article, WRITERS),
        ToolSpec("kb.update_draft", "Taslak makaleyi düzenler; yayındaki makale değiştirilemez.", R1,
                 UpdateDraftArgs, update_draft, WRITERS),
        ToolSpec("kb.publish", "Makaleyi yayımlar (herkes görür; insan onayı).", R2, _Ref, publish,
                 WRITERS),
        ToolSpec("kb.retire", "Makaleyi arşive kaldırır (insan onayı).", R2, _Ref, retire, WRITERS),
        ToolSpec("known_error.create", "Bilinen hata (KEDB) kaydını geçici çözümüyle taslak olarak "
                 "oluşturur.", R1, KnownErrorArgs, create_known_error, WRITERS),
    ):  # fmt: skip
        registry.register(spec)

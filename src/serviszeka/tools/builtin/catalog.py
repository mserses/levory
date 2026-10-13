"""Hizmet kataloğu ve hizmet talebi araçları: catalog.*, request.* (services/catalog, requests).

Talep araçları karşılama motorunu çağırır; plan adımları her zaman `ai:fulfilment` aktörüyle ve
yine `execute_tool` üzerinden çalışır (adımın kendi risk sınıfı ve politikası geçerlidir). İnsan
çağıran `request.run_fulfillment_step` / `request.complete` talebi insansız saymaz.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from serviszeka.auth import ROLE_AGENT, ROLE_APPROVER
from serviszeka.domain.enums import RiskClass
from serviszeka.services import catalog as catalog_service
from serviszeka.services import requests as request_service
from serviszeka.services.request_schemas import (
    CatalogItemRead,
    LinkedActionRead,
    ServiceRequestRead,
)
from serviszeka.tools.builtin._common import WRITERS, Args, Id
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolNotFoundError, ToolSpec

# Self-servis kullanıcılar (requester) kataloğu REST /catalog üzerinden görür; MCP araçları yok
CATALOG_READERS = frozenset({ROLE_AGENT, ROLE_APPROVER})
REQUEST_READERS = frozenset({ROLE_AGENT, ROLE_APPROVER})
LINK = {"request_id": "request"}


class ListItemsArgs(Args):
    q: str | None = Field(default=None, max_length=200, description="Ad/açıklamada ara")
    category: str | None = Field(default=None, max_length=100)
    standard: bool | None = Field(default=None, description="Yalnızca standart (önceden onaylı)")


async def list_items(ctx: ToolContext, args: ListItemsArgs) -> dict[str, Any]:
    rows = await catalog_service.list_items(
        ctx.session, category=args.category, standard=args.standard, q=args.q
    )
    items = [
        {
            "id": r.id,
            "key": r.key,
            "name": r.name,
            "description": r.description,
            "category": r.category,
            "standard": r.standard,
            "approval_policy": r.approval_policy.value,
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


class ItemRef(Args):
    item: str = Field(min_length=1, max_length=100, description="Katalog öğesi kimliği ya da anahtarı")


async def get_item(ctx: ToolContext, args: ItemRef) -> dict[str, Any]:
    item = await catalog_service.get_item(ctx.session, args.item)
    if item is None or not item.active:
        raise ToolNotFoundError(f"Katalog öğesi bulunamadı: {args.item}")
    return CatalogItemRead.model_validate(item).model_dump(mode="json")


async def _read(ctx: ToolContext, req) -> dict[str, Any]:
    actions = await request_service.linked_actions(ctx.session, req.id)
    data = ServiceRequestRead.model_validate(req).model_copy(
        update={"actions": [LinkedActionRead.model_validate(a) for a in actions]}
    )
    return data.model_dump(mode="json")


class CreateArgs(Args):
    item: str = Field(min_length=1, max_length=100, description="Katalog öğesi kimliği ya da anahtarı")
    requester: str = Field(min_length=1, max_length=200, description="Talep eden kullanıcı (UPN)")
    form_values: dict[str, Any] = Field(default_factory=dict, description="Form alanları")


async def create(ctx: ToolContext, args: CreateArgs) -> dict[str, Any]:
    item = await catalog_service.get_item(ctx.session, args.item)
    if item is None:
        raise ToolNotFoundError(f"Katalog öğesi bulunamadı: {args.item}")
    req = await request_service.create_request(
        ctx.session,
        item,
        requester=args.requester,
        opened_by=ctx.actor,
        form_values=args.form_values,
        settings=ctx.settings,
        now=ctx.now,
        llm=ctx.llm,
    )
    return await _read(ctx, req)


class RequestRef(Args):
    request_id: Id = Field(description="Hizmet talebi kimliği (id)")


async def get(ctx: ToolContext, args: RequestRef) -> dict[str, Any]:
    req = await request_service.get_request(ctx.session, args.request_id)
    if req is None:
        raise ToolNotFoundError(f"Hizmet talebi bulunamadı: {args.request_id}")
    return await _read(ctx, req)


async def run_fulfillment_step(ctx: ToolContext, args: RequestRef) -> dict[str, Any]:
    req = await request_service.resume(
        ctx.session, args.request_id, ctx.actor, settings=ctx.settings, now=ctx.now, llm=ctx.llm
    )
    return await _read(ctx, req)


class CompleteArgs(RequestRef):
    note: str = Field(default="", max_length=2000, description="Ne yapıldığı (denetime yazılır)")


async def complete(ctx: ToolContext, args: CompleteArgs) -> dict[str, Any]:
    req = await request_service.complete_manual_step(
        ctx.session, args.request_id, ctx.actor, args.note, settings=ctx.settings, now=ctx.now,
        llm=ctx.llm,
    )
    return await _read(ctx, req)


def register_all() -> None:
    R0, R1 = RiskClass.R0, RiskClass.R1
    for spec in (
        ToolSpec("catalog.list_items", "Etkin hizmet kataloğu öğelerini listeler.", R0,
                 ListItemsArgs, list_items, CATALOG_READERS),
        ToolSpec("catalog.get_item", "Katalog öğesinin form şemasını, onay politikasını ve "
                 "karşılama planını getirir.", R0, ItemRef, get_item, CATALOG_READERS),
        ToolSpec("request.create", "Kullanıcı adına hizmet talebi açar; iş onayı gerekmiyorsa "
                 "karşılama hemen başlar (her adım kendi risk sınıfıyla değerlendirilir; R2/R3 adım "
                 "insan onayı bekler).", R1, CreateArgs, create, WRITERS),
        ToolSpec("request.get", "Hizmet talebini, adım sonuçlarını ve bağlı eylem isteklerini "
                 "getirir.", R0, RequestRef, get, REQUEST_READERS, links=LINK),
        ToolSpec("request.run_fulfillment_step", "Karşılanmakta olan talebin sıradaki adımını "
                 "çalıştırır / sonuçlanan eylem isteğini işler.", R1, RequestRef,
                 run_fulfillment_step, WRITERS, links=LINK),
        ToolSpec("request.complete", "Talebin sıradaki el ile adımını tamamlandı işaretler ve "
                 "karşılamayı sürdürür (el ile adımlı talep insansız sayılmaz).", R1, CompleteArgs,
                 complete, WRITERS, links=LINK),
    ):  # fmt: skip
        registry.register(spec)

"""Hizmet kataloğu: form şeması, karşılama planı şablonları ve varsayılan (tohum) öğeler.

Form şeması JSON şemasının küçük bir alt kümesidir::

    {"type": "object",
     "properties": {
        "group": {"type": "string", "format": "group", "title": "Grup"},     # grup seçici
        "mailbox": {"type": "string", "format": "user", "title": "Posta kutusu"},  # kullanıcı seçici
        "access": {"type": "string", "enum": ["full_access", "send_as"], "title": "Erişim"},
        "remote": {"type": "boolean", "title": "Uzaktan mı?", "default": false},
        "justification": {"type": "string", "title": "Gerekçe", "maxLength": 500}},
     "required": ["group"]}

Karşılama planı sıralı adımlardır. Araç adımı::

    {"tool": "identity.add_to_group", "title": "Gruba ekle",
     "args": {"user": "{{requester}}", "group": "{{form.group}}"}, "when": "form.group"}

El ile adım (insan işi; talebi insansız saymaz): ``{"manual": true, "title": "Cihazı teslim et"}``.
Şablon yer tutucuları: ``{{requester}}``, ``{{request.id}}``, ``{{request.number}}``,
``{{form.<alan>}}``. Değerin tamamı tek yer tutucuysa ham değer (bool/None) geçer; boş (None)
değerli argüman atlanır (aracın varsayılanı geçerli olur). ``when``: yer tutucu adı; değeri boş /
false ise adım atlanır. Plan katalog/talep araçlarını (``catalog.*``, ``request.*``) içeremez.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit
from serviszeka.domain.enums import CatalogApprovalPolicy
from serviszeka.models import CatalogItem

FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,99}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_.]+)\s*\}\}")
PICKER_FORMATS = frozenset({"user", "group"})
MAX_FIELDS = 30
MAX_STEPS = 20
DEFAULT_MAX_LENGTH = 500
PICKER_MAX_LENGTH = 200
FORBIDDEN_STEP_DOMAINS = frozenset({"catalog", "request"})
_STRING_KEYS = {"type", "title", "description", "enum", "format", "maxLength", "minLength", "default"}
_BOOL_KEYS = {"type", "title", "description", "default"}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class CatalogError(ValueError):
    """Katalog öğesi tanımı ya da form değerleri geçersiz (422)."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class CatalogNotFoundError(LookupError):
    pass


class CatalogVersionConflictError(Exception):
    pass


# --- form şeması ------------------------------------------------------------------------------


def validate_form_schema(schema: Any) -> dict[str, Any]:
    """Form şeması tanımını denetler; geçersizse `CatalogError`."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        raise CatalogError("Form şeması nesne olmalı.")
    if set(schema) - {"type", "properties", "required", "title", "description"}:
        errors.append(f"Desteklenmeyen şema anahtarları: {sorted(set(schema) - {'type', 'properties', 'required', 'title', 'description'})}")
    if schema.get("type", "object") != "object":
        errors.append("Form şeması type=object olmalı.")
    props = schema.get("properties", {})
    if not isinstance(props, dict) or len(props) > MAX_FIELDS:
        raise CatalogError(f"properties en fazla {MAX_FIELDS} alanlı nesne olmalı.")
    for name, spec in props.items():
        if not FIELD_NAME_RE.match(name):
            errors.append(f"Geçersiz alan adı: {name!r}")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{name}: alan tanımı nesne olmalı")
            continue
        kind = spec.get("type")
        if kind == "boolean":
            extra = set(spec) - _BOOL_KEYS
            if extra:
                errors.append(f"{name}: desteklenmeyen anahtarlar {sorted(extra)}")
            if "default" in spec and not isinstance(spec["default"], bool):
                errors.append(f"{name}: varsayılan boolean olmalı")
        elif kind == "string":
            extra = set(spec) - _STRING_KEYS
            if extra:
                errors.append(f"{name}: desteklenmeyen anahtarlar {sorted(extra)}")
            fmt = spec.get("format")
            if fmt is not None and fmt not in PICKER_FORMATS:
                errors.append(f"{name}: format yalnızca user/group olabilir")
            enum = spec.get("enum")
            if enum is not None and (
                not isinstance(enum, list)
                or not enum
                or len(enum) > 50
                or not all(isinstance(v, str) and v for v in enum)
            ):
                errors.append(f"{name}: enum 1-50 metin değer olmalı")
            for key in ("maxLength", "minLength"):
                if key in spec and (
                    not isinstance(spec[key], int) or isinstance(spec[key], bool)
                    or not 0 <= spec[key] <= 4000
                ):
                    errors.append(f"{name}: {key} 0-4000 tam sayı olmalı")
            if "default" in spec and not isinstance(spec["default"], str):
                errors.append(f"{name}: varsayılan metin olmalı")
        else:
            errors.append(f"{name}: type string ya da boolean olmalı")
        for key in ("title", "description"):
            if key in spec and not isinstance(spec[key], str):
                errors.append(f"{name}: {key} metin olmalı")
    required = schema.get("required", [])
    if not isinstance(required, list) or not set(required) <= set(props):
        errors.append("required yalnızca tanımlı alanları içermeli.")
    if errors:
        raise CatalogError("Form şeması geçersiz.", errors)
    return {"type": "object", **{k: v for k, v in schema.items() if k != "type"}}


def validate_form(schema: dict[str, Any], values: Any) -> dict[str, Any]:
    """Form değerlerini şemaya göre doğrular; varsayılanları uygular, metinleri kırpar."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise CatalogError("Form değerleri nesne olmalı.")
    props: dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    errors: list[str] = []
    unknown = sorted(set(values) - set(props))
    if unknown:
        errors.append(f"Bilinmeyen alanlar: {', '.join(unknown)}")
    clean: dict[str, Any] = {}
    for name, spec in props.items():
        title = spec.get("title") or name
        value = values.get(name, spec.get("default"))
        if spec.get("type") == "boolean":
            if value is None:
                value = False
            if not isinstance(value, bool):
                errors.append(f"{title}: evet/hayır olmalı")
                continue
            if name in required and value is False and "default" not in spec:
                # Zorunlu boolean (ör. onay kutusu) işaretli olmalı
                errors.append(f"{title}: işaretlenmeli")
                continue
            clean[name] = value
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            if name in required:
                errors.append(f"{title}: zorunlu")
            continue
        if not isinstance(value, str):
            errors.append(f"{title}: metin olmalı")
            continue
        value = value.strip()
        fmt = spec.get("format")
        limit = spec.get("maxLength", PICKER_MAX_LENGTH if fmt else DEFAULT_MAX_LENGTH)
        if len(value) > limit:
            errors.append(f"{title}: en fazla {limit} karakter")
            continue
        if len(value) < spec.get("minLength", 0):
            errors.append(f"{title}: en az {spec['minLength']} karakter")
            continue
        if fmt and _CONTROL_RE.search(value):
            errors.append(f"{title}: geçersiz karakter")
            continue
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{title}: izin verilen değerlerden biri olmalı ({', '.join(enum)})")
            continue
        clean[name] = value
    if errors:
        raise CatalogError("Form değerleri geçersiz: " + "; ".join(errors), errors)
    return clean


# --- karşılama planı ---------------------------------------------------------------------------


def _placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(PLACEHOLDER_RE.findall(value))
    if isinstance(value, dict):
        return set().union(*(_placeholders(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_placeholders(v) for v in value)) if value else set()
    return set()


def allowed_placeholders(schema: dict[str, Any]) -> set[str]:
    fields = set((schema.get("properties") or {}).keys())
    return {"requester", "request.id", "request.number"} | {f"form.{f}" for f in fields}


def validate_plan(plan: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan adımlarını denetler: araç kayıtlı, argüman adları araç şemasında, yer tutucular geçerli."""
    from serviszeka.tools import registry

    if not isinstance(plan, list) or not 1 <= len(plan) <= MAX_STEPS:
        raise CatalogError(f"Karşılama planı 1-{MAX_STEPS} adımlı liste olmalı.")
    allowed = allowed_placeholders(schema)
    errors: list[str] = []
    clean: list[dict[str, Any]] = []
    for i, step in enumerate(plan, start=1):
        if not isinstance(step, dict):
            errors.append(f"Adım {i}: nesne olmalı")
            continue
        title = step.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            errors.append(f"Adım {i}: başlık (title) 1-200 karakter olmalı")
        when = step.get("when")
        if when is not None and when not in allowed:
            errors.append(f"Adım {i}: when geçersiz ({when})")
        if step.get("manual"):
            extra = set(step) - {"manual", "title", "when"}
            if extra:
                errors.append(f"Adım {i}: el ile adımda desteklenmeyen anahtarlar {sorted(extra)}")
            clean.append({k: step[k] for k in ("manual", "title", "when") if k in step})
            continue
        extra = set(step) - {"tool", "title", "args", "when"}
        if extra:
            errors.append(f"Adım {i}: desteklenmeyen anahtarlar {sorted(extra)}")
        tool = step.get("tool")
        spec = registry.find(tool) if isinstance(tool, str) else None
        if spec is None:
            errors.append(f"Adım {i}: araç kayıtlı değil ({tool})")
            continue
        if spec.domain in FORBIDDEN_STEP_DOMAINS:
            errors.append(f"Adım {i}: plan {spec.domain}.* araçlarını çağıramaz")
        args = step.get("args", {})
        if not isinstance(args, dict):
            errors.append(f"Adım {i}: args nesne olmalı")
            continue
        unknown = set(args) - set(spec.input_model.model_fields)
        if unknown:
            errors.append(f"Adım {i}: {tool} bu argümanları almaz: {sorted(unknown)}")
        bad = _placeholders(args) - allowed
        if bad:
            errors.append(f"Adım {i}: geçersiz yer tutucular {sorted(bad)}")
        clean.append({k: step[k] for k in ("tool", "title", "args", "when") if k in step})
    if errors:
        raise CatalogError("Karşılama planı geçersiz.", errors)
    return clean


def template_context(
    *, requester: str, request_id: str, request_number: str, form: dict[str, Any]
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "requester": requester,
        "request.id": request_id,
        "request.number": request_number,
    }
    ctx.update({f"form.{k}": v for k, v in form.items()})
    return ctx


def _render(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = PLACEHOLDER_RE.fullmatch(value.strip())
        if whole:
            return ctx.get(whole.group(1))
        return PLACEHOLDER_RE.sub(
            lambda m: "" if ctx.get(m.group(1)) is None else str(ctx.get(m.group(1))), value
        )
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, ctx) for v in value]
    return value


def render_args(template: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Adım argümanlarını üretir; değeri None olan argüman atlanır."""
    rendered = {k: _render(v, ctx) for k, v in (template or {}).items()}
    return {k: v for k, v in rendered.items() if v is not None}


def step_enabled(step: dict[str, Any], ctx: dict[str, Any]) -> bool:
    when = step.get("when")
    if not when:
        return True
    value = ctx.get(when)
    return bool(value.strip()) if isinstance(value, str) else bool(value)


# --- CRUD ----------------------------------------------------------------------------------------


class CatalogItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=2, max_length=100, pattern=KEY_RE.pattern)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    category: str = Field(min_length=1, max_length=100)
    form_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    approval_policy: CatalogApprovalPolicy = CatalogApprovalPolicy.NONE
    approval_group: str | None = Field(default=None, max_length=200)
    fulfilment_plan: list[dict[str, Any]]
    standard: bool = False
    active: bool = True


class CatalogItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    form_schema: dict[str, Any] | None = None
    approval_policy: CatalogApprovalPolicy | None = None
    approval_group: str | None = Field(default=None, max_length=200)
    fulfilment_plan: list[dict[str, Any]] | None = None
    standard: bool | None = None
    active: bool | None = None


def _check_definition(
    schema: dict[str, Any], plan: list[dict[str, Any]], policy: CatalogApprovalPolicy, group
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    schema = validate_form_schema(schema)
    plan = validate_plan(plan, schema)
    if CatalogApprovalPolicy(policy) == CatalogApprovalPolicy.GROUP and not (group or "").strip():
        raise CatalogError("approval_policy=group için approval_group zorunlu.")
    return schema, plan


async def get_item(session: AsyncSession, item_id_or_key: str) -> CatalogItem | None:
    item = await session.get(CatalogItem, item_id_or_key)
    if item is None:
        item = await session.scalar(select(CatalogItem).where(CatalogItem.key == item_id_or_key))
    return item


async def list_items(
    session: AsyncSession,
    *,
    category: str | None = None,
    standard: bool | None = None,
    include_inactive: bool = False,
    q: str | None = None,
) -> list[CatalogItem]:
    stmt = select(CatalogItem)
    if not include_inactive:
        stmt = stmt.where(CatalogItem.active.is_(True))
    if category is not None:
        stmt = stmt.where(CatalogItem.category == category)
    if standard is not None:
        stmt = stmt.where(CatalogItem.standard.is_(standard))
    rows = list((await session.scalars(stmt.order_by(CatalogItem.category, CatalogItem.name))).all())
    if q:
        needle = q.casefold()
        rows = [r for r in rows if needle in r.name.casefold() or needle in r.description.casefold()]
    return rows


async def create_item(
    session: AsyncSession, data: CatalogItemInput, actor: str, *, now: datetime | None = None
) -> CatalogItem:
    now = now or datetime.now(UTC)
    schema, plan = _check_definition(
        data.form_schema, data.fulfilment_plan, data.approval_policy, data.approval_group
    )
    if await get_item(session, data.key) is not None:
        raise CatalogError(f"Bu anahtarla katalog öğesi zaten var: {data.key}")
    item = CatalogItem(
        key=data.key,
        name=data.name,
        description=data.description,
        category=data.category,
        form_schema=schema,
        approval_policy=data.approval_policy,
        approval_group=(data.approval_group or None),
        fulfilment_plan=plan,
        standard=data.standard,
        active=data.active,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="catalog_item.create",
        entity_type="catalog_item",
        entity_id=item.id,
        details={"key": item.key, "standard": item.standard, "steps": [s.get("tool") for s in plan]},
    )
    return item


async def update_item(
    session: AsyncSession,
    item: CatalogItem,
    patch: CatalogItemPatch,
    actor: str,
    *,
    expected_version: int | None = None,
    now: datetime | None = None,
) -> CatalogItem:
    now = now or datetime.now(UTC)
    if expected_version is not None and expected_version != item.version:
        raise CatalogVersionConflictError(
            f"Katalog öğesi değişmiş (beklenen sürüm {expected_version}, güncel {item.version})."
        )
    fields = patch.model_dump(exclude_unset=True)
    schema = fields.get("form_schema", item.form_schema)
    plan = fields.get("fulfilment_plan", item.fulfilment_plan)
    policy = fields.get("approval_policy", item.approval_policy)
    group = fields.get("approval_group", item.approval_group)
    schema, plan = _check_definition(schema, plan, policy, group)
    for key, value in fields.items():
        setattr(item, key, value)
    item.form_schema, item.fulfilment_plan = schema, plan
    item.version += 1
    item.updated_at = now
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="catalog_item.update",
        entity_type="catalog_item",
        entity_id=item.id,
        details={"key": item.key, "fields": sorted(fields), "version": item.version},
    )
    return item


# --- varsayılan öğeler (tohum) -----------------------------------------------------------------------

_NOTIFY_DONE = {
    "tool": "notify.send_to_user",
    "title": "Talep edeni bilgilendir",
    "args": {
        "user": "{{requester}}",
        "subject": "{{request.number}} tamamlandı",
        "message": "{{request.number}} numaralı talebiniz karşılandı.",
    },
}


def default_items() -> list[dict[str, Any]]:
    """10 gerçekçi katalog öğesi. Grup/lisans/posta kutusu kimlikleri sahte dizinle (`demo`)
    uyumludur; üretimde kiracıdaki gerçek kimliklerle güncellenmelidir (PATCH /catalog/items)."""
    group_field = {"type": "string", "format": "group", "title": "Grup"}
    justification = {"type": "string", "title": "Gerekçe", "maxLength": 500}
    return [
        {
            "key": "password_reset",
            "name": "Şifre sıfırlama",
            "description": "Unuttuğunuz parolayı sıfırlar; geçici parola kayıtlı ikincil e-posta "
            "adresinize gönderilir ve ilk girişte değiştirmeniz istenir.",
            "category": "Kimlik ve erişim",
            "form_schema": {"type": "object", "properties": {}},
            "fulfilment_plan": [
                {
                    "tool": "identity.verify_requester",
                    "title": "Talep edenin kimliğini doğrula",
                    "args": {"request_id": "{{request.id}}", "user": "{{requester}}"},
                },
                {
                    "tool": "identity.reset_password",
                    "title": "Geçici parola ata ve ikincil adrese gönder",
                    "args": {"user": "{{requester}}", "request_id": "{{request.id}}"},
                },
            ],
            "standard": True,
        },
        {
            "key": "account_unlock",
            "name": "Hesap kilidi açma",
            "description": "Çok sayıda hatalı giriş nedeniyle kilitlenen hesabınızın kilidini açar.",
            "category": "Kimlik ve erişim",
            "form_schema": {"type": "object", "properties": {}},
            "fulfilment_plan": [
                {
                    "tool": "identity.verify_requester",
                    "title": "Talep edenin kimliğini doğrula",
                    "args": {"request_id": "{{request.id}}", "user": "{{requester}}"},
                },
                {
                    "tool": "identity.unlock_account",
                    "title": "Hesap kilidini aç",
                    "args": {"user": "{{requester}}", "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": True,
        },
        {
            "key": "standard_software_group",
            "name": "Standart yazılım grubu erişimi",
            "description": "Standart yazılım paketlerinin dağıtıldığı gruba eklenirsiniz.",
            "category": "Kimlik ve erişim",
            "form_schema": {
                "type": "object",
                "properties": {"group": {**group_field, "title": "Yazılım grubu"},
                               "justification": justification},
                "required": ["group"],
            },
            "fulfilment_plan": [
                {
                    "tool": "identity.add_to_group",
                    "title": "Yazılım grubuna ekle",
                    "args": {"user": "{{requester}}", "group": "{{form.group}}",
                             "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": True,
        },
        {
            "key": "shared_mailbox_access",
            "name": "Paylaşılan posta kutusu erişimi",
            "description": "Ekibinizin paylaşılan posta kutusuna erişim verilir (yönetici onayı ve "
            "BT onayı gerekir).",
            "category": "E-posta ve iş birliği",
            "form_schema": {
                "type": "object",
                "properties": {
                    "mailbox": {"type": "string", "format": "user", "title": "Posta kutusu"},
                    "access": {"type": "string", "enum": ["full_access", "send_as"],
                               "title": "Erişim türü", "default": "full_access"},
                    "justification": justification,
                },
                "required": ["mailbox"],
            },
            "approval_policy": "manager",
            "fulfilment_plan": [
                {
                    "tool": "mailbox.grant_shared_access",
                    "title": "Posta kutusu izni ver",
                    "args": {"mailbox": "{{form.mailbox}}", "user": "{{requester}}",
                             "access": "{{form.access}}", "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": False,
        },
        {
            "key": "m365_license",
            "name": "Microsoft 365 lisans talebi",
            "description": "Boş lisans varsa Microsoft 365 lisansı hesabınıza atanır.",
            "category": "Lisans",
            "form_schema": {
                "type": "object",
                "properties": {"sku": {"type": "string", "enum": ["SPE_E3", "POWER_BI_PRO"],
                                       "title": "Lisans", "default": "SPE_E3"}},
                "required": ["sku"],
            },
            "fulfilment_plan": [
                {"tool": "license.check_available", "title": "Boş lisansı denetle",
                 "args": {"sku": "{{form.sku}}"}},
                {
                    "tool": "license.assign",
                    "title": "Lisansı ata",
                    "args": {"user": "{{requester}}", "sku": "{{form.sku}}",
                             "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": True,
        },
        {
            "key": "teams_channel_access",
            "name": "Teams ekip kanalı erişimi",
            "description": "Bir Teams ekibine (ve kanallarına) üye olarak eklenirsiniz.",
            "category": "E-posta ve iş birliği",
            "form_schema": {
                "type": "object",
                "properties": {"team": {**group_field, "title": "Teams ekibi"},
                               "justification": justification},
                "required": ["team"],
            },
            "fulfilment_plan": [
                {
                    "tool": "identity.add_to_group",
                    "title": "Ekibin Microsoft 365 grubuna ekle",
                    "args": {"user": "{{requester}}", "group": "{{form.team}}",
                             "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": True,
        },
        {
            "key": "vpn_access",
            "name": "VPN erişim grubu",
            "description": "Uzaktan çalışma için VPN kullanıcıları grubuna eklenirsiniz.",
            "category": "Ağ erişimi",
            "form_schema": {
                "type": "object",
                "properties": {"justification": {**justification, "minLength": 3}},
                "required": ["justification"],
            },
            "fulfilment_plan": [
                {
                    "tool": "identity.add_to_group",
                    "title": "VPN grubuna ekle",
                    "args": {"user": "{{requester}}", "group": "sg-vpn-kullanicilari",
                             "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": True,
        },
        {
            "key": "new_employee_account",
            "name": "Yeni çalışan hesabı",
            "description": "İK sağlamasıyla açılan yeni çalışan hesabına lisans, standart gruplar ve "
            "departman posta kutusu erişimi hazırlanır; donanım teslimi el ile yapılır.",
            "category": "İşe giriş",
            "form_schema": {
                "type": "object",
                "properties": {
                    "user": {"type": "string", "format": "user", "title": "Yeni çalışanın hesabı"},
                    "mailbox": {"type": "string", "format": "user",
                                "title": "Departman posta kutusu"},
                    "role_group": {"type": "string", "format": "group",
                                   "title": "Ek yetki grubu (isteğe bağlı)"},
                },
                "required": ["user", "mailbox"],
            },
            "approval_policy": "group",
            "approval_group": "sg-ik-onaycilari",
            "fulfilment_plan": [
                {"tool": "license.assign", "title": "Microsoft 365 E3 lisansı ata",
                 "args": {"user": "{{form.user}}", "sku": "SPE_E3", "request_id": "{{request.id}}"}},
                {"tool": "identity.add_to_group", "title": "Standart yazılım grubuna ekle",
                 "args": {"user": "{{form.user}}", "group": "sg-yazilim-standart",
                          "request_id": "{{request.id}}"}},
                {"tool": "mailbox.grant_shared_access", "title": "Departman posta kutusu",
                 "args": {"mailbox": "{{form.mailbox}}", "user": "{{form.user}}",
                          "access": "full_access", "request_id": "{{request.id}}"}},
                {"tool": "identity.add_to_group", "title": "Ek yetki grubuna ekle",
                 "when": "form.role_group",
                 "args": {"user": "{{form.user}}", "group": "{{form.role_group}}",
                          "request_id": "{{request.id}}"}},
                {"manual": True, "title": "Dizüstü bilgisayarı hazırla ve teslim et"},
            ],
            "standard": False,
        },
        {
            "key": "privileged_group_access",
            "name": "Yetkili grup erişimi",
            "description": "Yönetici yetkisi veren (ayrıcalıklı) bir gruba erişim; yönetici onayı ve "
            "iki BT onayı gerekir.",
            "category": "Kimlik ve erişim",
            "form_schema": {
                "type": "object",
                "properties": {"group": {**group_field, "title": "Yetkili grup"},
                               "justification": {**justification, "minLength": 10}},
                "required": ["group", "justification"],
            },
            "approval_policy": "manager",
            "fulfilment_plan": [
                {
                    "tool": "identity.add_to_group",
                    "title": "Yetkili gruba ekle",
                    "args": {"user": "{{requester}}", "group": "{{form.group}}",
                             "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": False,
        },
        {
            "key": "mfa_reset",
            "name": "MFA sıfırlama",
            "description": "Telefonunuzu değiştirdiyseniz kayıtlı MFA yöntemleriniz silinir; bir "
            "sonraki girişte yeniden kaydedersiniz (BT onayı gerekir).",
            "category": "Kimlik ve erişim",
            "form_schema": {"type": "object", "properties": {"justification": justification}},
            "fulfilment_plan": [
                {
                    "tool": "identity.verify_requester",
                    "title": "Talep edenin kimliğini doğrula",
                    "args": {"request_id": "{{request.id}}", "user": "{{requester}}"},
                },
                {
                    "tool": "identity.reset_mfa",
                    "title": "MFA yöntemlerini sıfırla",
                    "args": {"user": "{{requester}}", "request_id": "{{request.id}}"},
                },
                _NOTIFY_DONE,
            ],
            "standard": False,
        },
    ]


async def seed_default_items(session: AsyncSession, actor: str = "system:catalog") -> list[str]:
    """Varsayılan öğeleri yoksa ekler (anahtar üzerinden; var olan öğeye dokunmaz)."""
    created: list[str] = []
    for data in default_items():
        if await get_item(session, data["key"]) is not None:
            continue
        await create_item(session, CatalogItemInput(**data), actor)
        created.append(data["key"])
    return created

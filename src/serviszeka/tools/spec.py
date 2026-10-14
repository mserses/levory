"""Araç tanımı (ToolSpec), yürütme bağlamı ve araç hataları.

Araç adı `alan.fiil_nesne` biçimindedir (ör. `incident.set_priority`, `identity.add_to_group`).
Alan (`domain`) politika ajanı, fiil (`verb`) politika eylemidir: `policy.POLICIES[(alan, fiil)]`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.domain.enums import RiskClass, RiskLevel

if TYPE_CHECKING:
    from serviszeka.llm import LLMClient
    from serviszeka.models import ActionRequest

TOOL_NAME_RE = re.compile(r"^[a-z][a-z_]*\.[a-z]+(?:_[a-z]+)*$")

# MCP/çağıran zarfı: araç argümanı olarak kullanılamaz (boru hattı ayırır)
RESERVED_ARGS: frozenset[str] = frozenset({"dry_run", "idempotency_key", "reason", "confidence"})

# Bağlanabilir kayıt türleri (ActionRequest sütunları)
LINK_KINDS: frozenset[str] = frozenset({"incident", "change", "request", "event_group"})

_RISK_ORDER = {RiskClass.R0: 0, RiskClass.R1: 1, RiskClass.R2: 2, RiskClass.R3: 3}

# Politika değerlendirmesinde risk sınıfı -> risk seviyesi
POLICY_RISK: Mapping[RiskClass, RiskLevel] = {
    RiskClass.R1: RiskLevel.LOW,
    RiskClass.R2: RiskLevel.HIGH,
    RiskClass.R3: RiskLevel.CRITICAL,
}

RISK_LABELS: Mapping[RiskClass, str] = {
    RiskClass.R0: "R0 · okuma, yapay zekâ serbest",
    RiskClass.R1: "R1 · düşük riskli yazma, politika izin verirse otomatik",
    RiskClass.R2: "R2 · her zaman insan onayı gerekir",
    RiskClass.R3: "R3 · kritik, iki onay ya da CAB onaylı değişiklik gerekir",
}


def risk_rank(risk: RiskClass) -> int:
    return _RISK_ORDER[RiskClass(risk)]


def max_risk(*risks: RiskClass) -> RiskClass:
    return max((RiskClass(r) for r in risks), key=risk_rank)


@dataclass
class ToolContext:
    """İşleyiciye verilen yürütme bağlamı.

    `actor` işi yapan kimliktir: doğrudan çağrıda çağıran, eylem isteğinde son onaylayan insan.
    İşleyiciler alan servislerini bu aktörle çağırır; servislerin kendi kuralları (ör. P1/P2 onayı,
    CAB, `ai:` aktör yasağı) aynen geçerlidir. İşleyici flush edebilir, asla commit etmez.
    """

    session: AsyncSession
    actor: CurrentUser
    settings: Settings
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    llm: LLMClient | None = None
    action_request: ActionRequest | None = None

    def get_llm(self) -> LLMClient:
        """LLM istemcisi (verilmediyse ayarlardan; yalnızca `serviszeka.llm` üzerinden)."""
        if self.llm is None:
            from serviszeka import llm as llm_module

            self.llm = llm_module.get_llm(self.settings)
        return self.llm


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]
RiskFn = Callable[[ToolContext, Any], "RiskClass | Awaitable[RiskClass]"]


@dataclass(frozen=True)
class ToolSpec:
    """Bildirimsel araç tanımı.

    - `risk_class`: temel (en düşük) risk sınıfı. `risk_fn` verilirse argümanlara/kayda göre daha
      yüksek bir sınıf döndürebilir (ör. ayrıcalıklı gruba ekleme R3); sonuç hiçbir zaman
      `risk_class`'ın altına inmez. R0 araçta `risk_fn` olmaz.
    - `input_model`: argüman şeması (pydantic; `extra="forbid"` önerilir). MCP girdi şeması buradan
      üretilir. `RESERVED_ARGS` alan adı olarak kullanılamaz.
    - `roles`: insan çağıranlar için gereken rollerden biri (admin hepsini kapsar). Yapay zekâ
      çağıranlar `ai_agent` rolüyle, `ai_allowed` ise ve rollerde admin dışı bir rol varsa çağırır
      (admin-yalnız araçlar yapay zekâya kapalıdır: `executor.ai_callable`).
    - `connectors`: gereken bağlayıcı türleri (`tools.connectors`); yapılandırılmamışsa araç
      kullanılamaz (503).
    - `links`: argüman adı -> bağ türü (`incident`/`change`/`request`/`event_group`); eylem
      isteğinin indeksli sütununa işlenir (bekleyen istek aramaları JSON taramadan SQL'de).
    """

    name: str
    description: str
    risk_class: RiskClass
    input_model: type[BaseModel]
    handler: Handler
    roles: frozenset[str]
    risk_fn: RiskFn | None = None
    connectors: tuple[str, ...] = ()
    ai_allowed: bool = True
    links: Mapping[str, str] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def verb(self) -> str:
        return self.name.split(".", 1)[1]

    def input_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.setdefault("type", "object")
        return schema

    def validate_definition(self) -> None:
        if not TOOL_NAME_RE.match(self.name):
            raise ValueError(f"Araç adı 'alan.fiil_nesne' biçiminde olmalı: {self.name!r}")
        if not self.description.strip():
            raise ValueError(f"{self.name}: açıklama zorunlu")
        if not self.roles:
            raise ValueError(f"{self.name}: en az bir insan rolü gerekli")
        if self.risk_class == RiskClass.R0 and self.risk_fn is not None:
            raise ValueError(f"{self.name}: R0 araçta koşullu risk olamaz")
        reserved = set(self.input_model.model_fields) & RESERVED_ARGS
        if reserved:
            raise ValueError(
                f"{self.name}: ayrılmış argüman adları kullanılamaz: {sorted(reserved)}"
            )
        bad = {k: v for k, v in self.links.items() if v not in LINK_KINDS}
        if bad or not set(self.links) <= set(self.input_model.model_fields):
            raise ValueError(f"{self.name}: geçersiz bağ tanımı {dict(self.links)}")


# --- hatalar --------------------------------------------------------------------------------


class ToolError(Exception):
    """Araç boru hattı hatası; `code` makine tarafından okunur, mesaj Türkçedir."""

    code = "tool_error"
    status_code = 400

    def __init__(self, message: str, *, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.errors:
            data["errors"] = self.errors
        return data


class ToolNotFoundError(ToolError):
    code = "not_found"
    status_code = 404


class ToolForbiddenError(ToolError):
    code = "forbidden"
    status_code = 403


class ToolValidationError(ToolError):
    code = "validation_error"
    status_code = 422


class ToolConflictError(ToolError):
    code = "conflict"
    status_code = 409


class IdempotencyConflictError(ToolConflictError):
    code = "idempotency_conflict"


class ToolUnavailableError(ToolError):
    code = "unavailable"
    status_code = 503


class ToolExecutionError(ToolError):
    """Alan servisi işlemi reddetti (geçersiz geçiş, kapalı kayıt vb.)."""

    code = "execution_error"
    status_code = 422

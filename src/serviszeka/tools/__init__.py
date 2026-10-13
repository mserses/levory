"""Faz 3 araç katmanı: bildirimsel araç kaydı + tek yürütme boru hattı + eylem istekleri.

Sözleşme ve kurallar: docs/faz3.md. Kısaca:

- `ToolSpec` ile araç tanımlanır, `registry.register(spec)` ile kayda alınır.
- Her çağrı `execute_tool(session, actor, ad, argümanlar, dry_run=, idempotency_key=)` üzerinden
  geçer: R0 çalışır; R1 insan çalışır, R1 yapay zekâ politika AUTO_APPLIED derse çalışır;
  R2/R3 asla çalışmaz, `ActionRequest` açılır ve insan onayıyla (`tools.actions`) yürütülür.
"""

from serviszeka.tools import actions
from serviszeka.tools.builtin import register_builtin_tools
from serviszeka.tools.connectors import connectors
from serviszeka.tools.executor import (
    ActionLinks,
    ToolResult,
    ToolStatus,
    as_tool_actor,
    can_call,
    execute_tool,
)
from serviszeka.tools.registry import ToolRegistry, registry
from serviszeka.tools.spec import (
    RESERVED_ARGS,
    RISK_LABELS,
    IdempotencyConflictError,
    ToolConflictError,
    ToolContext,
    ToolError,
    ToolExecutionError,
    ToolForbiddenError,
    ToolNotFoundError,
    ToolSpec,
    ToolUnavailableError,
    ToolValidationError,
)

# Yerleşik katalog araçları paket içe aktarılınca kayda alınır (bir kez)
register_builtin_tools()

__all__ = [
    "RESERVED_ARGS",
    "RISK_LABELS",
    "ActionLinks",
    "IdempotencyConflictError",
    "ToolConflictError",
    "ToolContext",
    "ToolError",
    "ToolExecutionError",
    "ToolForbiddenError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolStatus",
    "ToolUnavailableError",
    "ToolValidationError",
    "actions",
    "as_tool_actor",
    "can_call",
    "connectors",
    "execute_tool",
    "register_builtin_tools",
    "registry",
]

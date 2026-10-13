"""Araç argümanı/sonucu maskeleme ve kanonik özet.

- `mask_value`: iç içe yapıdaki her metne `privacy.mask_pii` uygular (yapay zekâya dönen sonuçlar,
  eylem isteği gösterim kopyası).
- `redact_args`: gizli bilgi gibi görünen anahtarları (`password`, `secret`, `token`...) tamamen
  gizler, kalan metinleri maskeler. Gizli bilgi argüman olmamalıdır; bu yalnızca bir emniyettir.
- `args_hash`: araç adı + kanonik argümanların SHA-256 özeti (idempotency, eylem isteği).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from serviszeka.privacy import mask_pii

_SECRET_KEY_RE = re.compile(
    r"pass(word)?|parola|sifre|şifre|secret|token|api[_-]?key|credential", re.IGNORECASE
)
REDACTED = "***"


def _is_identifier(key: Any) -> bool:
    # Kimlik/numara/özet alanları PII değildir; rakam desenleri yanlışlıkla maskelenmesin
    return isinstance(key, str) and (
        key in ("id", "ids", "number", "hash", "prev_hash")
        or key.endswith(("_id", "_ids", "_number"))
    )


def mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: (v if _is_identifier(k) else mask_value(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [mask_value(v) for v in value]
    return value


def redact_args(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (
                REDACTED
                if isinstance(k, str) and _SECRET_KEY_RE.search(k)
                else v
                if _is_identifier(k)
                else redact_args(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_args(v) for v in value]
    return mask_value(value)


def jsonable(value: Any) -> Any:
    """JSON'a gidiş-dönüş: saklanan ve özetlenen değer aynı olur."""
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def args_hash(tool: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"tool": tool, "args": jsonable(args)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

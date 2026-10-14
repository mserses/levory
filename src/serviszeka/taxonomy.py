"""Sınıflandırma sınıf listesi: kategori, hizmet ve çözüm grubu değerleri.

Kaynak `taxonomy.json`; Hizmet Masası ajanı ve değerlendirme seti aynı listeyi kullanır.
"""

import json
from functools import cache
from importlib import resources
from typing import Any

# Değeri sınıf listesinden gelmek zorunda olan serbest metin alanları
TAXONOMY_FIELDS: tuple[str, ...] = ("category", "service", "assignment_group")


@cache
def load() -> dict[str, Any]:
    return json.loads(resources.files("serviszeka").joinpath("taxonomy.json").read_text("utf-8"))


def allowed(field: str) -> frozenset[str]:
    values = load()[field]
    return frozenset(values if isinstance(values, list) else values.keys())


def prompt_section() -> str:
    """LLM'e verilecek izinli değerler listesi (anahtar: açıklama)."""
    tax = load()
    lines: list[str] = []
    for field in (*TAXONOMY_FIELDS, "impact", "urgency"):
        lines.append(f"{field}:")
        lines.extend(f"  - {key}: {desc}" for key, desc in tax[field].items())
    return "\n".join(lines)

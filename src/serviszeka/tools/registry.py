"""Araç kayıt defteri: tek doğruluk kaynağı (MCP listesi, /actions yürütmesi, kapı testleri)."""

from __future__ import annotations

from serviszeka.tools.spec import ToolNotFoundError, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        spec.validate_definition()
        if spec.name in self._tools:
            raise ValueError(f"Araç zaten kayıtlı: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def unregister(self, name: str) -> None:
        """Yalnızca testler için: geçici araç kaydını kaldırır."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolNotFoundError(f"Araç bulunamadı: {name}")
        return spec

    def find(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[ToolSpec]:
        return [self._tools[n] for n in self.names()]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


registry = ToolRegistry()

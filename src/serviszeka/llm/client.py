"""LLM istemcileri: Anthropic SDK sarmalayıcısı ve testler için deterministik sahte istemci.

Gizlilik kuralı: her istem (system + user) istemcinin içinde mask_pii'den geçirilir;
çağıranlara güvenilmez.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from serviszeka.config import Settings, get_settings
from serviszeka.privacy import mask_pii

DEFAULT_MAX_TOKENS = 2048

_JSON_INSTRUCTION = (
    "Yanıtını YALNIZCA tek bir geçerli JSON nesnesi olarak ver. "
    "Açıklama, markdown veya kod bloğu ekleme."
)


class LLMError(RuntimeError):
    """LLM çağrısı başarısız oldu ya da geçerli JSON dönmedi."""


@runtime_checkable
class LLMClient(Protocol):
    async def complete_json(
        self, *, system: str, user: str, model: str | None = None
    ) -> dict: ...


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Metindeki ilk JSON nesnesini sağlam biçimde çıkarır (kod bloklarını da tolere eder)."""
    if not text or not text.strip():
        raise LLMError("LLM boş yanıt döndü")
    candidates: list[str] = [m.group(1) for m in _FENCE_RE.finditer(text)]
    candidates.append(text)
    decoder = json.JSONDecoder()
    for cand in candidates:
        idx = cand.find("{")
        while idx != -1:
            try:
                obj, _ = decoder.raw_decode(cand, idx)
            except json.JSONDecodeError:
                idx = cand.find("{", idx + 1)
                continue
            if isinstance(obj, dict):
                return obj
            idx = cand.find("{", idx + 1)
    raise LLMError(f"LLM yanıtında geçerli JSON nesnesi bulunamadı: {text[:200]!r}")


ResponseFn = Callable[[str, str], "dict | Awaitable[dict]"]


class FakeLLM:
    """Testler için deterministik LLM.

    responses: sırayla döndürülecek sözlükler (liste bitince son yanıt tekrarlanır)
    ya da `fn(system, user) -> dict` (senkron veya async) çağrılabilir.
    Maskelenmiş istemler .calls listesine kaydedilir.
    """

    def __init__(self, responses: list[dict] | ResponseFn | None = None) -> None:
        self._responses = responses if responses is not None else [{}]
        if isinstance(self._responses, list) and not self._responses:
            raise ValueError("FakeLLM için en az bir yanıt gerekli")
        self._index = 0
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self, *, system: str, user: str, model: str | None = None
    ) -> dict:
        system_m, user_m = mask_pii(system), mask_pii(user)
        self.calls.append({"system": system_m, "user": user_m, "model": model})
        if callable(self._responses):
            result = self._responses(system_m, user_m)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise LLMError("FakeLLM fonksiyonu dict döndürmedi")
            return result
        responses: list[dict] = self._responses
        resp = responses[min(self._index, len(responses) - 1)]
        self._index += 1
        return copy.deepcopy(resp)


class AnthropicLLM:
    """Resmî `anthropic` AsyncAnthropic SDK'sı üzerinden JSON yanıt alan istemci."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.settings = settings or get_settings()
        self.max_tokens = max_tokens
        if client is None:
            if not self.settings.anthropic_api_key:
                raise LLMError("SZ_ANTHROPIC_API_KEY tanımlı değil; AnthropicLLM oluşturulamadı")
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        self._client = client

    async def complete_json(
        self, *, system: str, user: str, model: str | None = None
    ) -> dict:
        system_m = mask_pii(system)
        user_m = mask_pii(user)
        full_system = f"{system_m}\n\n{_JSON_INSTRUCTION}" if system_m else _JSON_INSTRUCTION
        try:
            message = await self._client.messages.create(
                model=model or self.settings.llm_fast_model,
                max_tokens=self.max_tokens,
                system=full_system,
                messages=[{"role": "user", "content": user_m}],
            )
        except LLMError:
            raise
        except Exception as exc:  # SDK hataları (APIError vb.) tek tipe indirgenir
            raise LLMError(f"Anthropic çağrısı başarısız: {exc}") from exc
        text = "".join(
            getattr(block, "text", "")
            for block in (getattr(message, "content", None) or [])
            if getattr(block, "type", "text") == "text"
        )
        return extract_json(text)


def get_llm(settings: Settings | None = None) -> LLMClient:
    """Üretim istemcisini döndürür; API anahtarı yoksa açık bir hata verir."""
    settings = settings or get_settings()
    if settings.llm_provider == "fake":
        from serviszeka.llm.fake import DeterministicFakeLLM, check_provider

        check_provider(settings)
        return DeterministicFakeLLM()
    if not settings.anthropic_api_key:
        raise LLMError(
            "LLM yapılandırılmamış: SZ_ANTHROPIC_API_KEY ortam değişkenini ayarlayın"
        )
    return AnthropicLLM(settings)

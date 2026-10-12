# ServisZeka — katkıda bulunanlar için notlar

AI-native ITIL 4 hizmet yönetimi platformu. Python 3.11+, FastAPI, SQLAlchemy 2 (async), pydantic v2. Kaynak kod `src/serviszeka/` altında (src layout).

## Komutlar

```bash
uv sync                  # bağımlılıklar
uv run pytest            # testler (asyncio_mode=auto, bellek içi SQLite)
uv run ruff check .      # lint
uv run uvicorn serviszeka.main:app --reload   # yerel sunucu
```

## Mimari kurallar

- **Her yapay zekâ eylemi** önce `serviszeka.policy.evaluate(...)` ile değerlendirilmeli, sonucu bir `AIDecision` kaydı olarak saklanmalı ve `serviszeka.audit.record(...)` ile denetim zincirine yazılmalıdır. Politikayı atlayan ajan kodu kabul edilmez.
- Gölge mod (`SZ_AI_SHADOW_MODE=true`) açıkken ajanlar yalnızca önerir, uygulamaz.
- **LLM çağrıları yalnızca `serviszeka.llm`** üzerinden yapılır (PII'yi `privacy.mask_pii` ile maskeler). `anthropic` SDK'sını doğrudan çağırmayın.
- `audit.record` commit etmez; commit işlemi çağıran servis/endpoint'in sorumluluğundadır.
- **Gizli bilgileri asla koda yazmayın**; tüm ayarlar `SZ_` önekli ortam değişkenlerinden `config.get_settings()` ile okunur (bkz. `.env.example`).
- Testlerde gerçek dış servis çağrılmaz: `FakeLLM` ve `httpx.MockTransport` kullanın.
- Kod/tanımlayıcılar İngilizce; kullanıcıya dönük mesajlar ve kısa yorumlar Türkçe olabilir.

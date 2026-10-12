# ServisZeka — katkıda bulunanlar için notlar

AI-native ITIL 4 hizmet yönetimi platformu. Python 3.11+, FastAPI, SQLAlchemy 2 (async), pydantic v2. Kaynak kod `src/serviszeka/` altında (src layout).

## Komutlar

```bash
uv sync                  # bağımlılıklar
uv run pytest            # testler (asyncio_mode=auto, bellek içi SQLite)
uv run ruff check .      # lint
uv run uvicorn serviszeka.main:app --reload   # yerel sunucu
uv run alembic upgrade head                   # göçleri uygula (üretimde şema böyle kurulur)
uv run alembic check                          # modeller ile göçler arasında fark yok mu
uv run alembic revision --autogenerate -m "..."   # model değişince yeni göç
```

## Mimari kurallar

- **Her yapay zekâ eylemi** önce `serviszeka.policy.evaluate(...)` ile değerlendirilmeli, sonucu bir `AIDecision` kaydı olarak saklanmalı ve `serviszeka.audit.record(...)` ile denetim zincirine yazılmalıdır. Politikayı atlayan ajan kodu kabul edilmez.
- Gölge mod (`SZ_AI_SHADOW_MODE=true`) açıkken ajanlar yalnızca önerir, uygulamaz.
- **LLM çağrıları yalnızca `serviszeka.llm`** üzerinden yapılır (PII'yi `privacy.mask_pii` ile maskeler). `anthropic` SDK'sını doğrudan çağırmayın.
- `audit.record` commit etmez; commit işlemi çağıran servis/endpoint'in sorumluluğundadır.
- **Gizli bilgileri asla koda yazmayın**; tüm ayarlar `SZ_` önekli ortam değişkenlerinden `config.get_settings()` ile okunur (bkz. `.env.example`).
- Testlerde gerçek dış servis çağrılmaz: `FakeLLM` ve `httpx.MockTransport` kullanın.
- **Şema değişikliği = göç**: `models.py` değişince `migrations/versions/` altına Alembic göçü ekleyin; `alembic check` temiz kalmalı. `create_all` yalnızca geliştirme/test içindir (`SZ_AUTO_CREATE_SCHEMA`, varsayılan true); üretimde `SZ_AUTO_CREATE_SCHEMA=false` ve `alembic upgrade head`.
- Kod/tanımlayıcılar İngilizce; kullanıcıya dönük mesajlar ve kısa yorumlar Türkçe olabilir.

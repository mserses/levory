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
uv run python evals/run_eval.py --oracle          # değerlendirme boru hattı duman testi
uv run python evals/run_eval.py                   # gerçek LLM ile %70 kapısı (SZ_ANTHROPIC_API_KEY gerekir)
```

## Mimari kurallar

- **Her yapay zekâ eylemi** önce `serviszeka.policy.evaluate(...)` ile değerlendirilmeli, sonucu bir `AIDecision` kaydı olarak saklanmalı ve `serviszeka.audit.record(...)` ile denetim zincirine yazılmalıdır. Politikayı atlayan ajan kodu kabul edilmez.
- Gölge mod (`SZ_AI_SHADOW_MODE=true`) açıkken ajanlar yalnızca önerir, uygulamaz.
- **LLM çağrıları yalnızca `serviszeka.llm`** üzerinden yapılır (PII'yi `privacy.mask_pii` ile maskeler). `anthropic` SDK'sını doğrudan çağırmayın.
- `audit.record` commit etmez; commit işlemi çağıran servis/endpoint'in sorumluluğundadır.
- **Gizli bilgileri asla koda yazmayın**; tüm ayarlar `SZ_` önekli ortam değişkenlerinden `config.get_settings()` ile okunur (bkz. `.env.example`).
- Testlerde gerçek dış servis çağrılmaz: `FakeLLM` ve `httpx.MockTransport` kullanın.
- **Şema değişikliği = göç**: `models.py` değişince `migrations/versions/` altına Alembic göçü ekleyin; `alembic check` temiz kalmalı. `create_all` yalnızca geliştirme/test içindir (`SZ_AUTO_CREATE_SCHEMA`, varsayılan true); üretimde `SZ_AUTO_CREATE_SCHEMA=false` ve `alembic upgrade head`.
- Sınıflandırma değerleri (`category`, `service`, `assignment_group`) yalnızca `src/serviszeka/taxonomy.json` listesinden gelir; liste değişirse `evals/data/dataset.jsonl` etiketleri de güncellenmelidir.
- **Kimlik doğrulama**: `GET /health` ve belgeler dışındaki her uç nokta `serviszeka.auth` ile korunur. Yeni yönlendirici `APIRouter(..., dependencies=[Depends(get_current_user)])` ile kurulmalı, her uç nokta rolünü `AgentUser` / `ReaderUser` / `ApproverUser` / `AdminUser` (veya `require_roles(...)`) ile istemelidir. Aktör/onaylayan yalnızca `user.username`'den gelir; `X-Actor` gibi istemci başlıklarına güvenmeyin. `tests/test_auth.py::test_every_api_route_requires_auth` korumasız uç noktayı yakalar.
- Varsayılanlar güvenlidir: `SZ_ENVIRONMENT=production`, `SZ_AUTH_MODE=oidc` (`SZ_AUTH_ISSUER` + `SZ_AUTH_AUDIENCE` zorunlu). `SZ_AUTH_MODE=dev` (`Bearer dev:<kullanıcı>:<roller>`, imza doğrulamasız) yalnızca `SZ_ENVIRONMENT=development` iken çalışır; aksi halde uygulama açılmaz.
- Testler `tests/conftest.py` içindeki autouse fikstürle dev kipinde çalışır; API testleri `tests/_auth.py` (`AGENT`, `APPROVER`, `ADMIN`, `dev_headers`) başlıklarını kullanır. OIDC yolu `tests/test_auth.py`'de testte üretilen RSA/EC anahtarları ve `httpx.MockTransport` JWKS ile sınanır.
- Kod/tanımlayıcılar İngilizce; kullanıcıya dönük mesajlar ve kısa yorumlar Türkçe olabilir.

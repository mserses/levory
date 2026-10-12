# ServisZeka

**ServisZeka**, ITIL 4 uygulamalarını temel alan, yapay zekâ yerlisi (AI-native) bir BT hizmet yönetimi platformudur. Olay, bilgi, değişiklik ve talep süreçlerinde yapay zekâ ajanları işi hızlandırır; ancak son söz her zaman insandadır.

## Temel ilkeler

- **Ajanlar önerir, insanlar onaylar.** Her ajan eylemi bir `AIDecision` kaydı olarak önerilir; güven eşiğinin altındaki veya riskli eylemler insan onayına düşer. Varsayılan olarak **gölge mod** açıktır: ajanlar öneri üretir ama hiçbir şeyi uygulamaz.
- **Politika motoru.** Tüm yapay zekâ eylemleri `policy.evaluate()` üzerinden geçer; sonuç ajan, eylem, güven skoru ve risk seviyesine göre *otomatik uygula / onaya gönder / reddet* olur.
- **Denetim zinciri.** Her kritik işlem `audit.record()` ile hash zincirine bağlı bir `AuditLog` kaydı oluşturur; `audit.verify_chain()` zincirin bozulmadığını doğrular.
- **KVKK uyumlu maskeleme.** LLM'e giden her metin `privacy.mask_pii()` ile maskelenir (TCKN, e-posta, telefon, IBAN vb.). LLM çağrıları yalnızca `serviszeka.llm` üzerinden yapılır.
- **ITSM köprüsü.** ServiceNow ve Jira Service Management ile iki yönlü senkronizasyon (`ExternalLink`, `SyncEvent`); ServisZeka mevcut araçların yerine geçmeden yanlarında çalışabilir.

## Mimari katmanlar

```
┌───────────────────────────────────────────────┐
│ API (FastAPI)        api/*                     │  HTTP uçları, şemalar
├───────────────────────────────────────────────┤
│ Ajanlar              agents/*                  │  sınıflandırma, öneri üretimi
├───────────────────────────────────────────────┤
│ Yönetişim            policy · audit · privacy  │  onay kuralları, denetim, maskeleme
├───────────────────────────────────────────────┤
│ Servisler / Alan     services/* · domain/*     │  ITIL iş kuralları (öncelik matrisi vb.)
│ Bilgi (RAG)          knowledge/*               │  makale arama ve oluşturma
├───────────────────────────────────────────────┤
│ Altyapı              db · models · llm ·       │  SQLAlchemy 2 async, Anthropic,
│                      integrations/*            │  ServiceNow / Jira SM istemcileri
└───────────────────────────────────────────────┘
```

## Modül haritası

| Modül | Sorumluluk |
|---|---|
| `src/serviszeka/auth.py` | OIDC bearer JWT doğrulama (JWKS), `CurrentUser`, `require_roles()` |
| `src/serviszeka/config.py` | `SZ_` önekli ortam değişkenlerinden ayarlar (`get_settings()`) |
| `src/serviszeka/db.py` | Async motor, oturum fabrikası, `get_session()` bağımlılığı |
| `src/serviszeka/models.py` | `Incident`, `AIDecision`, `AuditLog`, `KnowledgeArticle`, `ExternalLink`, `SyncEvent` |
| `src/serviszeka/domain/` | Enum'lar ve ITIL etki × aciliyet öncelik matrisi (`calculate_priority`) |
| `src/serviszeka/services/` | Olay yaşam döngüsü (oluştur, güncelle, iyimser kilitleme) |
| `src/serviszeka/agents/` | Hizmet masası / olay ajanları (öneri üretir, uygulamaz) |
| `src/serviszeka/policy.py` | Politika motoru: `evaluate()` → `PolicyDecision` |
| `src/serviszeka/audit.py` | Hash zincirli denetim kaydı: `record()`, `verify_chain()` |
| `src/serviszeka/privacy.py` | KVKK PII maskeleme: `mask_pii()` |
| `src/serviszeka/llm/` | `LLMClient` protokolü, `AnthropicLLM`, testler için `FakeLLM` |
| `src/serviszeka/knowledge/` | Bilgi makaleleri ve arama (`search`, `create_article`) |
| `src/serviszeka/integrations/` | ServiceNow ve Jira SM iki yönlü senkronizasyon |
| `src/serviszeka/api/` | REST uçları: `incidents`, `knowledge`, `integrations`, `audit` |
| `src/serviszeka/main.py` | FastAPI uygulaması, `/health` |
| `migrations/` | Alembic veritabanı göçleri (`alembic.ini`, async `env.py`) |

## Yerelde çalıştırma (uv)

Gereksinim: Python 3.11+ ve [uv](https://docs.astral.sh/uv/).

```bash
cd serviszeka
uv sync                       # bağımlılıkları kur
cp .env.example .env          # ayarları düzenleyin (varsayılan: SQLite)
uv run uvicorn serviszeka.main:app --reload
```

Geliştirmede `SZ_AUTO_CREATE_SCHEMA=true` (varsayılan) ile tablolar açılışta `create_all` ile kurulur.

Varsayılan ayarlar güvenlidir (`SZ_ENVIRONMENT=production`, `SZ_AUTH_MODE=oidc`): kimlik sağlayıcı (`SZ_AUTH_ISSUER`, `SZ_AUTH_AUDIENCE`) tanımlanmadan uygulama açılmaz. Yerel denemede geliştirme kipini `.env` içinde açıkça seçin (bkz. [Kimlik doğrulama](#kimlik-doğrulama-ve-yetkilendirme)):

```bash
SZ_ENVIRONMENT=development
SZ_AUTH_MODE=dev
curl -H "Authorization: Bearer dev:ayse:agent" http://localhost:8000/incidents
```

Uygulama `http://localhost:8000` adresinde açılır; API belgeleri `http://localhost:8000/docs`, sağlık kontrolü `http://localhost:8000/health`.

## Kimlik doğrulama ve yetkilendirme

`GET /health` ve API belgeleri (`/docs`, `/openapi.json`) dışındaki **tüm** uçlar `Authorization: Bearer <JWT>` ister. Belirteç bir OIDC sağlayıcısından (Keycloak, Microsoft Entra ID) gelir ve şöyle doğrulanır:

- İmza, yayıncının JWKS anahtarlarıyla doğrulanır. JWKS adresi `SZ_AUTH_JWKS_URL`'den ya da `<issuer>/.well-known/openid-configuration` keşfinden alınır. Anahtarlar önbelleğe alınır; bilinmeyen `kid` gelince (en fazla 30 sn'de bir) yenilenir. İstek zaman aşımı kısadır (`SZ_AUTH_HTTP_TIMEOUT`).
- Yalnızca `RS256` / `ES256` kabul edilir; `none` ve `HS*` her zaman reddedilir.
- `iss`, `aud`, `exp` zorunludur; `exp`/`nbf` için küçük bir saat kayması toleransı vardır (`SZ_AUTH_LEEWAY_SECONDS`).
- Kullanıcı adı `SZ_AUTH_USERNAME_CLAIM` (varsayılan `preferred_username`), yoksa `upn` → `email` → `sub` talebinden okunur. Denetim kayıtlarındaki aktör ve onaylayan her zaman bu addır; `X-Actor` başlığı artık yok sayılır.
- Roller varsayılan olarak Keycloak `realm_access.roles` (+ `resource_access.<aud>.roles`) ve Entra `roles` taleplerinden okunur; `SZ_AUTH_ROLES_CLAIM` ile başka bir yol seçilebilir.

Eksik/geçersiz belirteç → `401` (`WWW-Authenticate: Bearer`), yetersiz rol → `403`.

| Rol | Yetki |
|---|---|
| `agent` | Olay ve bilgi makalesi okuma/oluşturma/güncelleme, triage başlatma |
| `approver` | Yapay zekâ kararlarını onaylama, senkron çakışmalarını listeleme/çözme; olay ve makaleleri okuma |
| `admin` | Her şey: entegrasyon durumu, pull/push, denetim günlüğü (`/audit`) dahil; diğer tüm rolleri kapsar |

Karar onayında (`POST /incidents/decisions/{id}/approve`) onaylayan belirteçteki kullanıcıdır; gövdede farklı bir `approver` gönderilirse `403` döner.

**Geliştirme kipi.** `SZ_AUTH_MODE=dev` iken `Authorization: Bearer dev:<kullanıcı>:<rol1,rol2>` kabul edilir ve **hiçbir imza doğrulanmaz**. Bu kip yalnızca `SZ_ENVIRONMENT=development` ile çalışır; başka bir ortamda uygulama açılışta hata verip durur. Üretimde asla kullanmayın.

## Docker Compose ile çalıştırma

```bash
cp .env.example .env
docker compose up --build
```

`.env` içinde OIDC ayarlarını (`SZ_AUTH_ISSUER`, `SZ_AUTH_AUDIENCE`) doldurun; aksi halde uygulama açılmaz. Bu komut uygulamayı ve PostgreSQL 16'yı (pgvector imajı) ayağa kaldırır. Compose, `SZ_DATABASE_URL` değerini otomatik olarak `postgresql+asyncpg://...@db:5432/...` olarak ayarlar; veriler `pgdata` biriminde kalıcıdır. Konteyner açılışta önce `alembic upgrade head`, sonra uvicorn çalıştırır (`SZ_AUTO_CREATE_SCHEMA=false`).

## Veritabanı göçleri (Alembic)

Üretimde şema yalnızca göçlerle kurulur/güncellenir; `SZ_AUTO_CREATE_SCHEMA=false` yapın. Bağlantı adresi `SZ_DATABASE_URL`'den okunur.

```bash
uv run alembic upgrade head                            # şemayı son sürüme yükselt
uv run alembic check                                   # modeller ile göçler arasında fark var mı
uv run alembic revision --autogenerate -m "aciklama"   # model değişikliğinden sonra yeni göç
uv run alembic downgrade -1                            # bir göç geri al
```

Göçlerden önce `create_all` ile kurulmuş mevcut veritabanları da doğrudan `alembic upgrade head` ile yükseltilir: `0001` tabloları yalnızca yoksa kurar, `0002` eksik SLA duraklatma sütunlarını ekler (`stamp` gerekmez).

Model (`models.py`) değiştiren her değişiklik bir göç dosyası içermelidir; CI `alembic upgrade head` ve `alembic check` çalıştırır.

## Testler ve kod kalitesi

```bash
uv run pytest            # tüm testler (bellek içi SQLite, harici servis çağrısı yok)
uv run ruff check .      # lint
```

Testlerde gerçek LLM veya ITSM servisleri çağrılmaz: LLM için `FakeLLM`, HTTP için `httpx.MockTransport` kullanılır. CI (GitHub Actions) her push/PR'da ruff, pytest ve SQLite üzerinde `alembic upgrade head` + `alembic check` çalıştırır.

## Sınıflandırma değerlendirmesi (Faz 1 kapısı)

Hizmet Masası ajanının doğruluğu, `evals/data/dataset.jsonl` içindeki 200 etiketli Türkçe kayıtla ölçülür. Kapı: kayıtların en az %70'i doğru yönlendirilmeli (kind + category + assignment_group). Değerlendirme gerçek triage akışını gölge modda çalıştırır; maskeleme, sınıf listesi doğrulaması ve politika dahildir, hiçbir kayıt değişmez.

```bash
uv run python evals/run_eval.py --oracle                  # boru hattı duman testi (LLM'siz)
SZ_ANTHROPIC_API_KEY=... uv run python evals/run_eval.py  # gerçek ölçüm; kapı geçilmezse çıkış kodu 1
```

Sonuçlar `evals/results/` altına JSON ve Markdown olarak yazılır. Veri seti sentetiktir; yöntemi ve sınırları `evals/data/DATASET_CARD.md` içinde anlatılır. İzinli değerler `src/serviszeka/taxonomy.json`, etiketleme kuralları `evals/LABELING.md` dosyasındadır.

## Yol haritası

| Faz | Süre | Kapsam | Geçiş kapısı |
|---|---|---|---|
| **Faz 0 — Temel** | 2 hafta | Proje iskeleti, veri modeli, politika motoru, denetim zinciri, KVKK maskeleme, CI | Testler yeşil; uygulama tek komutla ayağa kalkıyor |
| **Faz 1 — MVP** | 6 hafta | Hizmet Masası + Olay ajanı, RAG bilgi tabanı, SLA takibi, self-servis portal, ITSM köprüsü (ServiceNow / Jira SM) | Olayların **%70'i** doğru sınıflandırılıyor |
| **Faz 2 — Kontrol** | 6 hafta | Değişiklik yönetimi ve CAB, CMDB, gelişmiş politika kuralları | Riskli hiçbir eylem onaysız çalışmıyor |
| **Faz 3 — Otomasyon** | 8 hafta | Talep karşılama otomasyonu, problem ajanı, MCP entegrasyonu | Standart taleplerin **%50'si** insansız karşılanıyor |
| **Faz 4 — Olgunluk** | Sürekli | Sürekli iyileştirme döngüsü, tahmine dayalı SLA | — |

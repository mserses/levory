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
| `src/serviszeka/models.py` | `Incident`, `AIDecision`, `AuditLog`, `KnowledgeArticle`, `ExternalLink`, `SyncEvent`; Faz 2: `ConfigurationItem`, `CIRelationship`, `ChangeRequest`, `ChangeApproval`, `StandardChangeTemplate`, `ChangeFreezeWindow`, `incident_cis` (olay ↔ CI), `CMDBExternalRef` |
| `src/serviszeka/domain/` | Enum'lar ve ITIL etki × aciliyet öncelik matrisi (`calculate_priority`) |
| `src/serviszeka/services/` | Olay yaşam döngüsü (oluştur, güncelle, iyimser kilitleme); `cmdb` (CI, ilişkiler, etki analizi); `changes` (değişiklik durum makinesi, CAB/ECAB, takvim/çakışma, risk) — bkz. [`docs/faz2.md`](docs/faz2.md) |
| `src/serviszeka/agents/` | Hizmet masası / olay ajanı (`service_desk`, triage) ve değişiklik risk ajanı (`change_risk`); politika izin vermedikçe yalnızca önerir |
| `src/serviszeka/policy.py` | Politika motoru: `evaluate()` → `PolicyDecision` |
| `src/serviszeka/audit.py` | Hash zincirli denetim kaydı: `record()`, `verify_chain()` |
| `src/serviszeka/privacy.py` | KVKK PII maskeleme: `mask_pii()` |
| `src/serviszeka/llm/` | `LLMClient` protokolü, `AnthropicLLM`, testler için `FakeLLM`, geliştirme/E2E için `SZ_LLM_PROVIDER=fake` (triage sınıflandırması + değişiklik risk anlatısı) |
| `src/serviszeka/knowledge/` | Bilgi makaleleri ve arama (`search`, `create_article`) |
| `src/serviszeka/integrations/` | ServiceNow ve Jira SM iki yönlü senkronizasyon; ServiceNow CMDB çekimi (`servicenow_cmdb`, `POST /integrations/servicenow/cmdb/pull`); Jira Assets yalnızca arayüz taslağı |
| `src/serviszeka/api/` | REST uçları: `incidents`, `knowledge`, `integrations`, `audit`, `me`, `cmdb`, `changes`; Faz 3: `actions` (eylem isteği onay kutusu) |
| `src/serviszeka/tools/` | Faz 3 araç katmanı: `ToolSpec` kaydı (33 araç, R0-R3 risk sınıfı), tek yürütme boru hattı `execute_tool` (politika + `AIDecision` + denetim, idempotency, dry-run), eylem istekleri (`actions`: onay, red, süre aşımı, onay anında yürütme), bağlayıcı arayüzleri (Entra ID / Graph / Teams) — bkz. [`docs/faz3.md`](docs/faz3.md) |
| Sorun yönetimi | `models.Problem`, `services/problems.py`, `agents/problem.py` (eğilim tespiti, LLM 5 Neden taslağı, taslak RFC; yalnızca araç boru hattından), `tools/builtin/problem.py` (8 araç), `api/problems.py` (`/problems`) — bkz. [`docs/faz3.md`](docs/faz3.md#sorun-yönetimi-itil-4-problem-management) |
| Faz 4 KPI / tahmine dayalı SLA | `services/kpi.py` (12 KPI, pencere + önceki pencereye göre eğilim; MTTR düşüşü veriden), `services/sla_forecast.py` (Kaplan–Meier + grup yükü, açıklamalı ihlal olasılığı, Brier backtest), `api/kpi.py` (`/kpi`, `/sla/report`, `/sla/forecast`), R0 araçlar `kpi.report` / `sla.report` / `sla.forecast` — bkz. [`docs/faz4.md`](docs/faz4.md) |
| Faz 4 izleme ve olay yönetimi | `POST /events/alertmanager` / `/events/generic` (paylaşılan gizli ya da HMAC + yeniden oynatma koruması), `services/events.py` (CI eşleme, CMDB etki grafiğiyle bağıntı, tekilleştirme, çırpınma bastırma), `agents/events.py` (`ai:events`: grup başına tek olay, P1/P2 onaylı, asla otomatik çözüm), araçlar `event.*`, `GET /events` — bkz. [`docs/faz4.md`](docs/faz4.md) |
| Faz 4 sürekli iyileştirme (CSI) | `services/improvements.py` (CSI0000001 kaydı: idea → approved → in_progress → measured → closed / rejected; KPI tabanı + hedef, uygulama sonrası önce/sonra ölçümü ve hedefe ulaşıldı mı — MTTR düşüşünün kanıtı), `agents/improvement.py` (`ai:csi`: KPI eğilimi, SLA ihlali/tahmini, triage kabulü, yeniden açılma, işe yaramayan KB, düzeltmesiz bilinen hata, başarısız değişiklik PIR'i sinyalleri; LLM yalnızca gerekçe metni; asla onaylamaz), `/improvements` (onay/kapatma approver/admin), araçlar `csi.*`, zamanlayıcı işi `csi_review` — bkz. [`docs/faz4.md`](docs/faz4.md) |
| `src/serviszeka/mcp_server.py` | MCP sunucusu (resmi Python MCP SDK, streamable HTTP, `/mcp`): araç kaydını JSON şeması ve risk sınıfıyla sunar; bearer OIDC, yapay zekâ ajanları `ai_agent` rolüyle `ai:<istemci>` |
| `src/serviszeka/cors.py` | `SZ_CORS_ORIGINS` ayarından beslenen CORS ara katmanı |
| `src/serviszeka/openapi_export.py` | OpenAPI şemasını portal tip üretimi için dışa aktarır |
| `portal/` | Web portalı (Vite + React + TypeScript); bkz. `portal/README.md` |
| `src/serviszeka/main.py` | FastAPI uygulaması, `/health` |
| `migrations/` | Alembic veritabanı göçleri (`alembic.ini`, async `env.py`) |

## Demo (tek komut)

SENTETİK Türkçe demo verisiyle (26 CI, son 60 güne yayılmış 40 olay, sorun/bilinen hata, bilgi bankası, insansız katalog talepleri, CAB'da bekleyen değişiklik, bağıntılı uyarı grubu, ölçülmeye hazır iyileştirme) API + portal; geliştirme kipi (dev kimlik, sahte LLM, sahte dizin, zamanlayıcı açık). 10 dakikalık rehberli tur ve kullanıcılar: [`DEMO.md`](DEMO.md).

```bash
docker compose -f docker-compose.demo.yml up --build   # PostgreSQL; portal http://localhost:8080
./scripts/demo.sh                                      # Docker'sız: SQLite + uv + npm; portal http://localhost:5173
./scripts/demo.sh --check                              # duman testi (başlat, /health + portal kökü, durdur)
uv run python -m serviszeka.demo_seed                  # yalnızca tohum (idempotent; SZ_ENVIRONMENT=development dışında reddeder)
```

### Yayınlanmış Docker imajları

`.github/workflows/serviszeka-docker.yml`, `main`'e gelen her değişiklikte ve `serviszeka-v*` etiketlerinde iki imajı GitHub Container Registry'ye yayınlar (linux/amd64 + linux/arm64):

| İmaj | İçerik |
| --- | --- |
| `ghcr.io/mserses/serviszeka-api` | API; üretim ve demo için aynı imaj, davranış `SZ_*` ortam değişkenleriyle seçilir |
| `ghcr.io/mserses/serviszeka-portal-demo` | Demo portalı: dev girişi ve `http://localhost:8000` API adresi pakete gömülü. Üretim portalı kurumun OIDC/API adresleriyle kendi derlemesini gerektirir (`docs/KURULUM.md`) |

Etiketler: `latest` (main), `sha-<kısa commit>`, `serviszeka-v1.2.3` etiketinden `1.2.3`. Demo'yu derlemeden, yayınlanmış imajlarla açmak için:

```bash
docker compose -f docker-compose.demo.yml -f docker-compose.ghcr.yml up
```

Paketler ilk yayında GitHub'da özeldir; herkese açmak için GitHub > Packages > paket ayarları > "Change visibility".

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

**API anahtarı olmadan triage.** Yerel denemede `SZ_LLM_PROVIDER=fake` ile yapay zekâ sınıflandırması, Anthropic yerine anahtar kelime kurallarına dayalı deterministik bir istemciyle çalışır (ör. "tüm şirket" + "çöktü" → etki/aciliyet yüksek → P1 önerisi, onay gerekli). Maskeleme, KB kaynak doğrulama, politika ve denetim zinciri gerçek akıştaki gibi işler; kararlarda model `fake-deterministic` görünür. Bu ayar **yalnızca** `SZ_ENVIRONMENT=development` iken kabul edilir; başka ortamda uygulama açılmaz.

## Web portalı

`portal/` altında Vite + React 18 + TypeScript (strict) ile yazılmış Türkçe arayüz: self-servis talep (`/talep`, `/taleplerim`), destek uzmanı çalışma alanı (`/kayitlar`), onay kutusu (`/onaylar`), bilgi bankası (`/bilgi-bankasi`), değişiklik yönetimi (`/degisiklikler` liste + takvim, `/degisiklikler/yeni`, `/degisiklikler/:id` risk/CAB/geçişler), CMDB (`/cmdb`, `/cmdb/:id` etki grafiği) ve yönetim (`/yonetim/denetim`, `/yonetim/entegrasyonlar`). Ayrıntılar, ekran sahipliği ve kurallar: [`portal/README.md`](portal/README.md).

Gereksinim: Node 22 ve npm.

```bash
# 1) API (ayrı terminal, serviszeka/ kökünde) — dev kimlik doğrulaması + sahte LLM
SZ_ENVIRONMENT=development SZ_AUTH_MODE=dev SZ_LLM_PROVIDER=fake \
SZ_CORS_ORIGINS=http://localhost:5173 \
  uv run uvicorn serviszeka.main:app --reload

# 2) Portal
cd portal
npm ci
cp .env.example .env.local     # VITE_AUTH_MODE=dev, VITE_API_BASE_URL=http://localhost:8000
npm run dev                    # http://localhost:5173 → /giris'te kullanıcı adı + rol seçin
```

Kontroller ve testler (`portal/` içinde):

```bash
npm run typecheck && npm run lint && npm test -- --run && npm run build
npm run e2e        # Playwright: gerçek API (geçici SQLite, dev kimlik, sahte LLM) + Vite
npm run sync:api   # API değişince: openapi.json, src/api/schema.d.ts ve taksonomi kopyası
```

Uçtan uca senaryolar (`portal/e2e/`): son kullanıcı talep açar ve Taleplerim'de görür; uzman yapay zekâ ile sınıflandırır (P1 önerisi → onay gerekli), onaylayıcı onaylar, uzman güncel önceliği görür; bayat öneri onayı 409 "yeniden triage gerekli" gösterir; bilgi bankası makalesi oluşturulur, yayınlanır ve aranır; yönetici denetim zincirini doğrular. Faz 2: change_manager bağımlısı olan bir CMDB CI'ı için normal RFC açar (canlı etki önizlemesi) → yapay zekâ riski değerlendirir → gönderir → CAB'a yollar; talep eden oy veremez (arayüz + API 403), iki CAB üyesi onaylar → zamanlanır; aynı CI'da çakışan ikinci değişiklik zamanlanamaz → uygulanır → PIR ile kapatılır (`change-flow`); şablondan standart değişiklik düşük riskte CAB'ı atlar, aynı şablon kritiklik-1 prod CI'da normal'e çevrilir ve CAB'sız zamanlanamaz (`standard-change`); son kullanıcı `/degisiklikler` ve `/cmdb`'ye ulaşamaz, API 403 (`requester-change-access`). `npm run e2e` API'yi (`127.0.0.1:8765`) ve Vite'ı (`127.0.0.1:5180`) kendisi başlatır; her çalıştırma boş bir geçici veritabanıyla başlar. Tarayıcı olarak Playwright'ın Chromium'u kullanılır (`npx playwright install chromium`; kurulu bir tarayıcı varsa `PLAYWRIGHT_BROWSERS_PATH`). Derlenmiş imaja karşı koşmak için: `E2E_WEB_URL=http://127.0.0.1:8080 npm run e2e`. Faz 3 (`faz3-*.spec.ts`) ve Faz 4 (`faz4-*.spec.ts`) senaryoları kendi backend + portal çiftlerinde koşar (8766/5181 ve 8767/5182); Faz 4 backend'i açılmadan önce `e2e/faz4_data.py seed` SENTETİK geçmiş olayları tohumlar, CSI senaryosu ölçüm penceresini `faz4_data.py time-travel` ile ileri sarar.

**Üretim imajı.** `portal/Dockerfile` çok aşamalıdır: Node 22 ile derler, nginx ile (root olmayan kullanıcı, 8080) statik SPA olarak sunar. Bilinmeyen yollar `index.html`'e düşer (istemci tarafı yönlendirme); `/assets/` uzun süre önbelleklenir, `index.html` her seferinde doğrulanır, kaynak haritaları imaja girmez. Her yanıtta güvenlik başlıkları (`Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, HSTS) bulunur. CSP sıkıdır: betik ve stil yalnızca kendi kökeninden (`'unsafe-inline'` yok); `connect-src` / `frame-src` / `form-action` için yalnızca çalışma zamanında verilen `PORTAL_API_ORIGIN` ve `PORTAL_OIDC_ORIGIN` kökenleri eklenir. `VITE_*` derleme argümanları pakete gömülür (gizli bilgi değildir).

```bash
docker build -t serviszeka-portal portal \
  --build-arg VITE_API_BASE_URL=https://api.ornek.com \
  --build-arg VITE_OIDC_AUTHORITY=https://sso.ornek.com/realms/serviszeka \
  --build-arg VITE_OIDC_CLIENT_ID=serviszeka-portal
docker run -p 8080:8080 -e PORTAL_API_ORIGIN=https://api.ornek.com \
  -e PORTAL_OIDC_ORIGIN=https://sso.ornek.com serviszeka-portal
```

OIDC istemcisinde (public client, PKCE) yönlendirme adresleri `<portal-kökeni>/auth/callback` ve `<portal-kökeni>/auth/silent` (sessiz yenileme; yalnızca bu sayfa aynı kökenden çerçevelenebilir), çıkış dönüşü `<portal-kökeni>/giris` olarak kayıtlı olmalı; API'de `SZ_CORS_ORIGINS` portal kökenini içermelidir.

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
| `agent` | Olay ve bilgi makalesi okuma/oluşturma/güncelleme, triage başlatma; değişiklik açma, kendi taslağını gönderme, atandığı değişikliği uygulama, risk değerlendirmesi başlatma; CMDB okuma |
| `approver` | Yapay zekâ kararlarını onaylama, senkron çakışmalarını listeleme/çözme; olay ve makaleleri okuma |
| `admin` | Her şey: entegrasyon durumu, pull/push, denetim günlüğü (`/audit`) dahil; diğer tüm rolleri kapsar |
| `change_manager` | Değişiklik açma, yetkilendirme/zamanlama/kapatma, CAB oyu ve ECAB onayı (kendi talebi/uygulayıcısı olduğu değişiklik hariç), yapay zekâ risk önerisini onaylama, CMDB yazma, standart değişiklik şablonları ve dondurma pencereleri |
| `cab` | Değişiklik Danışma Kurulu: değişiklikleri ve CMDB'yi okur, değişiklikleri onaylar/reddeder (talep eden ve uygulayıcı kendi değişikliğini asla onaylayamaz) |
| `ai_agent` | Faz 3: yapay zekâ ajanı hizmet sorumlusu (client credentials). Aktör `ai:<azp/appid>` olur, diğer roller yok sayılır; yalnızca MCP (`/mcp`) araçlarını risk sınıfı ve politika denetimiyle kullanır, REST insan uçlarına erişemez, hiçbir eylem isteğini onaylayamaz |
| `requester` | Self-servis son kullanıcı: `POST /incidents` (arayan her zaman kendi kullanıcı adıdır; gövdedeki `caller` ve `impact` yok sayılır) ve yalnızca arayanı kendisi olan olayları `GET /incidents` / `GET /incidents/{id}` ile görür (başkasınınki `404`). Yapay zekâ kararlarını, triage'ı, SLA uyarılarını, bilgi bankasını göremez; yanıtlarda `ai_confidence` her zaman `null` döner |

`requester` ve yalnızca `approver` rolündeki kullanıcı değişiklik (`/changes`) ve CMDB (`/cmdb`) uçlarını göremez. Faz 2 servis API'si, durum makinesi ve kapı kuralları: [`docs/faz2.md`](docs/faz2.md).

**Faz 3 (otomasyon).** Araçlar risk sınıfıyla tanımlanır: R0 okuma (yapay zekâ serbest), R1 düşük riskli yazma (yapay zekâ yalnızca gölge mod kapalı + güven ≥ eşik iken), R2 her zaman insan onayı, R3 insan onayı + ikinci onaylayan ya da CAB onaylı değişiklik. R2/R3 çağrısı çalışmaz, bir eylem isteği açar ve `{"status": "pending_approval", "action_request_id": ...}` döner; `approver`/`admin` (R3 değişikliğe bağlıysa `change_manager`) `GET /actions`, `POST /actions/{id}/approve|reject` ile karar verir, son onayda istek onaylayanın kimliğiyle yürütülür (talep eden kendi isteğini onaylayamaz, istek 24 saatte (`SZ_ACTION_REQUEST_TTL_HOURS`) düşer). MCP istemcileri `POST /mcp` (bearer belirteç) ile bağlanır. Hizmet kataloğu (`/catalog`, 10 Türkçe öğe) ve hizmet talepleri (`/requests`, `REQ0000001`): iş onayı (yönetici/grup) önce gelir, karşılama adımları `ai:fulfilment` aktörüyle araç boru hattından geçer (R2/R3 adım eylem isteği bekler); insansız çözüm oranı `GET /requests/metrics` ve `uv run python evals/run_request_eval.py` ile ölçülür. Kimlik/posta/lisans/bildirim bağlayıcıları `SZ_CONNECTOR_PROVIDER` (`none` | `fake` (yalnızca geliştirme) | `graph`: Entra ID + Microsoft 365 Graph + Teams gelen webhook). Sözleşme: [`docs/faz3.md`](docs/faz3.md).

Rolü olmayan ama kimliği doğrulanmış her kullanıcı `GET /me` ile `{username, roles, ai_confidence_threshold}` bilgisini alabilir (web portalı gezinmeyi buna göre kurar, güven göstergesi eşiği `SZ_AI_CONFIDENCE_THRESHOLD`'dan gelir).

Portal için ek uçlar:

- `GET /incidents?mine=true` — arayanı (caller) oturumdaki kullanıcı olan kayıtlar (tüm roller; `requester` için her zaman açık).
- `GET /incidents/decisions?outcome=needs_approval&outcome=suggested&outcome=escalated&limit=50&offset=0` — olaylar arası onay kutusu (`approver`/`admin`): henüz onaylanmamış kararlar, en yeni önce; her satırda `incident_number`, `incident_title`, `incident_priority`, `incident_status`, `incident_current_version` bulunur. `outcome` verilmezse üçü birden.

Karar onayında (`POST /incidents/decisions/{id}/approve`) onaylayan belirteçteki kullanıcıdır; gövdede farklı bir `approver` gönderilirse `403` döner.

**Geliştirme kipi.** `SZ_AUTH_MODE=dev` iken `Authorization: Bearer dev:<kullanıcı>:<rol1,rol2>` kabul edilir ve **hiçbir imza doğrulanmaz**. Bu kip yalnızca `SZ_ENVIRONMENT=development` ile çalışır; başka bir ortamda uygulama açılışta hata verip durur. Üretimde asla kullanmayın.

**CORS.** Web portalı API'yi başka bir kökenden çağırıyorsa `SZ_CORS_ORIGINS` ile izinli kökenleri verin (virgülle ayrılmış veya JSON dizi, örn. `SZ_CORS_ORIGINS=http://localhost:5173`). Boşsa (varsayılan) CORS başlıkları eklenmez. `Authorization`, `If-Match` başlıklarına izin verilir; `ETag` portal tarafından okunabilir (`expose`). Çerez kullanılmadığı için `allow_credentials` kapalıdır.

**OpenAPI dışa aktarımı** (portal tip üretimi için): `uv run python -m serviszeka.openapi_export > portal/openapi.json`. Ayrıntılar: [`portal/README.md`](portal/README.md).

## Docker Compose ile çalıştırma

```bash
cp .env.example .env
docker compose up --build
```

`.env` içinde OIDC ayarlarını (`SZ_AUTH_ISSUER`, `SZ_AUTH_AUDIENCE`) ve portal için `PORTAL_OIDC_AUTHORITY` / `PORTAL_OIDC_ORIGIN` değerlerini doldurun; aksi halde uygulama açılmaz. Bu komut API'yi (`:8000`), web portalını (`http://localhost:8080`, nginx) ve PostgreSQL 16'yı (pgvector imajı) ayağa kaldırır. API'nin CORS listesi varsayılan olarak portal kökenini (`http://localhost:8080`) içerir; farklı bir adres için `.env`'de `SZ_CORS_ORIGINS` ve `PORTAL_API_BASE_URL` verin. Compose, `SZ_DATABASE_URL` değerini otomatik olarak `postgresql+asyncpg://...@db:5432/...` olarak ayarlar; veriler `pgdata` biriminde kalıcıdır. Konteyner açılışta önce `alembic upgrade head`, sonra uvicorn çalıştırır (`SZ_AUTO_CREATE_SCHEMA=false`).

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

Testlerde gerçek LLM veya ITSM servisleri çağrılmaz: LLM için `FakeLLM`, HTTP için `httpx.MockTransport` kullanılır. CI (GitHub Actions) her push/PR'da ruff, pytest ve SQLite üzerinde `alembic upgrade head` + `alembic check` çalıştırır; `portal` işi OpenAPI/tip/taksonomi kopyasının güncel olduğunu denetler, typecheck, lint, Prettier, birim testleri, derleme, Playwright E2E ve portal Docker imajı derlemesini koşar.

## Sınıflandırma değerlendirmesi (Faz 1 kapısı)

Hizmet Masası ajanının doğruluğu, `evals/data/dataset.jsonl` içindeki 200 etiketli Türkçe kayıtla ölçülür. Kapı: kayıtların en az %70'i doğru yönlendirilmeli (kind + category + assignment_group). Değerlendirme gerçek triage akışını gölge modda çalıştırır; maskeleme, sınıf listesi doğrulaması ve politika dahildir, hiçbir kayıt değişmez.

```bash
uv run python evals/run_eval.py --oracle                  # boru hattı duman testi (LLM'siz)
SZ_ANTHROPIC_API_KEY=... uv run python evals/run_eval.py  # gerçek ölçüm; kapı geçilmezse çıkış kodu 1
```

Sonuçlar `evals/results/` altına JSON ve Markdown olarak yazılır. Veri seti sentetiktir; yöntemi ve sınırları `evals/data/DATASET_CARD.md` içinde anlatılır. İzinli değerler `src/serviszeka/taxonomy.json`, etiketleme kuralları `evals/LABELING.md` dosyasındadır.

## Faz 2 kapısı

Kapı: **hiçbir riskli eylem onaysız çalışmıyor.** Kanıt `tests/test_gate_no_unapproved_risk.py` (ve `tests/test_changes.py` içindeki `test_gate_*`):

```bash
uv run pytest tests/test_gate_no_unapproved_risk.py -q
```

1. **Politika tablosu (kapsamlı ızgara).** `policy.POLICIES` içindeki her (ajan, eylem), her risk seviyesi, güven değerleri (0, eşik altı/üstü, 1, aralık dışı, NaN, ±sonsuz), eşikler (0 / 0,75 / 1) ve gölge mod açık/kapalı için `policy.evaluate` çağrılır. HIGH/CRITICAL hiçbir zaman `auto_applied` olmaz (yalnızca `needs_approval` / `escalated`); "asla otomatik" eylemler (`change.execute`, `change.approve`, `incident.resolve`) hiçbir koşulda otomatik uygulanmaz; otomatik uygulama yalnızca gölge mod kapalı, geçerli güven ≥ eşik ve risk ≤ eylemin sınırı iken olur. Tabloda HIGH/CRITICAL'e kadar otomatik izin veren eylem bulunamaz, değişiklik ajanlarının sınırı en fazla LOW'dur; listede olmayan eylem reddedilir.
2. **Statik tarama (sezgisel).** `src/serviszeka/agents/*.py` AST ile taranır. Veritabanına yazan modül (`session.add/flush/...` ya da adı `apply_` / `update_` / `set_` / `_set_` ile başlayan bir uygulama çağrısı) `policy.evaluate`, `AIDecision(...)` ve `audit.record` içermelidir; uygulama çağrısı içeren her fonksiyonda uygulamadan önce `policy.evaluate` çağrılmalı ve fonksiyon `AUTO_APPLIED` sonucuna bakmalıdır. Tek istisna insan onayı yoludur (`approver`/`user` parametresi alıp `approved_by` yazan fonksiyon). Ajanlar `transition`, `record_approval`, `approve_ai_decision` çağıramaz ve `anthropic`'i doğrudan içe aktaramaz. Kural basit bir sezgiseldir, kod incelemesinin yerine geçmez; denetleyicinin bu ihlalleri gerçekten yakaladığı sentetik kaynaklarla ayrıca sınanır.
3. **Davranış (API üzerinden).** Gölge mod kapalı, güven eşiği 0 olsa bile: `ai:` önekli aktör (yönetici rolleriyle bile) değişiklik durumunu değiştiremez, CAB onayı veremez, yapay zekâ kararını onaylayamaz; risk ajanı (`POST /changes/{id}/assess`) durumu hiç değiştirmez ve onay kaydı oluşturmaz. Normal değişiklik gerekli CAB onayları olmadan (0 ya da 1/2 onay, talep eden/uygulayıcı kendi onayı sayılmaz) `scheduled`/`implement`'e ulaşamaz; durum veritabanında zorla `scheduled` yapılsa bile uygulama anında yetki yeniden denetlenir. Yüksek riskli standart değişiklik normal'e çevrilir ve CAB'ı atlayamaz; risk sonradan zorla yükseltilirse uygulanamaz. Acil (ve başarısız) değişiklik PIR (`review_notes`; boş/boşluk kabul edilmez) olmadan kapanamaz; ECAB için CAB onayı yetmez, change_manager onayı gerekir. Triage P1/P2'yi onaysız uygulayamaz (`needs_approval`; öncelik değişmez, yalnızca onaylayıcı uygular). Bir kontrol testi aynı akışın gerekli onaylarla uygulamaya ulaştığını gösterir.

**Değişiklik risk ajanı** (`agents/change_risk.py`, `POST /changes/{id}/assess`, agent/change_manager): önce belgelenmiş deterministik skor (tür, CMDB etki analizinden en yüksek kritiklik ve etkilenen iş hizmeti sayısı, prod, aynı CI'larda son 180 günde başarısız değişiklik oranı, son 30 günde kök CI'lara bağlı olaylar (`incident_cis`; bağ yoksa 0 ve kararda not edilir), eksik/zayıf geri dönüş planı); ardından LLM (hızlı model, `serviszeka.llm`) yalnızca Türkçe anlatı + kontrol listesi yazar ve gerekçeli en fazla ±10 düzeltme önerebilir; nihai skor 0–100'e kırpılır. Karar `AIDecision` (`change_risk.assess_risk`; girdiler, deterministik döküm, LLM çıktısı) ve denetim kaydı olarak saklanır; risk alanlarını yalnızca `policy.evaluate` `auto_applied` derse yazar (yükseltme). Düşürme `needs_approval` olur ve `POST /changes/decisions/{id}/approve` ile change_manager onaylar. Yalnızca `draft`/`assess` durumunda çalışır (aksi halde 422). `SZ_LLM_PROVIDER=fake` (geliştirme/E2E) deterministik skordan türetilmiş bir anlatı ve kontrol listesi döndürür (düzeltme her zaman 0), böylece "Riski değerlendir" API anahtarı olmadan da çalışır.

4. **Uçtan uca (portal + gerçek API).** `portal/e2e/change-flow.spec.ts`, `standard-change.spec.ts`, `requester-change-access.spec.ts`: talep eden kendi değişikliğine oy veremez (API 403), 1 onayla zamanlama reddedilir (422), CAB üyesi zamanlayamaz, aynı CI'da çakışan değişiklik zamanlanamaz (409, çakışma diyaloğu), kritiklik-1 prod CI'daki "standart" değişiklik normal'e çevrilir ve doğrudan zamanlanamaz (422), son kullanıcı değişiklik/CMDB ekranlarına ve uçlarına erişemez.

Ayarlar: `SZ_CAB_MIN_APPROVALS` (varsayılan 2, en az 1) normal değişiklik için gereken CAB onayıdır; aynı onaylayanın eşzamanlı ikinci oyu tekil kısıtla durdurulur ve `409` döner.

## Faz 3 — Otomasyon

Kapı: **standart taleplerin %50'si insansız karşılanıyor**, bu sırada her R2/R3 eylem insan onayı istiyor. Sözleşmenin tamamı [`docs/faz3.md`](docs/faz3.md).

### Araçlar ve risk sınıfları

Her otomasyon yeteneği `serviszeka.tools` altında `alan.fiil_nesne` adlı bir araçtır (61 araç). Risk sınıfı: **R0** okuma (yapay zekâ serbest), **R1** düşük riskli yazma (yapay zekâ yalnızca gölge mod kapalı + güven ≥ eşik iken; yoksa onaya düşer), **R2** her zaman insan onayı, **R3** insan onayı + ikinci (farklı) onaylayan ya da CAB onaylı değişiklik. Koşullu risk yalnızca yükseltir: izin listesi dışı/ayrıcalıklı/bilinmeyen gruba ekleme R3; doğrulanmamış (başkası adına, talepsiz ya da devre dışı hesap) parola sıfırlama **ve kilit açma** R2. R1-R3 araçlar `dry_run` ve `idempotency_key` alır. R2/R3 çağrısı çalışmaz: eylem isteği (`ActionRequest`) açar; onay `GET /actions`, `POST /actions/{id}/approve|reject` ya da portalda **Onaylar → Eylem onayları** (yapay zekâ ve talep eden onaylayamaz; 24 saatte düşer).

### MCP sunucusu

Araç kaydı resmi MCP SDK ile **`POST /mcp`** adresinde (streamable HTTP, durumsuz, JSON yanıt) sunulur. Kimlik REST ile aynıdır: `Authorization: Bearer <OIDC JWT>`; belirteçsiz istek `401`. Yapay zekâ istemcisi kimlik sağlayıcıda kendi istemcisiyle (client credentials) belirteç alır ve **`ai_agent`** rolü taşır; ServisZeka onu `ai:<azp|appid>` aktörüne ve yalnızca `ai_agent` rolüne indirger (insan REST uçlarına erişemez, onay veremez). Keycloak / Entra ID kurulumu: `.env.example` "yapay zekâ ajanı hizmet sorumluları". `tools/list` her aracı JSON şemasıyla ve açıklamanın başında risk sınıfıyla (`[R3 · …]`) döndürür; yapay zekâya dönen sonuç PII maskelidir. R1-R3 araçlar zarf alanlarını da alır: `dry_run`, `idempotency_key`, `reason`, `confidence`.

Claude Code / Claude Desktop gibi bir MCP istemcisi için (`.mcp.json`; belirteç ortam değişkeninden):

```json
{
  "mcpServers": {
    "serviszeka": {
      "type": "http",
      "url": "https://servis.ornek.com.tr/mcp",
      "headers": { "Authorization": "Bearer ${SERVISZEKA_AI_TOKEN}" }
    }
  }
}
```

Yerelde deneme (dev kimlik, sahte dizin; `SZ_ENVIRONMENT=development SZ_AUTH_MODE=dev SZ_LLM_PROVIDER=fake SZ_CONNECTOR_PROVIDER=fake`):

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H "Authorization: Bearer dev:servis-ajani:ai_agent" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"identity.add_to_group","arguments":{"user":"ali","group":"sg-domain-admins","reason":"deneme"}}}'
# -> {"status": "pending_approval", "action_request_id": "...", "risk_class": "R3", ...}
```

### Hizmet kataloğu ve talepler

`/catalog` 10 Türkçe öğeyle tohumlanır (`POST /catalog/seed`; sahte dizinle geliştirmede açılışta): standart (`password_reset`, `account_unlock`, `standard_software_group`, `m365_license`, `teams_channel_access`, `vpn_access`) ve standart olmayan (`shared_mailbox_access`, `new_employee_account`, `privileged_group_access`, `mfa_reset`). Grup/lisans/posta kutusu kimlikleri sahte dizine göredir; üretimde kiracının kimlikleriyle güncelleyin (`PATCH /catalog/items/{key}`). Talep (`/requests`, `REQ0000001`): iş onayı (yönetici ya da onay grubu; portalda **Taleplerim → Onayımı bekleyenler**) risk onayından önce gelir; karşılama adımları `ai:fulfilment` aktörüyle araç boru hattından geçer; R2/R3 adım eylem isteğini bekler. **İnsansız** yalnızca sıfır insan onayı ve sıfır insan düzenlemesiyle karşılanan taleptir (portalda "✓ İnsansız çözüldü" rozeti); oran `GET /requests/metrics`. Bağlayıcılar aşağıdaki gibi seçilir (ayrıntı ve doğrulanmamış uç noktalar [`docs/faz3.md`](docs/faz3.md)).

#### Bağlayıcılar

Kimlik ve bildirim sağlayıcısı ayrı seçilir: `SZ_IDENTITY_PROVIDER` + `SZ_NOTIFIER_PROVIDER`. İkisi de boşsa eski tek ayar `SZ_CONNECTOR_PROVIDER` (`none` | `fake` | `graph` | `keycloak`) aynen geçerlidir; Slack ayarlıysa bildirim Slack olur. Zorunlu ayar eksikse ya da iki ayar çelişirse uygulama açılmaz; hata mesajı eksik `SZ_` ayarlarını adıyla yazar.

| Ayar | Değer | Kurulan bağlayıcılar | Zorunlu ayarlar | Not |
|---|---|---|---|---|
| `SZ_IDENTITY_PROVIDER` | `graph` | identity, mail, license (+ `teams` bildirici) | `SZ_GRAPH_TENANT_ID`, `SZ_GRAPH_CLIENT_ID`, `SZ_GRAPH_CLIENT_SECRET` | Entra ID'de bulutta kilit açma ve paylaşılan posta kutusu izni yok |
| | `keycloak` | yalnızca identity | `SZ_KEYCLOAK_SERVER_URL`, `SZ_KEYCLOAK_REALM`, `SZ_KEYCLOAK_CLIENT_ID`, `SZ_KEYCLOAK_CLIENT_SECRET` | Kilit açma var; ikincil e-posta, posta ve lisans yok |
| | `memory` | identity, mail, license, messaging (sahte dizin) | `SZ_ENVIRONMENT=development` | Yerel deneme / E2E (`SZ_CONNECTOR_PROVIDER=fake` ile aynı) |
| `SZ_NOTIFIER_PROVIDER` | `teams` | Graph posta + Teams gelen webhook | `SZ_IDENTITY_PROVIDER=graph`, `SZ_GRAPH_SENDER_MAILBOX` | Kanal webhook'ları `SZ_TEAMS_WEBHOOK_URLS` |
| | `slack` | Slack (kanal + DM) | `SZ_SLACK_BOT_TOKEN` ya da `SZ_SLACK_WEBHOOK_URL` | Geçici parola Slack'e gitmez, kimlik yığınının ikincil adresine gider (Keycloak'ta ikincil adres yok) |
| | `log` | — (`notify.send_*` 503) | — | Nöbetçi uyarısı log ya da `SZ_ONCALL_WEBHOOK_URL` |

### Kapı ölçümü

```bash
uv run python evals/run_automation_eval.py          # etiketli 200 talep; kapı geçilmezse çıkış kodu 1
uv run python evals/run_automation_eval.py --json   # ayrıntı (talep bazında sonuç)
uv run python evals/run_automation_eval.py --stack entra      # Entra ID / Graph biçimli sahte
uv run python evals/run_automation_eval.py --stack keycloak   # Keycloak + Slack biçimli sahte
uv run pytest tests/test_automation_eval.py tests/test_gate_no_unapproved_risk.py -q
```

`evals/data/requests.jsonl`: 200 gerçekçi Türkçe hizmet talebi (10 katalog öğesinin hepsi), talep edenin dizin öznitelikleri (departman, yönetici, ikincil e-posta, hesap etkin mi, dizinde var mı, kilitli mi), kimin açtığı (kendisi / servis masası), insan kararları ve altın etiket `{item, expected_touchless, reason}` (veri kartı `evals/data/REQUESTS_CARD.md`). Her talep **gerçek talep boru hattından** geçer: bellek içi sahte dizin (ağ yok), gölge mod kapalı, varsayılan güven eşiği (0,75); LLM kullanılmaz (çağrılırsa sayılır ve kapı kalır). İnsanlar simüle edilir (yönetici / İK onay grubu, iki BT onaylayan, servis masası).

Son ölçüm:

| Ölçüt | Değer |
|---|---|
| Standart talepler insansız | **134 / 155 (%86,5)** — hedef %50 |
| Yanlış insansız (otomatikleşmemesi gerekirken insansız biten) | **0** (sert kalma koşulu) |
| Güvenlik değişmezi ihlali (insansız talepte insan dokunuşu; R2/R3'ün gerekli onaydan az onayla yürümesi) | 0 |
| Etiket uyumu (beklenen insansız == gerçekleşen) | %100 |
| Açılan eylem istekleri | R2: 64 (60 yürütüldü, 4 reddedildi) · R3: 17 (11 yürütüldü, 6 reddedildi) — yeni çalışan lisansı ve standart grup adımı doğrulanmış self-servis değilse R2 |

Öğe bazında: `vpn_access` 20/20, `teams_channel_access` 18/20, `password_reset` 34/40, `m365_license` 17/20, `account_unlock` 21/25, `standard_software_group` 24/30; standart olmayan dört öğe 0 (her biri iş ve/veya BT onayı ister). Otomatikleşmeyen standart talepler: ayrıcalıklı / bilinmeyen / izin listesi dışı grup (R3), servis masasının başkası adına açtığı ya da devre dışı/dizinde olmayan hesaba ait parola sıfırlama ve kilit açma (R2), ikincil e-postası olmayan parola sıfırlama ve boş lisansı olmayan SKU (başarısız). Ölçüm, başkası adına açılan kilit açma talebinin onaysız (R1) yürüdüğü bir açığı yakaladı; `identity.unlock_account` artık parola sıfırlama gibi koşullu R1/R2'dir.

Aynı veri seti ve etiketler, sahte dizin hedef yığının belgelenmiş kısıtlarıyla sarılarak da ölçülür (`--stack`; `connectors/fake_stacks.py`):

| Yığın (`--stack`) | Standart insansız | Fark |
|---|---|---|
| `memory` (varsayılan) | 134 / 155 (%86,5) | Sahte dizin her işlemi destekler |
| `entra` | 113 / 155 (%72,9) | Bulutta kilit açma yok: `account_unlock` 0/25 (`failed`); paylaşılan posta kutusu adımı `failed` |
| `keycloak` (+ Slack) | 117 / 155 (%75,5) | Kilit açma çalışır (21/25); geçici parola yapılandırılabilir ikincil e-posta özniteliğine SMTP ile gider (`SZ_KEYCLOAK_SECONDARY_EMAIL_ATTRIBUTE`, `password_reset` 34/40); lisans sağlayıcısı yok (`m365_license` 0/20) |

Üç yığında da yanlış insansız 0, ihlal 0. Keycloak'ta ikincil e-posta özniteliği yalnızca yöneticilerce düzenlenebilir olmalıdır (`docs/faz3.md` K11); aksi halde hesabı ele geçiren geçici parolayı kendi adresine yönlendirebilir.

Uçtan uca (`portal/e2e/faz3-*.spec.ts`, gölge modu kapalı ayrı backend + portal çifti): katalogdan "Standart yazılım grubu erişimi" insansız karşılanır ve rozet görünür; "Yetkili grup erişimi" yönetici onayından sonra R3 eylem isteği açar, ilk BT onayında `1/2` bekler, ikinci farklı onaylayanla yürür ve talep insan katkısıyla karşılanır; benzer üç olaydan eğilim taraması sorun önerir ve RCA taslağı (E2E'de FakeLLM) görünür.

## Faz 4 — Olgunluk

Kapı: **MTTR düşüşü veriden hesaplanabilir.** Sözleşmenin tamamı (formüller, uçlar, araçlar, zamanlayıcı) [`docs/faz4.md`](docs/faz4.md).

| Yetenek | Nerede | Not |
|---|---|---|
| KPI'lar ve raporlama | `services/kpi.py`, `GET /kpi`, `/kpi/definitions`, `/sla/report`; portal `/panolar` | 12 KPI (MTTR, MTTA, FCR, SLA uyumu, birikim, yeniden açılma, insansız oran, triage kabulü, değişiklik başarısı …), her biri önceki eşit pencereyle (`delta_pct`, `trend`); MTTR = `ort(resolved_at − created_at − sla_paused_seconds)` |
| Tahmine dayalı SLA | `services/sla_forecast.py`, `GET /sla/forecast`, `/sla/forecast/backtest`; portal `/sla-tahmin` | Kaplan–Meier + grup yükü, açıklanabilir Türkçe gerekçeler, zaman tabanlı geriye dönük sınama (Brier) |
| İzleme ve olay yönetimi | `POST /events/alertmanager` / `/events/generic`, `services/events.py`, `agents/events.py`; portal `/olaylar` | Paylaşılan gizli / HMAC + yeniden oynatma koruması, CMDB grafiğiyle bağıntı (kök CI), grup başına tek olay; P1/P2 olay açmak R2 (insan onayı); çözülen uyarı not + çözüm **önerisi**; üstlen (R1) / sustur (R2) `POST /events/groups/{id}/acknowledge|silence` |
| Sürekli iyileştirme (CSI) | `services/improvements.py`, `agents/improvement.py` (`ai:csi`), `/improvements`; portal `/iyilestirmeler` | 8 deterministik sinyal (MTTR artışı, SLA ihlali, triage kabulü …) → fikir (R1); onay / başlatma / uygulandı / kapatma yalnızca insan; KPI tabanı → uygulama sonrası önce/sonra ölçümü (`measurement.delta_pct`, `target_met`) |
| Kalıcı bildirim kuyruğu + zamanlayıcı | `notifications.py` (`notification_outbox`), `scheduler.py` | En az bir kez teslim, idempotency anahtarı, kira ile tek kopya; işler: kuyruk, eylem süre aşımı, SLA uyarısı, bekleyen talepler, `csi_review` |

Yeni araçlar (hepsi `policy.POLICIES`'te, kapı `tests/test_gate_no_unapproved_risk.py` bölüm 7-9): `kpi.report`, `sla.report`, `sla.forecast` (R0); `event.list_alerts`, `event.correlate` (R0), `event.acknowledge` (R1), `event.create_incident` (R1, P1/P2 olacaksa R2), `event.silence` (R2); `csi.list`, `csi.detect_signals` (R0), `csi.create_improvement`, `csi.measure_improvement` (R1). Yapay zekâ yalnızca yönetici olmayan bir insan rolünün de çağırabildiği araçları çağırabilir (`tools/executor.ai_callable`).

Göçler: `0010` bildirim kuyruğu + zamanlayıcı kiraları, `0011` KPI indeksleri (`incidents.resolved_at` / `responded_at`, `audit_log.occurred_at`), `0012` izleme/olay, `0013` sürekli iyileştirme (tek doğrusal zincir).

### Kapı ölçümü — MTTR kanıtı (SENTETİK)

```bash
uv run python evals/run_mttr_evidence.py          # özet + kapı; geçilmezse çıkış kodu 1
uv run python evals/run_mttr_evidence.py --json   # ayrıntı (--save -> evals/results/mttr_evidence.json)
uv run pytest tests/test_mttr_evidence.py tests/test_kpi.py tests/test_improvements.py -q
```

Gerçek veri **değildir**: bellek içi SQLite'ta deterministik bir önce/sonra senaryosu (`src/serviszeka/mttr_evidence.py`) kurulur ve MTTR Faz 4 KPI formülleriyle hesaplanır; sonuç bağımsız aritmetikle ve sürekli iyileştirme kaydının gerçek ölçüm akışıyla çapraz denetlenir.

| Ölçüt | Önce (30 gün) | Sonra (30 gün) | Δ |
|---|---|---|---|
| MTTR `network_ops` (hedef grup), `kpi.report` | 9,50 sa | 4,25 sa | **−%55,3** |
| Aynı, bağımsız aritmetik (duvar saati − SLA duraklatması) | 9,50 sa | 4,25 sa | −%55,3 |
| MTTR `dba` (kontrol grubu) | 6,00 sa | 6,00 sa | %0 |
| CSI kaydı (taban → ölçülen, hedef taban × 0,7) | 9,50 sa | 4,25 sa | −%55,3, hedef tuttu |

Gerçek bir kurulumda aynı kanıt `GET /kpi?kpi=mttr&days=30` (önceki pencereye göre `delta_pct`) ve kapanan iyileştirmenin `measurement` alanıdır.

Uçtan uca (`portal/e2e/faz4-*.spec.ts`; gölge mod kapalı, SENTETİK geçmişle tohumlanmış ayrı backend + portal çifti, `E2E_PROFILE=faz4`): Alertmanager webhook'u veritabanı CI'sı ve bağımlı uygulamada ateşleyen uyarılarla **tek** bağıntılı grup ve **tek** olay (P2 → onay bekler, onaylanınca açılır) üretir, çözülen uyarı aynı olaya not ekler; KPI panosu tohumlanmış veriyle API ile aynı değerleri gösterir; tohumdaki MTTR gerilemesinden CSI ajanı iyileştirme önerir → onay → uygulama → ölçüm → kapanış ve ölçülen değer görünür.

## Üretim belgeleri

- [`docs/KURULUM.md`](docs/KURULUM.md) — mimari, PostgreSQL ve göçler, tüm `SZ_` ayarları (`docs/_gen_settings.py` ile üretilir), Keycloak / Entra ID OIDC, bağlayıcı izinleri, MCP istemcisi, TLS/CSP, yedekleme ve sürüm yükseltme
- [`docs/ISLETIM.md`](docs/ISLETIM.md) — sağlık kontrolleri, zamanlayıcı kiraları, ölü bildirimler, denetim zinciri, gizli döndürme, olay oyun kitapları, KVKK, kapıların yeniden ölçümü
- [`docs/GUVENLIK.md`](docs/GUVENLIK.md) — tehdit modeli, yapılan incelemeler, kalan riskler

## Yol haritası

| Faz | Süre | Kapsam | Geçiş kapısı |
|---|---|---|---|
| **Faz 0 — Temel** | 2 hafta | Proje iskeleti, veri modeli, politika motoru, denetim zinciri, KVKK maskeleme, CI | Testler yeşil; uygulama tek komutla ayağa kalkıyor |
| **Faz 1 — MVP** | 6 hafta | Hizmet Masası + Olay ajanı, RAG bilgi tabanı, SLA takibi, self-servis portal, ITSM köprüsü (ServiceNow / Jira SM) | Olayların **%70'i** doğru sınıflandırılıyor |
| **Faz 2 — Kontrol** | 6 hafta | Değişiklik yönetimi ve CAB, CMDB, gelişmiş politika kuralları | Riskli hiçbir eylem onaysız çalışmıyor |
| **Faz 3 — Otomasyon** | 8 hafta | Talep karşılama otomasyonu, problem ajanı, MCP entegrasyonu | Standart taleplerin **%50'si** insansız karşılanıyor |
| **Faz 4 — Olgunluk** | Sürekli | Sürekli iyileştirme döngüsü, tahmine dayalı SLA, KPI/raporlama, izleme ve olay yönetimi | MTTR düşüşü veriden hesaplanabiliyor (`evals/run_mttr_evidence.py`) |

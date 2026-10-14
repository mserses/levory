# ServisZeka — üretim kurulumu

Bu belge ServisZeka API'sini (FastAPI) ve web portalını (nginx) üretime kurmak için adım adım
yoldur. İşletim (sağlık, kuyruk, denetim, olay oyun kitapları) [`ISLETIM.md`](ISLETIM.md), tehdit
modeli ve kalan riskler [`GUVENLIK.md`](GUVENLIK.md). Davranış sözleşmeleri:
[`faz2.md`](faz2.md), [`faz3.md`](faz3.md), [`faz4.md`](faz4.md).

> **"Doğrulanmalı"** işaretli maddeler kodda belgelere göre yazıldı ve testlerde
> `httpx.MockTransport` ile sınandı; gerçek bir kiracıda/sunucuda **denenmedi**. Üretime almadan
> önce bir test ortamında doğrulayın.

## 1. Mimari

```mermaid
flowchart LR
  subgraph Kullanicilar["Kullanıcılar"]
    B["Tarayıcı<br/>(portal SPA)"]
    M["MCP istemcisi<br/>(ai_agent hizmet sorumlusu)"]
  end
  AM["Alertmanager /<br/>izleme kaynağı"]
  IDP["OIDC sağlayıcı<br/>Keycloak / Entra ID"]
  subgraph DMZ["Ters vekil (TLS)"]
    P["nginx portal imajı<br/>:8080 statik SPA + CSP"]
    RP["API vekili"]
  end
  subgraph APP["ServisZeka API (uvicorn :8000)"]
    REST["REST uçları<br/>(OIDC bearer)"]
    MCPS["/mcp<br/>(streamable HTTP)"]
    EV["/events/*<br/>(paylaşılan gizli / HMAC)"]
    SCH["Zamanlayıcı<br/>(kira ile tek kopya)"]
  end
  DB[("PostgreSQL 16<br/>şema: alembic")]
  LLM["Anthropic API<br/>(maskeli istem)"]
  CON["Bağlayıcılar<br/>Graph · Keycloak Admin · Slack · SMTP · Teams webhook"]
  ITSM["ServiceNow / Jira SM<br/>(yönetici tetikli senkron)"]
  ONC["Nöbetçi webhook'u"]

  B -->|statik dosyalar| P
  B -->|Authorization Code + PKCE| IDP
  B -->|Bearer JWT| RP
  M -->|client credentials| IDP
  M -->|Bearer JWT| RP
  AM -->|webhook| RP
  RP --> REST & MCPS & EV
  REST & MCPS & EV & SCH --> DB
  REST -. JWKS .-> IDP
  REST --> LLM
  REST --> CON
  SCH --> CON
  SCH --> ONC
  REST --> ITSM
```

- API tek süreçtir: REST, `/mcp` ve izleme webhook'ları aynı uygulamadadır; arka plan
  zamanlayıcısı (`scheduler.py`) uygulama yaşam döngüsünde çalışır. Birden fazla kopya
  çalıştırılabilir: her zamanlayıcı işi `scheduler_leases` satırıyla tek kopyada koşar.
- Durum yalnızca veritabanındadır (kuyruk, kiralar, eylem istekleri, denetim zinciri). JWKS
  önbelleği süreç içidir.
- Kimlik doğrulamasız uçlar yalnızca `GET /health`, `/docs`, `/openapi.json`; `/events/*` kendi
  paylaşılan gizlisini, `/mcp` bearer belirtecini doğrular.

## 2. Ön koşullar

| Bileşen | Sürüm / not |
|---|---|
| API imajı | `Dockerfile` (Python 3.12, `uv sync --frozen --no-dev`, root olmayan kullanıcı 10001, `HEALTHCHECK /health`). Kaynaktan: Python ≥ 3.11 + [uv](https://docs.astral.sh/uv/) |
| Portal imajı | `portal/Dockerfile` (Node 22 ile derleme, nginx 1.28 ile sunum, root olmayan, 8080, `/healthz`) |
| Veritabanı | PostgreSQL 16 (compose `pgvector/pgvector:pg16` imajını kullanır; pgvector eklentisi **gerekmez**, gömme vektörleri JSON sütununda) |
| Kimlik | OIDC sağlayıcı: Keycloak ya da Microsoft Entra ID (RS256/ES256 imzalı erişim belirteci) |
| Ağ çıkışı | IdP (JWKS/keşif), `api.anthropic.com` (LLM), seçilen bağlayıcılar (Graph, Keycloak Admin, `slack.com`, SMTP, Teams/nöbetçi webhook'ları), isteğe bağlı ServiceNow / Jira |
| Saat | Tüm kopyalarda NTP: zamanlayıcı kiraları ve webhook imza penceresi saate dayanır |

Uygulama ayarları yalnızca `SZ_` önekli ortam değişkenlerinden (ya da çalışma dizinindeki `.env`)
okunur ve süreç başında bir kez önbelleğe alınır: **her ayar değişikliği yeniden başlatma
gerektirir.** Bilinmeyen `SZ_` adları sessizce yok sayılır (`extra="ignore"`); yazım hatasını
aşağıdaki tabloyla karşılaştırın.

Açılışta reddedilen yapılandırmalar (uygulama başlamaz, hata ayar **adlarını** yazar, değerleri
yazmaz): `SZ_AUTH_MODE=dev`, `SZ_LLM_PROVIDER=fake`, sahte dizin (`memory`/`fake`) ve
`SZ_SMTP_ALLOW_PLAINTEXT=true` `SZ_ENVIRONMENT=development` dışında; OIDC kipinde
`SZ_AUTH_ISSUER`/`SZ_AUTH_AUDIENCE` eksik; seçilen kimlik/bildirim sağlayıcısının zorunlu ayarı
eksik ya da `SZ_CONNECTOR_PROVIDER` ile çelişki; SMTP'de STARTTLS ve örtük TLS birlikte ya da ikisi
de kapalı.

## 3. PostgreSQL ve göçler

1. Veritabanı ve sahibi olan bir kullanıcı açın (uygulama göç sırasında tablo/indeks kurar):

   ```sql
   CREATE ROLE serviszeka LOGIN PASSWORD '<gizli>';
   CREATE DATABASE serviszeka OWNER serviszeka;
   ```

2. `SZ_DATABASE_URL=postgresql+asyncpg://serviszeka:<gizli>@db.ornek:5432/serviszeka` ve
   **`SZ_AUTO_CREATE_SCHEMA=false`** verin (imajda varsayılan zaten false).
3. Şemayı göçlerle kurun:

   ```bash
   docker run --rm --env-file serviszeka.env serviszeka-api alembic upgrade head
   docker run --rm --env-file serviszeka.env serviszeka-api alembic current   # ... (head)
   ```

   İmajın varsayılan komutu da açılışta `alembic upgrade head` çalıştırır. Birden fazla kopyada
   göçü **bir kez**, ayrı bir iş olarak çalıştırıp kopyaları sonra başlatın (eşzamanlı
   `alembic upgrade` için kilit yoktur).
4. Göç zinciri tek ve doğrusaldır (`tests/test_migrations.py`). CI göçleri SQLite üzerinde sınar
   (yükseltme + `alembic check` + geri alma); PostgreSQL yolunu üretim öncesi bir kopyada deneyin.

## 4. Ortam değişkenleri

Örnek dosya: [`.env.example`](../.env.example). Gizli değerleri (tabloda **Gizli: evet**) bir
gizli yöneticisinden enjekte edin; depoya, imaja ya da `VITE_*` derleme argümanlarına yazmayın.
`SecretStr` türündekiler günlükte/`repr`'de görünmez; `SZ_DATABASE_URL`, `SZ_ANTHROPIC_API_KEY`,
`SZ_SERVICENOW_PASSWORD`, `SZ_JIRA_API_TOKEN`, `SZ_ONCALL_WEBHOOK_URL`, `SZ_TEAMS_WEBHOOK_URLS`
düz metin türündedir (bkz. GUVENLIK.md kalan riskler).

Üretim için asgari küme:

```bash
SZ_ENVIRONMENT=production
SZ_AUTH_MODE=oidc
SZ_AUTH_ISSUER=https://sso.ornek.com/realms/serviszeka
SZ_AUTH_AUDIENCE=serviszeka-api
SZ_AUTH_ROLES_CLAIM=resource_access.serviszeka-api.roles
SZ_CORS_ORIGINS=https://portal.ornek.com
SZ_DATABASE_URL=postgresql+asyncpg://serviszeka:<gizli>@db.ornek:5432/serviszeka
SZ_AUTO_CREATE_SCHEMA=false
SZ_ANTHROPIC_API_KEY=<gizli>
SZ_AI_SHADOW_MODE=true          # ilk haftalarda gölge mod; kapı ölçümlerinden sonra kapatın
```

Aşağıdaki tablo `config.py`'den üretilir (`uv run python docs/_gen_settings.py --write`);
`tests/test_docs_settings.py` eksik ya da bayat satırı yakalar.

<!-- SZ_SETTINGS:BEGIN (docs/_gen_settings.py --write ile üretilir; elle düzenlemeyin) -->
#### Ortam ve kimlik doğrulama

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_ENVIRONMENT` | `development` \| `test` \| `staging` \| `production` | `production` |  | Üretimde `production` bırakın. `development` dışındaki her ortamda dev kimliği, sahte LLM, sahte dizin ve düz metin SMTP reddedilir. |
| `SZ_AUTH_MODE` | `oidc` \| `dev` | `oidc` |  | `oidc` (bearer JWT). `dev` imza doğrulamaz; yalnızca `development`. |
| `SZ_AUTH_ISSUER` | metin | — |  | OIDC kipinde **zorunlu**. Belirteçteki `iss` ile birebir aynı olmalı. |
| `SZ_AUTH_AUDIENCE` | metin | — |  | OIDC kipinde **zorunlu**. Beklenen `aud`; Keycloak istemci rolleri de `resource_access.<bu değer>.roles` yolundan okunur. |
| `SZ_AUTH_JWKS_URL` | metin | — |  | Boşsa `<issuer>/.well-known/openid-configuration` keşfinden. |
| `SZ_AUTH_ROLES_CLAIM` | metin | — |  | Rol talebinin noktalı yolu. Boşsa `realm_access.roles` + `resource_access.<aud>.roles` + `roles` birlikte okunur. |
| `SZ_AUTH_USERNAME_CLAIM` | metin | `preferred_username` |  | Aktör adı talebi; yoksa `upn`, `email`, `sub`. |
| `SZ_AUTH_LEEWAY_SECONDS` | int | `30` |  | `exp`/`nbf` saat kayması toleransı (sn). |
| `SZ_AUTH_HTTP_TIMEOUT` | sayı | `3.0` |  | JWKS / keşif isteği zaman aşımı (sn). |
| `SZ_CORS_ORIGINS` | liste | `[]` |  | Portal kökenleri (virgüllü ya da JSON dizi). Boşsa CORS kapalı. |

#### Veritabanı

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_DATABASE_URL` | metin | `sqlite+aiosqlite:///./serviszeka.db` | evet | Üretimde `postgresql+asyncpg://kullanıcı:parola@host:5432/vt`. |
| `SZ_AUTO_CREATE_SCHEMA` | bool | `true` |  | Üretimde **false**; şema yalnızca `alembic upgrade head` ile. |

#### LLM ve yapay zekâ yönetişimi

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_LLM_PROVIDER` | `anthropic` \| `fake` | `anthropic` |  | `fake` yalnızca `development` (deterministik, ağsız). |
| `SZ_ANTHROPIC_API_KEY` | metin | — | evet | Boşsa LLM gerektiren uçlar 503, zamanlanmış CSI gerekçesi deterministik metne düşer. |
| `SZ_LLM_REASONING_MODEL` | metin | `claude-opus-5-5` |  | Sorun yönetimi RCA taslağı. |
| `SZ_LLM_FAST_MODEL` | metin | `claude-haiku-5-5` |  | Triage, değişiklik risk anlatısı, CSI gerekçesi. |
| `SZ_AI_CONFIDENCE_THRESHOLD` | sayı | `0.75` |  | Bu güvenin altındaki yapay zekâ kararı insana gider. |
| `SZ_AI_SHADOW_MODE` | bool | `true` |  | true iken ajanlar yalnızca önerir (R1 araçlar da onaya düşer). |

#### Onaylar ve süreler

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_CAB_MIN_APPROVALS` | int (≥1) | `2` |  | Normal değişiklik için en az CAB onayı. |
| `SZ_ACTION_REQUEST_TTL_HOURS` | int (≥1, ≤720) | `24` |  | Onay bekleyen eylem isteği bu süre sonunda `expired`. |
| `SZ_REQUEST_APPROVAL_TTL_HOURS` | int (≥1, ≤2160) | `168` |  | İş onayı bekleyen hizmet talebi bu süre sonunda reddedilir. |

#### Bağlayıcı seçimi ve grup politikası

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_CONNECTOR_PROVIDER` | `none` \| `fake` \| `graph` \| `keycloak` | `none` |  | Eski tek seçim. Yeni kurulumda `none` bırakıp aşağıdaki iki ayarı kullanın; çelişki açılışı durdurur. |
| `SZ_IDENTITY_PROVIDER` | `graph` \| `keycloak` \| `memory` | — |  | `graph` \| `keycloak` (\| `memory` yalnızca geliştirme). |
| `SZ_NOTIFIER_PROVIDER` | `teams` \| `slack` \| `log` | — |  | `teams` \| `slack` \| `log`. |
| `SZ_IDENTITY_STANDARD_GROUPS` | liste | `[]` |  | Ayrıcalıksız, iş onayı gerektirmeyen grup kimlikleri; yalnızca bunlara ekleme R1 olabilir. Boş = her ekleme R3. |
| `SZ_IDENTITY_PRIVILEGED_GROUPS` | liste | `[]` |  | Her zaman ayrıcalıklı (R3) gruplar. |

#### Microsoft Entra ID / Graph / Teams

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_GRAPH_TENANT_ID` | metin | — |  | `SZ_IDENTITY_PROVIDER=graph` iken zorunlu. |
| `SZ_GRAPH_CLIENT_ID` | metin | — |  | `graph` iken zorunlu (uygulama kaydı istemci kimliği). |
| `SZ_GRAPH_CLIENT_SECRET` | metin | — | evet | `graph` iken zorunlu. |
| `SZ_GRAPH_BASE_URL` | metin | `https://graph.microsoft.com/v1.0` |  | Graph API tabanı. |
| `SZ_GRAPH_LOGIN_URL` | metin | `https://login.microsoftonline.com` |  | Belirteç ucu tabanı. |
| `SZ_GRAPH_TIMEOUT` | sayı (>0) | `10.0` |  | Graph / Teams HTTP zaman aşımı (sn). |
| `SZ_GRAPH_SENDER_MAILBOX` | metin | — |  | `SZ_NOTIFIER_PROVIDER=teams` iken zorunlu (`Mail.Send` göndereni). |
| `SZ_TEAMS_WEBHOOK_URLS` | JSON nesne | `{}` | evet | `{"kanal": "https://..."}`; URL'ler belirteç taşır. |

#### Keycloak (kimlik bağlayıcısı)

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_KEYCLOAK_SERVER_URL` | metin | — |  | `keycloak` iken zorunlu; Keycloak <17 için `.../auth` ile biter. |
| `SZ_KEYCLOAK_REALM` | metin | — |  | `keycloak` iken zorunlu. |
| `SZ_KEYCLOAK_CLIENT_ID` | metin | — |  | `keycloak` iken zorunlu (hizmet hesabı etkin gizli istemci). |
| `SZ_KEYCLOAK_CLIENT_SECRET` | metin | — | evet | `keycloak` iken zorunlu. |
| `SZ_KEYCLOAK_MANAGER_ATTRIBUTE` | metin | `manager` |  | Yöneticinin kullanıcı adını taşıyan öznitelik. |
| `SZ_KEYCLOAK_SECONDARY_EMAIL_ATTRIBUTE` | metin | `secondary_email` |  | Geçici parolanın gideceği ikincil e-posta özniteliği; yalnızca yöneticilerce düzenlenebilir olmalı. |

#### SMTP

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_SMTP_HOST` | metin | — |  | Host + gönderen doluysa SMTP bildiricisi etkin. |
| `SZ_SMTP_PORT` | int (≥1, ≤65535) | `587` |  | 587 (STARTTLS) ya da 465 (örtük TLS). |
| `SZ_SMTP_SENDER` | metin | — |  | Gönderen adresi. |
| `SZ_SMTP_USERNAME` | metin | — |  | İsteğe bağlı kimlik doğrulama kullanıcısı. |
| `SZ_SMTP_PASSWORD` | metin | — | evet | İsteğe bağlı. |
| `SZ_SMTP_STARTTLS` | bool | `true` |  | `SZ_SMTP_SSL` ile birlikte açılamaz. |
| `SZ_SMTP_SSL` | bool | `false` |  | Örtük TLS (SMTPS). |
| `SZ_SMTP_ALLOW_PLAINTEXT` | bool | `false` |  | TLS'siz SMTP; yalnızca `development`. |

#### Slack

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_SLACK_BOT_TOKEN` | metin | — | evet | `xoxb-...`; doluysa bildirim Slack olabilir. |
| `SZ_SLACK_DEFAULT_CHANNEL` | metin | — |  | `default` kanal kimliği (`C...`). |
| `SZ_SLACK_ONCALL_CHANNEL` | metin | — |  | `oncall` kanal kimliği; nöbetçi uyarısını da alır. |
| `SZ_SLACK_WEBHOOK_URL` | metin | — | evet | Bot yoksa tek kanallı gelen webhook. |

#### ITSM köprüsü (ServiceNow / Jira SM)

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_SERVICENOW_INSTANCE_URL` | metin | — |  | Örn. `https://ornek.service-now.com`; üçü doluysa etkin. |
| `SZ_SERVICENOW_USERNAME` | metin | — |  | Table API kullanıcısı (Basic). |
| `SZ_SERVICENOW_PASSWORD` | metin | — | evet | Table API parolası. |
| `SZ_JIRA_BASE_URL` | metin | — |  | Örn. `https://ornek.atlassian.net`; adres + e-posta + belirteç doluysa etkin. |
| `SZ_JIRA_EMAIL` | metin | — |  | API kullanıcısının e-postası (Basic). |
| `SZ_JIRA_API_TOKEN` | metin | — | evet | Atlassian API belirteci. |
| `SZ_JIRA_SERVICE_DESK_ID` | metin | — |  | Talep açılacak hizmet masası. |
| `SZ_JIRA_REQUEST_TYPE_ID` | metin | — |  | Boşsa hizmet masasının ilk istek türü. |
| `SZ_SYNC_CONFLICT_POLICY` | `manual` \| `remote_wins` \| `local_wins` | `manual` |  | İki yönlü senkron çakışmasında davranış. |

#### Bildirim kuyruğu ve zamanlayıcı

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_ONCALL_WEBHOOK_URL` | metin | — | evet | Nöbetçi uyarısı webhook'u; her bildiriciden önceliklidir. |
| `SZ_NOTIFICATION_MAX_ATTEMPTS` | int (≥1, ≤50) | `5` |  | Bu kadar denemeden sonra satır `dead`. |
| `SZ_NOTIFICATION_BACKOFF_SECONDS` | sayı (>0) | `30.0` |  | Üstel geri çekilme tabanı (en fazla 1 sa). |
| `SZ_OUTBOX_RETENTION_DAYS` | int (≥1, ≤3650) | `30` |  | `sent` satırların saklama süresi. |
| `SZ_OUTBOX_DEAD_RETENTION_DAYS` | int (≥1, ≤3650) | `90` |  | `dead` satırların saklama süresi. |
| `SZ_SCHEDULER_ENABLED` | bool | `true` |  | Arka plan işleri (kuyruk, süre aşımları, SLA, CSI). |
| `SZ_SCHEDULER_TICK_SECONDS` | sayı (>0) | `15.0` |  | Zamanlayıcı tur aralığı. |

#### İzleme webhook'ları ve olay bağıntısı

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_EVENTS_WEBHOOK_SECRET` | metin | — | evet | Boşsa `/events/*` her isteğe 401 (kapalı). |
| `SZ_EVENTS_MAX_BODY_BYTES` | int (≥1024, ≤20971520) | `1048576` |  | Webhook gövde sınırı (413). |
| `SZ_EVENTS_REPLAY_WINDOW_SECONDS` | int (≥10, ≤86400) | `300` |  | İmza zaman damgası penceresi; tekrar kaydı 2 pencere. |
| `SZ_EVENTS_CI_MAPPING` | JSON dizi | 5 kural |  | Etiket → CI kuralları (JSON dizi); varsayılan `ci_id`, `ci`, `instance` (port atılır), `service`, `job`. |
| `SZ_EVENTS_CORRELATION_WINDOW_MINUTES` | int (≥1, ≤1440) | `30` |  | Bağıntı penceresi. |
| `SZ_EVENTS_FLAP_THRESHOLD` | int (≥2, ≤100) | `4` |  | Pencere içinde bu kadar ateşleme → bastırılır. |
| `SZ_EVENTS_FLAP_WINDOW_MINUTES` | int (≥1, ≤1440) | `60` |  | Çırpınma penceresi. |
| `SZ_EVENTS_INCIDENT_MIN_SEVERITY` | `critical` \| `high` \| `warning` \| `info` | `high` |  | Olay açan en düşük önem. |
| `SZ_EVENTS_MAX_INCIDENTS_PER_HOUR` | int (≥1, ≤10000) | `20` |  | Olay fırtınası sınırı (P1/P2 yine onaya gider). |

#### Sürekli iyileştirme (CSI)

| Değişken | Tür | Varsayılan | Gizli | Açıklama |
|---|---|---|---|---|
| `SZ_CSI_WINDOW_DAYS` | int (≥7, ≤180) | `30` |  | Sinyal penceresi (önceki eşit pencereyle karşılaştırılır). |
| `SZ_CSI_MEASURE_DAYS` | int (≥1, ≤366) | `30` |  | Uygulama sonrası ölçüm penceresi. |
| `SZ_CSI_COOLDOWN_DAYS` | int (≥0, ≤365) | `30` |  | Reddedilen/kapanan sinyalin yeniden önerilmeme süresi. |
<!-- SZ_SETTINGS:END -->

Portal (derleme argümanları `VITE_*` pakete gömülür, **gizli değildir**; `PORTAL_*` çalışma
zamanında CSP'ye yazılır):

| Değişken | Ne zaman | Açıklama |
|---|---|---|
| `VITE_API_BASE_URL` | derleme | API kökü (ör. `https://api.ornek.com`) |
| `VITE_AUTH_MODE` | derleme | `oidc` (üretim) \| `dev` |
| `VITE_OIDC_AUTHORITY` | derleme | Keycloak: `https://sso.ornek.com/realms/serviszeka`; Entra: `https://login.microsoftonline.com/<tenant>/v2.0` |
| `VITE_OIDC_CLIENT_ID` | derleme | Portalın public istemci kimliği |
| `VITE_OIDC_SCOPE` | derleme | Varsayılan `openid profile email`; Entra'da API kapsamı eklenir (§5.2) |
| `VITE_TIME_ZONE` | derleme | Varsayılan `Europe/Istanbul` |
| `PORTAL_API_ORIGIN` | çalışma | CSP `connect-src` (API kökeni) |
| `PORTAL_OIDC_ORIGIN` | çalışma | CSP `connect-src` / `frame-src` / `form-action` (IdP kökeni) |

## 5. OIDC kurulumu

ServisZeka belirteci şöyle doğrular (`auth.py`): imza JWKS ile (yalnızca RS256/ES256), `iss` =
`SZ_AUTH_ISSUER`, `aud` ∋ `SZ_AUTH_AUDIENCE`, `exp` zorunlu. Kullanıcı adı
`SZ_AUTH_USERNAME_CLAIM` → `upn` → `email` → `sub`. Roller `SZ_AUTH_ROLES_CLAIM` (noktalı yol)
boşsa `realm_access.roles` ∪ `resource_access.<SZ_AUTH_AUDIENCE>.roles` ∪ `roles`.

| Rol | Kime |
|---|---|
| `requester` | Son kullanıcılar (self-servis) |
| `agent` | Servis masası / destek uzmanları |
| `approver` | Yapay zekâ kararlarını ve eylem isteklerini onaylayanlar |
| `change_manager` | Değişiklik yöneticileri (CMDB yazma, ECAB) |
| `cab` | CAB üyeleri |
| `admin` | Platform yöneticileri (denetim, entegrasyon, katalog tohumu); her rolü kapsar |
| `ai_agent` | **Yalnızca** yapay zekâ hizmet sorumluları (client credentials). Belirteç `ai:<azp\|appid\|client_id>` aktörüne ve yalnızca `ai_agent` rolüne indirgenir; insana bu rolü, ajana insan rolü vermeyin |

**Önerilen rol talebi:** rolleri realm/dizin genelinde değil, ServisZeka API uygulamasına özgü
tanımlayın ve yalnızca o yolu okuyun — Keycloak'ta `SZ_AUTH_ROLES_CLAIM=resource_access.<api-istemcisi>.roles`
(istemci kimliğinde `.` olmamalı: yol noktadan bölünür), Entra'da `SZ_AUTH_ROLES_CLAIM=roles`.
Böylece aynı realm'deki başka bir uygulamanın `admin` rolü ServisZeka'da yetki vermez.

### 5.1 Keycloak

1. **API istemcisi** `serviszeka-api` (OpenID Connect, *Client authentication* açık, tüm akışlar
   kapalı — yalnızca rol ve hedef kitle taşıyıcısı). *Roles* sekmesinde istemci rolleri:
   `requester`, `agent`, `approver`, `change_manager`, `cab`, `admin`, `ai_agent`.
2. **Portal istemcisi** `serviszeka-portal`: *Client authentication* kapalı (public),
   *Standard flow* açık, *Direct access grants* kapalı, *Advanced → PKCE Method* `S256`.
   - Valid redirect URIs: `https://portal.ornek.com/auth/callback`,
     `https://portal.ornek.com/auth/silent`
   - Valid post logout redirect URIs: `https://portal.ornek.com/giris`
   - Web origins: `https://portal.ornek.com`
   - *Client scopes → dedicated scope → Add mapper → Audience*: Included Client Audience
     `serviszeka-api`, *Add to access token* açık.
   - *Client scopes → dedicated scope → Scope*: `serviszeka-api` istemci rollerini kapsama alın
     (ya da *Full scope allowed*), böylece roller `resource_access.serviszeka-api.roles`'ta gelir.
3. Kullanıcılara/gruplara `serviszeka-api` istemci rollerini atayın.
4. **Yapay zekâ ajanı** (her MCP istemcisi için ayrı): gizli istemci `servis-ajani`,
   *Client authentication* açık, yalnızca *Service accounts roles* açık. *Service account roles*
   sekmesinde yalnızca `serviszeka-api` → `ai_agent`. Aynı Audience mapper'ı ekleyin. Belirteçte
   `azp=servis-ajani` → aktör `ai:servis-ajani`.
5. API ayarları: `SZ_AUTH_ISSUER=https://sso.ornek.com/realms/serviszeka`,
   `SZ_AUTH_AUDIENCE=serviszeka-api`, `SZ_AUTH_ROLES_CLAIM=resource_access.serviszeka-api.roles`.
   Keycloak < 17 için yayıncı yolu `/auth/realms/...` içerir.
6. Portal: `VITE_OIDC_AUTHORITY=https://sso.ornek.com/realms/serviszeka`,
   `VITE_OIDC_CLIENT_ID=serviszeka-portal`, `PORTAL_OIDC_ORIGIN=https://sso.ornek.com`.

### 5.2 Microsoft Entra ID

1. **API uygulama kaydı** "ServisZeka API":
   - *Expose an API*: Application ID URI `api://<api-uygulama-kimliği>`, kapsam
     `access_as_user` (yönetici ve kullanıcı onayı).
   - *Manifest*: `"requestedAccessTokenVersion": 2` (eski adıyla `accessTokenAcceptedVersion`) —
     v2 belirteçte `iss` = `https://login.microsoftonline.com/<tenant>/v2.0`, `aud` = API uygulama
     (istemci) kimliği olur.
   - *App roles*: `requester`, `agent`, `approver`, `change_manager`, `cab`, `admin`
     (*Allowed member types: Users/Groups*) ve `ai_agent` (*Applications*).
   - *Enterprise applications → ServisZeka API → Properties*: "Assignment required" açık;
     *Users and groups* ile rolleri atayın.
2. **Portal uygulama kaydı** "ServisZeka Portal": platform **Single-page application**, yönlendirme
   adresleri `https://portal.ornek.com/auth/callback` ve `https://portal.ornek.com/auth/silent`,
   çıkış adresi `https://portal.ornek.com/giris`. *API permissions*: ServisZeka API →
   `access_as_user` (delegated) + yönetici onayı. İstemci gizlisi yok.
3. **Yapay zekâ ajanı** uygulama kaydı (her MCP istemcisi için ayrı): istemci gizlisi ya da
   sertifika; *API permissions* → ServisZeka API → *Application permissions* → `ai_agent`,
   yönetici onayı. Belirteç `scope=api://<api-uygulama-kimliği>/.default` ile alınır; belirteçte
   `roles=["ai_agent"]`, `azp` (v1: `appid`) → aktör `ai:<ajan-uygulama-kimliği>`.
4. API ayarları: `SZ_AUTH_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0`,
   `SZ_AUTH_AUDIENCE=<api-uygulama-kimliği>`, `SZ_AUTH_ROLES_CLAIM=roles`,
   `SZ_AUTH_USERNAME_CLAIM=preferred_username` (aktör ve onaylayan adı; ad değişirse geçmiş
   kayıtlar eski adla kalır).
5. Portal: `VITE_OIDC_AUTHORITY=https://login.microsoftonline.com/<tenant>/v2.0`,
   `VITE_OIDC_CLIENT_ID=<portal-uygulama-kimliği>`,
   `VITE_OIDC_SCOPE="openid profile email api://<api-uygulama-kimliği>/access_as_user"`,
   `PORTAL_OIDC_ORIGIN=https://login.microsoftonline.com`.

**OIDC — doğrulanmalı:** (O1) Keycloak'ta istemci rollerinin portal belirtecinde
`resource_access.serviszeka-api.roles` altında geldiği (kapsam eşlemesi); (O2) Entra v2 erişim
belirtecinde `preferred_username` ve `azp` taleplerinin bulunduğu; (O3) sessiz yenilemenin
(`/auth/silent` iframe'i) üçüncü taraf çerez kısıtlı tarayıcılarda çalıştığı — çalışmazsa oturum
süresi dolunca yeniden giriş gerekir; (O4) IdP'nin sessiz yenileme yanıtını portal kökeninden
çerçevelemeye izin verdiği.

## 6. Bağlayıcılar

Seçim: `SZ_IDENTITY_PROVIDER` (`graph` | `keycloak`) + `SZ_NOTIFIER_PROVIDER` (`teams` | `slack` |
`log`); `SZ_CONNECTOR_PROVIDER=none` bırakın. Ayrıntılı davranış [`faz3.md`](faz3.md) "Bağlayıcılar" ve "Sağlayıcı seçimi".
Katalog öğelerindeki grup/SKU/posta kutusu kimlikleri sahte dizine göredir: kurulumdan sonra
`PATCH /catalog/items/{id}` ile kiracınızın kimliklerine çevirin ve standart grupları
`SZ_IDENTITY_STANDARD_GROUPS`'a (Keycloak'ta grup **kimliği**) yazın.

### 6.1 Microsoft Graph (Entra ID + M365 + Teams)

Ayarlar: `SZ_GRAPH_TENANT_ID`, `SZ_GRAPH_CLIENT_ID`, `SZ_GRAPH_CLIENT_SECRET`; Teams bildirimi
için `SZ_GRAPH_SENDER_MAILBOX`, kanal webhook'ları `SZ_TEAMS_WEBHOOK_URLS`.

En az izinler (uygulama izni, yönetici onayı; **doğrulanmalı**): `User.Read.All`,
`GroupMember.Read.All`, `GroupMember.ReadWrite.All`, `User.RevokeSessions.All`,
`User.ReadWrite.All` (hesap etkin/devre dışı), `UserAuthenticationMethod.ReadWrite.All`,
`Organization.Read.All`, `LicenseAssignment.ReadWrite.All`, `Mail.Send` — bir **uygulama erişim
ilkesiyle** yalnızca `SZ_GRAPH_SENDER_MAILBOX`'a sınırlayın. Ayrıcalıklı kullanıcılarda parola
sıfırlama/devre dışı bırakma ek dizin rolü isteyebilir.

Desteklenmeyen (açık hata, uydurulmaz): bulutta hesap kilidi açma (`account_unlock` → `failed`),
paylaşılan posta kutusu izni (Exchange Online gerekir). Doğrulanmalı listesi:
[`faz3.md`](faz3.md) "Doğrulanmalı" 1-7.

### 6.2 Keycloak (kimlik)

Ayarlar: `SZ_KEYCLOAK_SERVER_URL`, `SZ_KEYCLOAK_REALM`, `SZ_KEYCLOAK_CLIENT_ID`,
`SZ_KEYCLOAK_CLIENT_SECRET`; öznitelikler `SZ_KEYCLOAK_MANAGER_ATTRIBUTE`,
`SZ_KEYCLOAK_SECONDARY_EMAIL_ATTRIBUTE`.

Hizmet hesabı etkin gizli istemci; *Service account roles* → `realm-management`: `view-users`,
`query-groups`, `manage-users` (**doğrulanmalı K1**). Keycloak 24+ kullanıcı profilinde yönetici
ve ikincil e-posta özniteliklerini tanımlayın; ikincil e-posta **yalnızca yöneticilerce
düzenlenebilir** olmalı (K11; aksi halde ele geçirilmiş hesap geçici parolayı kendi adresine
yönlendirir). Keycloak e-posta göndermez: geçici parola SMTP ile ikincil adrese gider (§6.4);
SMTP yoksa `identity.reset_password` başarısız olur (açılışta uyarı). Posta/lisans bağlayıcısı
yoktur: `m365_license` ve `shared_mailbox_access` öğelerini devre dışı bırakın. Doğrulanmalı:
[`faz3.md`](faz3.md) K1-K11.

### 6.3 Slack (bildirim)

`SZ_NOTIFIER_PROVIDER=slack`, `SZ_SLACK_BOT_TOKEN` (`xoxb-…`), `SZ_SLACK_DEFAULT_CHANNEL`,
`SZ_SLACK_ONCALL_CHANNEL` (kanal **kimlikleri**), isteğe bağlı `SZ_SLACK_WEBHOOK_URL`. Bot
kapsamları: `chat:write`, `im:write`, `users:read`, `users:read.email`; nöbetçi tekilliği için
`channels:history` (genel) / `groups:history` (özel kanal). Botu iki kanala da davet edin. Geçici
parola Slack'e asla gitmez. Doğrulanmalı: [`faz3.md`](faz3.md) "Slack — doğrulanmalı" 8-13.

### 6.4 SMTP

`SZ_SMTP_HOST`, `SZ_SMTP_SENDER`, isteğe bağlı `SZ_SMTP_USERNAME`/`SZ_SMTP_PASSWORD`. 587 +
`SZ_SMTP_STARTTLS=true` ya da 465 + `SZ_SMTP_SSL=true` (ikisi birlikte değil). TLS'siz SMTP
üretimde açılmaz. Gönderen hesaba yalnızca gönderim yetkisi verin. **Doğrulanmalı:** kurumsal
röle ile STARTTLS sertifika doğrulaması ve ikincil adrese teslim.

### 6.5 ServiceNow ve Jira Service Management (ITSM köprüsü)

Senkron otomatik değildir: `admin` rolüyle `POST /integrations/{system}/pull`,
`POST /integrations/push/{incident_id}`, `POST /integrations/servicenow/cmdb/pull`; çakışmalar
`GET /integrations/conflicts` (approver). Kimlik doğrulama Basic.

| Sistem | Ayarlar | Kullanılan uçlar | Önerilen en az yetki (**doğrulanmalı**) |
|---|---|---|---|
| ServiceNow | `SZ_SERVICENOW_INSTANCE_URL`, `_USERNAME`, `_PASSWORD` | `GET/POST/PATCH /api/now/table/incident`; `GET /api/now/table/cmdb_ci`, `cmdb_rel_ci` | Yalnızca web servis erişimli entegrasyon kullanıcısı; `itil` (incident okuma/yazma) + `cmdb_read`; REST API ACL'si açıksa `snc_platform_rest_api_access` |
| Jira SM (Cloud) | `SZ_JIRA_BASE_URL`, `SZ_JIRA_EMAIL`, `SZ_JIRA_API_TOKEN`, `SZ_JIRA_SERVICE_DESK_ID`, `SZ_JIRA_REQUEST_TYPE_ID` | `/rest/servicedeskapi/request`, `/rest/servicedeskapi/servicedesk/{id}(/requesttype)`, `/rest/api/3/issue/{key}(/transitions)`, `POST /rest/api/3/search/jql` | Hizmet masası projesinde ajan lisanslı hizmet hesabı: *Browse projects*, *Create issues*, *Edit issues*, *Transition issues* |

Jira Assets (CMDB) yalnızca arayüz taslağıdır (çağrı `NotImplementedError`).

### 6.6 Alertmanager / izleme webhook'u

`SZ_EVENTS_WEBHOOK_SECRET` en az 32 rastgele bayt olsun (`openssl rand -hex 32`); boşsa uçlar 401.

```yaml
# alertmanager.yml
receivers:
  - name: serviszeka
    webhook_configs:
      - url: https://api.ornek.com/events/alertmanager
        send_resolved: true          # çözülen uyarı olaya not + çözüm önerisi düşer
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/serviszeka-token
```

Diğer kaynaklar `POST /events/generic` ve tercihen HMAC kullanır:

```bash
ts=$(date +%s)
sig=$(printf '%s.%s' "$ts" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -X POST https://api.ornek.com/events/generic -H 'Content-Type: application/json' \
  -H "X-ServisZeka-Timestamp: $ts" -H "X-ServisZeka-Signature: sha256=$sig" --data "$BODY"
```

Etiket → CI eşlemesi `SZ_EVENTS_CI_MAPPING`; varsayılan `ci_id`, `ci`, `instance` (port atılır),
`service`, `job` etiketlerini CI adına eşler. Gövde sınırı `SZ_EVENTS_MAX_BODY_BYTES` (1 MiB):
ters vekil sınırı en az bu kadar olmalı.

## 7. MCP istemcisi

Uç nokta `POST https://api.ornek.com/mcp` (streamable HTTP, durumsuz, JSON yanıt). Belirteç
ajan istemcisinin client credentials akışıyla alınır:

```bash
# Keycloak
curl -s -d grant_type=client_credentials -d client_id=servis-ajani -d client_secret="$SECRET" \
  https://sso.ornek.com/realms/serviszeka/protocol/openid-connect/token | jq -r .access_token
# Entra ID
curl -s -d grant_type=client_credentials -d client_id="$AJAN_ID" -d client_secret="$SECRET" \
  -d scope="api://$API_ID/.default" \
  https://login.microsoftonline.com/$TENANT/oauth2/v2.0/token | jq -r .access_token
```

```json
{
  "mcpServers": {
    "serviszeka": {
      "type": "http",
      "url": "https://api.ornek.com/mcp",
      "headers": { "Authorization": "Bearer ${SERVISZEKA_AI_TOKEN}" }
    }
  }
}
```

- Erişim belirteci kısa ömürlüdür (IdP ayarı); süresi dolunca 401 alınır, istemciyi yenilenen
  belirteçle yeniden başlatın.
- `tools/list` yalnızca `ai_agent` için açık araçları, açıklamanın başında risk sınıfıyla verir.
  R2/R3 çağrıları `{"status": "pending_approval", "action_request_id": …}` döner; onay portalda
  **Onaylar → Eylem onayları**. Kayıt değiştiren araçlarda `expected_version` zorunludur.
- Her ajan için ayrı istemci açın: denetim kaydındaki aktör (`ai:<istemci>`) ajanı ayırt eder ve
  tek ajanın erişimi IdP'de kapatılabilir.

## 8. TLS, CSP ve ters vekil

- API ve portal TLS'i ters vekilde sonlandırır; uygulama `:8000`, portal `:8080` yalnızca iç
  ağdan erişilebilir olmalı. Portal imajı HSTS, CSP, `X-Frame-Options: DENY`,
  `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP/CORP başlıklarını
  kendisi ekler; vekil bunları ezmemeli.
- Portal CSP'si: `script-src 'self'`, `style-src 'self'` (`'unsafe-inline'` yok);
  `connect-src` yalnızca `'self' PORTAL_API_ORIGIN PORTAL_OIDC_ORIGIN`; yalnızca `/auth/silent`
  aynı kökenden çerçevelenebilir. API kökeni ya da IdP değişince `PORTAL_*_ORIGIN` güncellenmeli.
- `SZ_CORS_ORIGINS` yalnızca portal kökenini içersin (çerez yok, `allow_credentials` kapalı).
- API önündeki vekil için örnek (nginx):

  ```nginx
  server {
    listen 443 ssl;
    server_name api.ornek.com;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 1m;                  # >= SZ_EVENTS_MAX_BODY_BYTES
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    location = /docs         { return 404; }  # isteğe bağlı: belgeleri dışarıya kapatın
    location = /openapi.json { allow 10.0.0.0/8; deny all; proxy_pass http://serviszeka_api; }
    # http bloğunda: limit_req_zone $binary_remote_addr zone=webhook:10m rate=10r/s;
    location /events/        { limit_req zone=webhook burst=20; proxy_pass http://serviszeka_api; }
    location /               { proxy_pass http://serviszeka_api; proxy_read_timeout 120s; }
  }
  ```

  Uygulamada hız sınırlama yoktur; `/events/`, `/mcp` ve LLM tetikleyen uçlar için vekilde sınır
  koyun. LLM çağıran uçlar (triage, risk, RCA) birkaç saniye sürebilir.

## 9. Yedekleme ve geri yükleme

Tüm kalıcı durum PostgreSQL'dedir (API ve portal durumsuzdur; ayarlar ortamdadır).

```bash
pg_dump -Fc -d "$PG_URL" -f serviszeka-$(date +%F).dump            # günlük + işlem öncesi
pg_restore --clean --if-exists --no-owner -d "$PG_URL_HEDEF" serviszeka-YYYY-MM-DD.dump
```

Geri yükleme sonrası:

1. Tüm API kopyaları durdurulmuşken `DELETE FROM scheduler_leases;` (yedekteki kiralar ileri tarihli
   olabilir; satırlar ilk turda yeniden kurulur).
2. `alembic current` = yedeğin göç sürümü; daha yeni bir imajla açacaksanız önce
   `alembic upgrade head`.
3. API'yi başlatın, admin olarak `GET /audit/verify` → `{"valid": true}`.
4. Kuyrukta `pending`/`failed` satırlar varsa yeniden gönderilir (en az bir kez; alıcıya
   `Idempotency-Key` gider).
5. Bekleyen eylem istekleri süresine göre `expired` olur; süresi geçmemiş ama yedekten dönen
   istekleri gözden geçirin.

Yedekler kişisel veri ve denetim zinciri içerir: şifreli saklayın, erişimi sınırlayın; saklama
süresi KVKK politikanıza uymalı (ISLETIM.md §7).

## 10. Sürüm yükseltme

1. Sürüm notlarını ve yeni `SZ_` ayarlarını okuyun (`uv run python docs/_gen_settings.py` çıktısını
   önceki sürümle karşılaştırın; açılışta zorunlu hale gelen ayar varsa ekleyin).
2. Üretimin kopyasında prova: `alembic upgrade head`, `alembic check`, duman testi
   (`/health`, giriş, `GET /audit/verify`).
3. Yedek alın (§9).
4. API kopyalarını durdurun (zamanlayıcı ve kuyruk gönderimi durur; kuyruk kalıcıdır).
5. Göçü tek bir iş olarak çalıştırın: `alembic upgrade head`.
6. Yeni API imajıyla kopyaları, ardından yeni portal imajını başlatın (portal `index.html`
   önbelleklenmez; yeni paket hemen yüklenir).
7. Doğrulama: `/health` sürümü, `scheduler_leases.last_status='ok'` (ISLETIM.md §2),
   `GET /audit/verify`, bir triage ve bir eylem onayı.

Geri dönüş: her göçün `downgrade`'i vardır (`alembic downgrade <önceki>`), ancak yeni sütun/tablo
verisi kaybolur; tercih edilen yol §9'daki yedekten dönüş + eski imajdır.

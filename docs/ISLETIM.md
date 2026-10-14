# ServisZeka — işletim el kitabı

Kurulum [`KURULUM.md`](KURULUM.md), güvenlik [`GUVENLIK.md`](GUVENLIK.md). Bu belgedeki SQL'ler
PostgreSQL içindir. Enum sütunları VARCHAR olarak **büyük harfli üye adıyla** saklanır
(`'DEAD'`, `'PENDING'`, …); API ve JSON küçük harfli değerleri gösterir.

Uygulamanın yönetim CLI'ı yoktur; aşağıdaki Python parçaları API imajında
(`docker run --rm --env-file serviszeka.env serviszeka-api python - <<'EOF' … EOF`) ya da kaynakta
`uv run python` ile, üretim ortam değişkenleriyle çalıştırılır.

## 1. Sağlık kontrolleri

| Kontrol | Nasıl | Sağlıklı |
|---|---|---|
| API süreci | `GET /health` (kimliksiz; imaj `HEALTHCHECK`'i) | `{"status":"ok","version":…}` — **veritabanını denetlemez** |
| Portal | `GET /healthz` (nginx) | `ok` |
| Veritabanı | `pg_isready`; `SELECT 1` | — |
| Kimlik doğrulama | Geçerli bir belirteçle `GET /me` | 200; 401 hepsinde → §6.2 |
| Zamanlayıcı | §2 sorgusu | Her işin `last_status='ok'`, `last_finished_at` aralığın ~2 katından yeni |
| Bildirim kuyruğu | `SELECT state, count(*) FROM notification_outbox GROUP BY state;` | `DEAD` artmıyor; `PENDING`/`FAILED` birikmiyor |
| Eylem istekleri | `GET /actions?state=pending` (approver) | Bekleyenler TTL (`SZ_ACTION_REQUEST_TTL_HOURS`) içinde karara bağlanıyor |
| Denetim zinciri | `GET /audit/verify` (admin) ya da portal **Yönetim → Denetim** | `{"valid": true}` — §4 |
| Entegrasyonlar | `GET /integrations/status` (admin) | Yapılandırılan sistemler `configured` |
| İzleme webhook'u | Alertmanager'da webhook hata sayaçları; `GET /events/groups?state=…` | 401/413/422 yok |

Tüm zinciri tek istekte hesaplayan `GET /audit/verify` büyük tablolarda yavaştır; günlük çalıştırın,
sağlık yoklamasına koymayın.

## 2. Zamanlayıcı ve kiralar

İşler (`scheduler.py`, `SZ_SCHEDULER_ENABLED=true`, tur `SZ_SCHEDULER_TICK_SECONDS`=15 sn):

| İş | Aralık | Zaman aşımı | Ne yapar |
|---|---|---|---|
| `outbox_dispatch` | 30 sn | 300 sn | zamanı gelmiş kuyruk satırlarını gönderir |
| `outbox_retention` | 1 sa | 300 sn | süresi dolan `SENT`/`DEAD` satırları siler (açık olayın `sla:*` satırları kalır) |
| `action_expiry` | 60 sn | 300 sn | süresi dolan eylem isteklerini `expired` yapar, bağlı talebi `failed` |
| `sla_check` | 60 sn | 300 sn | açık P1/P2'de SLA riski/ihlali için nöbetçi bildirimi (olay + durum başına bir kez) |
| `request_expiry` | 5 dk | 300 sn | iş onayı süresi dolan talebi reddeder, kancası kaçmış talebi ilerletir |
| `csi_review` | 24 sa | 600 sn | CSI ajanı: sinyallerden fikir, ölçüm penceresi dolanı ölçer |

Kira: iş başında `UPDATE scheduler_leases … WHERE job=:j AND lease_until <= now` (tek kopya
kazanır), süre `max(aralık, zaman aşımı + 60 sn)`; iş bitince `lease_until = başlangıç + aralık`.

```sql
SELECT job, owner, lease_until, last_started_at, last_finished_at, last_status, last_error, runs
FROM scheduler_leases ORDER BY job;
```

| Belirti | Olası neden | Yapılacak |
|---|---|---|
| Tablo boş / hiç satır yok | `SZ_SCHEDULER_ENABLED=false` ya da API başlamadı | Günlükte `Zamanlayıcı başladı (<owner>)` satırını arayın |
| `last_status='error'` | İş hatası (`last_error` gizli bilgisizdir) | Günlükte `Zamanlayıcı işi başarısız: <iş>` yığın izi; hata zamanlayıcıyı durdurmaz, iş bir sonraki aralıkta yeniden denenir |
| `lease_until` gelecekte, `owner` artık yok | Kopya iş sırasında öldü | Kira süresi dolunca (en fazla `zaman aşımı + 60 sn`, `csi_review` için 24 sa) başka kopya alır. Beklenemiyorsa: `UPDATE scheduler_leases SET lease_until = now() WHERE job = '<iş>';` |
| Aynı iş iki kopyada | Kopyaların saati farklı | NTP'yi düzeltin; kira saat karşılaştırmasıdır |
| Eylem istekleri süresi dolduğu hâlde `pending` | `action_expiry` çalışmıyor | `GET /actions` çağrısı da süre aşımını işler; zamanlayıcıyı düzeltin |

İşi elle bir kez çalıştırmak (kirayı yine alır; başka kopyada çalışıyorsa `skipped` döner):

```python
import asyncio
from serviszeka.scheduler import Scheduler
print(asyncio.run(Scheduler().run_job("outbox_dispatch")))   # JobRun(job, status, result, error)
```

CSI incelemesi ayrıca `POST /improvements/review` (agent/approver/admin) ile tetiklenebilir.

## 3. Bildirim kuyruğu ve ölü satırlar (dead-letter)

`notification_outbox` durumları: `PENDING` → `SENDING` (kiralandı, 120 sn) → `SENT`; hata →
`FAILED` (`SZ_NOTIFICATION_BACKOFF_SECONDS × 2^(n-1)`, en fazla 1 sa sonra yeniden) →
`SZ_NOTIFICATION_MAX_ATTEMPTS` (5) denemeden sonra `DEAD`. Her deneme denetime
`notification.sent|failed|dead` olarak yazılır. Teslimat **en az bir kez**dir; alıcıya
`Idempotency-Key` başlığı gider (Slack bot kipi yeniden denemede kanal geçmişinde anahtarı arar).

```sql
SELECT id, event, entity_type, entity_id, attempts, last_error, updated_at
FROM notification_outbox WHERE state = 'DEAD' ORDER BY updated_at DESC LIMIT 50;
```

1. `last_error`'a bakın (URL'ler `[URL]`, gizliler `***` olarak ayıklanmıştır). Tipik nedenler:
   yanlış/iptal edilmiş `SZ_ONCALL_WEBHOOK_URL`, Slack `missing_scope` / `not_in_channel`, alıcı
   5xx.
2. Nedeni düzeltin (ayar değiştiyse API'yi yeniden başlatın).
3. Satırları **bir deneme** için kuyruğa geri alın (son deneme yeniden "teslim edilmiş olabilir"
   kabul edilir, Slack botu yinelemeyi denetler; yine başarısız olursa satır yeniden `DEAD` olur):

   ```sql
   UPDATE notification_outbox
   SET state = 'PENDING', attempts = attempts - 1, next_attempt_at = now()
   WHERE state = 'DEAD' AND id IN ('<id>', '<id>');
   ```

4. Bir sonraki `outbox_dispatch` turunda (≤ 30 sn) gönderilir; denetimde `notification.sent`.

Silmeyin: `sla:<olay>:<durum>` anahtarlı satırlar SLA uyarısının tekilliğini sağlar; açık olayın
satırı silinirse aynı ihlal yeniden bildirilir. `DEAD` satırlar `SZ_OUTBOX_DEAD_RETENTION_DAYS`
(90) gün sonra zamanlayıcıyla silinir; denetim kayıtları kalır.

## 4. Denetim zinciri

Her kayıt önceki kaydın SHA-256 özetini içerir (`audit_log.prev_hash` → `hash`); PostgreSQL'de
yazıcılar danışma kilidiyle sıraya girer. Doğrulama: `GET /audit/verify` (admin) ya da portal.
Yapay zekâ `audit.verify_chain` aracını çağıramaz (yalnızca admin).

Günlük iş olarak (önerilir) son halkayı uygulama dışı, değiştirilemez bir yere yazın — zincirin
**sonunun silinmesini** ya da tümüyle yeniden hesaplanmasını yalnızca bu dış kopya ortaya çıkarır:

```sql
SELECT seq, occurred_at, hash FROM audit_log ORDER BY seq DESC LIMIT 1;
```

**`valid: false` olursa** (güvenlik olayı olarak ele alın):

1. Hemen yedek alın (`pg_dump -Fc`), mevcut durumu kanıt olarak saklayın. Zinciri "onarmayın" —
   özetleri yeniden hesaplamak kanıtı yok eder.
2. İlk bozuk kaydı bulun:

   ```python
   import asyncio
   from sqlalchemy import select
   from serviszeka import audit, db
   from serviszeka.models import AuditLog

   async def main():
       prev = audit.GENESIS_HASH
       async with db.get_sessionmaker()() as s:
           for r in await s.scalars(select(AuditLog).order_by(AuditLog.seq)):
               calc = audit.compute_hash(
                   prev_hash=r.prev_hash, occurred_at=r.occurred_at, actor=r.actor,
                   action=r.action, entity_type=r.entity_type, entity_id=r.entity_id,
                   details=r.details)
               if r.prev_hash != prev or calc != r.hash:
                   print("ilk bozuk kayıt:", r.seq, r.occurred_at, r.actor, r.action)
                   return
               prev = r.hash
       print("zincir sağlam; son özet", prev)

   asyncio.run(main())
   ```

3. `prev_hash` uyuşmuyorsa önceki kayıt silinmiş/eklenmiş; `hash` uyuşmuyorsa kayıt değiştirilmiş.
   Aynı `seq`'i son sağlam yedekle ve dış kopyayla karşılaştırın.
4. Veritabanı erişim günlüklerini (kim, ne zaman doğrudan yazdı) inceleyin; uygulama denetim
   kaydını yalnızca ekler, güncellemez/silmez.
5. Kayıt dışı değişikliği olay raporuyla belgeleyin; KVKK kapsamında kişisel veri etkilendiyse
   bildirim yükümlülüklerini değerlendirin. Zincir bu noktadan sonra da bozuk görünmeye devam eder;
   düzeltme kararı (yedekten dönüş vb.) yönetimce verilir.

## 5. Gizli bilgi döndürme

Ayarlar süreç başında okunur: her değişiklikten sonra kopyaları **sırayla yeniden başlatın**.

| Gizli | Yöntem | Not |
|---|---|---|
| IdP imza anahtarları | IdP'de yeni anahtarı yayımlayın, sonra onunla imzalayın | Bilinmeyen `kid` JWKS'yi en fazla 30 sn'de bir yeniler; önbellek 10 dk. Yeniden başlatma gerekmez |
| Yapay zekâ ajanı istemci gizlisi | Yalnızca IdP'de | ServisZeka'da ayar yok; eski belirteçler süreleri dolana dek geçerlidir |
| `SZ_GRAPH_CLIENT_SECRET` | Entra'da ikinci gizli ekle → dağıt → eskisini sil | Kesintisiz |
| `SZ_KEYCLOAK_CLIENT_SECRET` | *Regenerate* eskisini hemen geçersiz kılar → yeni değeri dağıtıp hemen yeniden başlatın | Arada kimlik araçları başarısız; etkilenen talepler `POST /requests/{id}/retry` |
| `SZ_EVENTS_WEBHOOK_SECRET` | Tek değerdir: göndericiyi ve API'yi aynı bakım penceresinde değiştirin | Arada istekler 401; gönderenin yeniden deneme davranışını doğrulayın |
| `SZ_SLACK_BOT_TOKEN` / `SZ_SLACK_WEBHOOK_URL` | Slack'te yeni belirteç/webhook → dağıt → eskisini iptal | — |
| `SZ_SMTP_PASSWORD`, `SZ_SERVICENOW_PASSWORD`, `SZ_JIRA_API_TOKEN`, `SZ_ANTHROPIC_API_KEY` | Yeni değeri oluştur → dağıt → eskisini iptal | — |
| `SZ_ONCALL_WEBHOOK_URL`, `SZ_TEAMS_WEBHOOK_URLS` | URL'nin kendisi gizlidir: yeni URL → dağıt → eskisini sil | Arada `FAILED` satırlar geri çekilmeyle yeniden denenir |
| Veritabanı parolası | `ALTER ROLE serviszeka PASSWORD …` ile `SZ_DATABASE_URL`'yi aynı pencerede değiştirin | Açık bağlantılar sürer, yenileri yeni parolayı ister |

Döndürmeden sonra: `GET /integrations/status`, kuyrukta yeni `FAILED`/`DEAD` yok, bir
bağlayıcı aracını `dry_run` ile deneyin.

## 6. Platform olay oyun kitapları

### 6.1 API açılmıyor
Günlükteki ilk hata ayar adını verir: `AuthConfigError` (OIDC ayarı eksik ya da dev kipi
geliştirme dışı), `ConnectorConfigError` (bağlayıcı ayarı eksik/çelişkili, SMTP TLS), sahte LLM
geliştirme dışı, `alembic upgrade head` hatası (göç). Değeri düzeltip yeniden başlatın.

### 6.2 Tüm istekler 401 / 503
- 503 "Kimlik doğrulama yapılandırılmamış": `SZ_AUTH_ISSUER`/`SZ_AUTH_AUDIENCE` boş.
- 401 herkese: `iss` ayarla birebir aynı değil (sonda `/`, Entra v1/v2), `aud` uyuşmuyor, JWKS'ye
  erişilemiyor (`JWKS alınamadı` günlüğü; başarısız çekmeden sonra 5 sn bekler), saat kayması >
  `SZ_AUTH_LEEWAY_SECONDS`. Reddetme nedeni INFO düzeyinde `Kimlik doğrulama reddedildi: …`.
- 403: rol talebi okunmuyor → `SZ_AUTH_ROLES_CLAIM` yolunu belirteçle karşılaştırın (`GET /me`
  rolleri gösterir).

### 6.3 LLM kesintisi
Anahtar yoksa LLM gerektiren uçlar 503 döner; çağrı hatası ya da şemaya uymayan yanıtta triage
kararı `escalated` olur ve insana düşer, kayıt değişmez. CSI gerekçesi deterministik metne düşer.
Kalıcı kesintide servis masası elle sınıflandırmaya devam eder; gerekirse `SZ_AI_SHADOW_MODE=true`.

### 6.4 Yapay zekâ beklenmedik davranıyor (acil durdurma)
1. `SZ_AI_SHADOW_MODE=true` + yeniden başlatma: ajanlar ve MCP R1 çağrıları artık uygulamaz,
   eylem isteği açar (R2/R3 zaten her zaman onay ister).
2. İlgili ajan istemcisini IdP'de devre dışı bırakın / gizlisini iptal edin (verilmiş belirteçler
   `exp`'e kadar geçerlidir; gerekirse vekilde `/mcp`'yi kapatın).
3. Bekleyen istekleri reddedin: `GET /actions?state=pending` → `POST /actions/{id}/reject`.
4. Neler yapıldı: `SELECT occurred_at, actor, action, entity_type, entity_id FROM audit_log
   WHERE actor LIKE 'ai:%' AND occurred_at > now() - interval '24 hours' ORDER BY seq;`
   (`tool.execute`, `ai_decision.tool`, `action_request.*`).

### 6.5 Uyarı fırtınası / webhook seli
Olay ajanı saatte `SZ_EVENTS_MAX_INCIDENTS_PER_HOUR` (20) üstünde P3-P5 olay açmaz (P1/P2 onaya
gider); çırpınan uyarılar bastırılır. Gürültülü grubu portalda **Sustur** (R2, onaylı). Kaynağı
kesmek için vekilde `/events/` yolunu kapatın ya da `SZ_EVENTS_WEBHOOK_SECRET`'ı boşaltıp yeniden
başlatın (tüm webhook'lar 401).

### 6.6 Bildirimler gitmiyor
§3 sorgusu; `PENDING` birikiyorsa zamanlayıcı (§2), `FAILED`/`DEAD` artıyorsa alıcı. Nöbetçi
bildirici seçimi: `SZ_ONCALL_WEBHOOK_URL` > (Slack botu + nöbet kanalı | Slack webhook'u) > yalnızca
günlük (`Nöbetçi bildirimi:` WARNING). `SZ_NOTIFIER_PROVIDER=teams|log` iken Slack kullanılmaz.

### 6.7 Bağlayıcı hataları
Hizmet talebi adımı `failed` olur, talep insan müdahalesi bekler (`POST /requests/{id}/retry`,
agent). Graph/Keycloak 401 → gizli/izin; Graph'ta kilit açma ve paylaşılan posta kutusu
desteklenmez (beklenen `failed`). Hata mesajları gizli bilgi taşımaz.

## 7. KVKK notları

**Maskeleme kapsamı** (`privacy.mask_pii`): T.C. kimlik no (sağlama doğrulamalı), e-posta,
Türkiye cep telefonu (5xx), TR IBAN, kart no (Luhn). **Maskelenmez:** ad-soyad, kullanıcı adı,
adres, sabit hat, TR dışı IBAN, serbest metindeki diğer kişisel veriler.

Maskeleme uygulanan yerler: LLM'e giden her istem (sistem + kullanıcı, `serviszeka.llm`),
yapay zekâ kararının girdi özeti, MCP/yapay zekâ çağıranlara dönen araç sonuçları, eylem isteğinin
gösterilen argümanları (`args_display`), bildirim kuyruğu yükü ve hata metni, izleme etiketleri
(`password`/`token` görünümlü anahtarlar `***`). **Veritabanındaki kayıtlar maskesizdir** (olay,
talep, eylem isteği ham `args`'ı, denetim ayrıntıları); erişim rol ile sınırlanır (`requester`
yalnızca kendi kayıtlarını görür). Geçici parola hiçbir kayıtta, sonuçta ya da denetimde yer almaz.

**LLM veri akışı** (Anthropic API; yurt dışına aktarım — hukuki dayanak ve veri işleyen
sözleşmesi kurumca değerlendirilmeli):

| Özellik | Gönderilen (maskeli) | Model |
|---|---|---|
| Olay triage | Başlık + açıklama, en çok 3 bilgi bankası makalesi, ilgili CI adları | `SZ_LLM_FAST_MODEL` |
| Değişiklik risk anlatısı | Değişiklik metni + deterministik risk dökümü | `SZ_LLM_FAST_MODEL` |
| Sorun RCA taslağı | Bağlı en çok 20 olay, alan başına 500 karakter | `SZ_LLM_REASONING_MODEL` |
| CSI gerekçesi | Sinyal kanıtı (sayılar) | `SZ_LLM_FAST_MODEL` |

`SZ_ANTHROPIC_API_KEY` verilmezse hiçbir veri LLM'e gitmez (bu özellikler 503 / insana).

**Saklama:** uygulama yalnızca bildirim kuyruğunu (`SENT` 30, `DEAD` 90 gün) ve webhook
tekrar kayıtlarını (2 × `SZ_EVENTS_REPLAY_WINDOW_SECONDS`) kendiliğinden siler. Olay, talep,
yapay zekâ kararı, uyarı, eylem isteği ve denetim kayıtları için otomatik saklama/imha **yoktur**;
saklama sürelerini kurum belirlemeli. Denetim zincirinden kayıt silmek ya da anonimleştirmek
doğrulamayı bozar (§4) — imha talebini hukuk birimiyle planlayın.

Portal belirteçleri `sessionStorage`'dadır (sekme kapanınca silinir); `localStorage` kullanılmaz.

## 8. Kapıların yeniden ölçümü

| Kapı | Sentetik (tekrarlanabilir) | Üretim verisinden |
|---|---|---|
| Faz 1 — olayların %70'i doğru sınıflandırılır | `uv run python evals/run_eval.py` | `GET /kpi?kpi=ai_triage_acceptance&days=30` |
| Faz 3 — standart taleplerin %50'si insansız | `uv run python evals/run_automation_eval.py [--stack entra\|keycloak]` | `GET /requests/metrics`, `GET /kpi?kpi=automation_rate` |
| Faz 4 — MTTR düşüşü veriden | `uv run python evals/run_mttr_evidence.py` (SENTETİK) | `GET /kpi?kpi=mttr&days=30`, kapanan CSI kaydının `measurement` alanı |

**Sınıflandırma (%70).** Gerçek LLM ile çalışır (`SZ_ANTHROPIC_API_KEY`; veri seti sentetik ama
API'ye gider). `--model`, `--limit`, `--concurrency`; kendi etiketli örnekleminiz için
`--dataset kayitlar.jsonl` (satır başına `{"id","title","description","labels":{"kind","category",
"service","assignment_group","impact","urgency"}}`, değerler `taxonomy.json`'dan). Gölge modda
çalışır, kayıt değiştirmez; sonuç `evals/results/`, kapı geçilmezse çıkış kodu 1. Taksonomi, istem
ya da model değişince yeniden ölçün. Üretimde `ai_triage_acceptance` (uygulanan ve insanın
değiştirmediği kararların oranı) eğilimini izleyin.

**Otomasyon (%50).** LLM'siz, ağsız; 200 etiketli talep gerçek boru hattından geçer. Kapı: oran ≥
%50 **ve** yanlış insansız = 0 **ve** değişmez ihlali = 0. `SZ_IDENTITY_STANDARD_GROUPS`, katalog
öğeleri, kimlik yığını ya da bir aracın risk kuralı değişince yeniden çalıştırın (hedef yığın için
`--stack`). Üretimde `GET /requests/metrics` (`standard_touchless_rate`, `meets_gate`,
`by_item`) kapanan standart taleplerden hesaplanır.

**MTTR kanıtı.** `run_mttr_evidence.py` deterministik SENTETİK senaryodur; gerçek kanıt yerine
**sunulmaz**. Gerçek ölçüm: (1) KPI'lı bir iyileştirme kaydı açın (`kpi_key=mttr`, gerekirse
kırılım; taban KPI'dan hesaplanır), (2) approver onaylar, (3) başlatılır ve uygulama anı
işaretlenir (`/implemented`), (4) `SZ_CSI_MEASURE_DAYS` dolunca `POST /improvements/{id}/measure`
(dolmadan 409), (5) kapatılır. `measurement.delta_pct` ve `target_met` iddianın kaynağıdır;
`GET /kpi?kpi=mttr&days=30` önceki eşit pencereye göre `delta_pct` verir.

# Faz 4 — Olgunluk: KPI'lar, raporlama ve tahmine dayalı SLA

**Hedef:** sürekli iyileştirme ölçülebilir olmalı — *MTTR düşüşü veriden hesaplanabilir*. Her KPI
aynı uzunluktaki önceki pencereyle karşılaştırılır (`previous`, `delta`, `delta_pct`, `trend`);
örn. `GET /kpi?kpi=mttr&days=30` → `delta_pct = -40` "son 30 günde MTTR %40 düştü" demektir.

## KPI'lar — `services/kpi.py`

Pencere yarı açık ve UTC'dir: `[end - days, end)` (`end` varsayılan şimdi; saat dilimsiz girdi UTC
kabul edilir, `+03:00` gibi girdiler UTC'ye çevrilir). Önceki pencere `[start - (end - start),
start)`. `trend`: KPI'nın `better` yönüne göre `improved` / `worsened` / `flat` / `n/a` (bir
pencerede veri yoksa). Süreler SQL'de `epoch_seconds` ile hesaplanır (PostgreSQL `EXTRACT(EPOCH
FROM timestamptz)` — oturum saat diliminden bağımsız; SQLite `julianday`, değerler UTC saklanır);
sayım/oranlar SQL `COUNT` / `SUM(CASE …)` ile gruplanır; medyan/p90 taşınabilirlik için Python'da.
Durum geçmişine (denetim günlüğü) dayanan KPI'lar JSON ayrıntıyı Python'da yorumlar.

"Olay" = `Incident.kind == incident`; **etkin süre** = duvar saati süresi − `sla_paused_seconds`
(arayan/tedarikçi beklemesinde duran SLA saati hariç).

| Anahtar | Birim / iyi yön | Formül |
|---|---|---|
| `mttr` | saat / düşük | `ort(resolved_at − created_at − sla_paused_seconds)`, `resolved_at ∈ W`, iptal hariç; ayrıca medyan, p90; kırılım öncelik / grup / kategori / hizmet. Yeniden açılan olayda son çözüm sayılır |
| `mtta` | saat / düşük | `ort(responded_at − created_at)`, `responded_at ∈ W`, **iptal hariç** (MTTR ile tutarlı; 0014 öncesi iptal edilen olaylar da sayılıyordu) (ilk yanıt = NEW'den ilk çıkış); kırılım öncelik / grup |
| `fcr_rate` | % / yüksek | İlk temasta çözüm vekili: W'de çözülen olaylardan hiç yeniden atanmamış (`assignment_group` dolu bir değerden başka değere hiç değişmemiş — ilk atama sayılmaz) ve hiç yeniden açılmamış olanlar / tümü |
| `sla_response_compliance` | % / yüksek | Uyumlu / (uyumlu + ihlal). Nüfus: W'de yanıtlanan (`responded_at <= response_due_at` uyumlu) + hâlâ yanıtsız ve vadesi W'de ve şimdiden önce geçen (ihlal; saat şu an duruyorsa ve vade duraklamadan sonraysa sayılmaz). Kırılım öncelik / grup |
| `sla_resolve_compliance` | % / yüksek | Aynı kural `resolved_at` / `resolve_due_at` ile. Vadeler duraklatma kadar ötelendiğinden duraklatılan süre uyumu bozmaz |
| `backlog` | adet / düşük | Pencere sonunda açık olay: `created_at < T` ve (`resolved_at > T` ya da (`resolved_at` boş ve durum kapalı/iptal değil)); yaşlanma kovaları `0-1g, 1-3g, 3-7g, 7-30g, 30g+`, öncelik/grup kırılımı |
| `reopen_rate` | % / düşük | W'de `status: resolved → (closed olmayan)` geçişi olan olay / W'de çözülen ya da yeniden açılan olay (denetim `incident.update`) |
| `automation_rate` | % / yüksek | `touchless` / `fulfilled_at ∈ W` hizmet talepleri (Faz 3 tanımı); kırılım katalog öğesi |
| `ai_triage_acceptance` | % / yüksek | W'de üretilen `service_desk.classify` kararlarından **kabul edilen** / tümü. Kabul = uygulandı (`auto_applied` ya da insan onayladı) **ve** sonrasında bir insan (`ai:` olmayan aktör; `ai_triage_approval` onay güncellemesi hariç) kararın uyguladığı alanlardan birini (etki/aciliyet uygulandıysa öncelik dahil) değiştirmedi. `details`: `applied`, `accepted`, `overridden`, `override_rate`, `not_applied`, sonuç dağılımı |
| `change_success_rate` | % / yüksek | `succeeded` / `actual_end ∈ W` ve `implementation_outcome` dolu değişiklikler (geri alınan değişiklik `failed` ile kapanır); kırılım tür |
| `emergency_change_share` | % / düşük | `emergency` / `created_at ∈ W` değişiklikler |
| `problem_known_error_lead_time` | saat / düşük | `ort(ilk known_error geçişi − sorun created_at)`, geçiş ∈ W. Geçiş = denetimde `problem.transition (to=known_error)` ya da `problem.known_error.publish (from=new|investigating)` |

Bilinen yaklaşımlar: iptal zamanı tutulmadığı için iptal edilen olaylar geçmişte de açık sayılmaz;
yeniden açılan olayın eski çözüm aralığı `backlog` geçmişinde açık görünür; vadesi geçen açık
olay, sonradan çözüldüğü pencerede de (geç çözüm olarak) sayılır. Gölge modda önerilip insanın
elle aynen uyguladığı triage kararı "uygulanmadı" sayılır.

## Tahmine dayalı SLA — `services/sla_forecast.py`

Açık olay başına **açıklanabilir** çözüm SLA'sı ihlal olasılığı (kara kutu ML yok):

1. **Karşılaştırma kümesi:** aynı tür + öncelik + çözüm grubu, son `lookback_days` (varsayılan 180)
   içinde açılmış, iptal hariç. Çözülmüş olay < `MIN_SAMPLE` (10) ise yalnızca tür + öncelik
   (`basis=priority`); o da yetmezse tahmin yok (`basis=none`, `probability=null`).
2. **Kaplan–Meier:** etkin süreler; hâlâ açık karşılaştırma olayları o ana kadarki etkin yaşlarıyla
   sağdan sansürlü gözlemdir (yalnızca hızlı çözülenleri saymanın iyimser yanlılığını önler).
3. **Koşullu olasılık:** bütçe `B = (resolve_due_at − created_at) − sla_paused_seconds`, etkin yaş
   `a` → `P = S'(B) / S'(a)`, `S'(t) = (n·S(t) + 0.5)/(n + 1)` düzeltmesiyle; ihlale düşmemiş olayda
   `[0.01, 0.99]`. `a ≥ B` → `basis=breached`, `P = 1`. Saat duruyorsa yaş duraklama anında donar.
4. **Kuyruk yükü:** `load_ratio` = grubun şu anki açık kaydı / son `lookback_days` içinde 24 eşit
   aralıklı andaki ortalama açık kaydı (en az 1). Olasılık oranı `clamp(load_ratio, 0.5, 3)^0.5`
   ile çarpılır; beklenen kalan süre aynı çarpanla ölçeklenir.
5. **Beklenen çözüm:** koşullu medyan (`S(t) ≤ S(a)/2` ilk `t`) → `şimdi + (t − a)·çarpan`.
6. **Gerekçeler** (`reasons[]`, Türkçe): `base_rate`, `age`, `queue_load`, `fallback_priority`,
   `paused`, `already_breached`, `insufficient_sample`; `weight` = log-olasılık oranına katkının
   mutlak değeri, büyükten küçüğe.

**Geriye dönük sınama** (`backtest(split_at, end)`): zaman tabanlı bölme. Eğitim: `split_at`
öncesinde açılanlar (o anda açık olanlar o andaki duvar saati yaşıyla sansürlü — gelecek bilgisi
sızmaz). Test: `[split_at, end)` içinde açılan ve sonucu `end` anında bilinenler — `end`'den önce
çözülenler ile `end`'de hâlâ açık ama bütçesini aşmış olanlar (ihlal); sonucu bilinmeyen açıklar
dışarıda (yalnızca çözülenleri almak ihlaldeki açık olayları atardı: sonuca göre seçim). Tahmin
açılış anında (`a = 0`, yük düzeltmesi yok). Geçmiş bir an için `forecast(now=…)` da o andan sonra
çözülen olayları o anda açık (sansürlü) sayar. Çıktı: Brier skoru, sabit oran (eğitim ihlal oranı) taban çizgisi, beceri
`1 − brier/baseline`, 5 kovalı kalibrasyon tablosu. Sentetik testte (iki grup, %70 / %10 ihlal)
Brier 0.17, taban 0.23, beceri 0.26.

## HTTP ve araçlar

Okuma rolleri: agent / approver / admin (`ReaderUser`); `requester` ve `ai_agent` belirteci 403.

| Uç nokta | Açıklama |
|---|---|
| `GET /kpi?days=30&end=&kpi=mttr&kpi=…` | `KPIReportRead` (`window`, `previous_window`, `kpis[]`: değer, payda, önceki, delta, eğilim, ayrıntı, kırılım) |
| `GET /kpi/definitions` | Anahtar, ad, birim, iyi yön, formül |
| `GET /sla/report?days=&end=` | Yanıt + çözüm SLA uyumu (öncelik / grup kırılımı) |
| `GET /sla/forecast?assignment_group=&min_probability=&limit=&lookback_days=` | `ForecastReportRead` (olasılığa göre azalan; tahminsizler sonda) |
| `GET /sla/forecast/backtest?split_at=&end=&lookback_days=` | `BacktestRead` |

Araçlar (`tools/builtin/kpi.py`, hepsi **R0**, `READERS`, politika girdisi `LOW`): `kpi.report`
(`days`, `end`, `kpis`), `sla.report` (`days`, `end`), `sla.forecast` (`assignment_group`,
`min_probability`, `limit`). Kapı (`tests/test_gate_no_unapproved_risk.py` bölüm 7): R0 + koşullu
risk yok + politika girdisi var; yapay zekâ en gevşek politikada çağırdığında `AIDecision` /
`ActionRequest` / `ToolInvocation` ya da kayıt değişikliği oluşmaz (yalnızca `tool.read`
denetimi); hesaplayan modüller statik taramada yazma/commit/denetim çağrısı içermez.

Testler: `tests/test_kpi.py` (duraklatılan SLA, yarı açık pencere sınırları, `+03:00` / saat
dilimsiz uç durumları, denetime dayalı KPI'lar, API rolleri; `SZ_TEST_POSTGRES_URL` verilirse
PostgreSQL'de `SET TIME ZONE 'Asia/Tokyo'` altında SQLite ile aynı sonuç), `tests/test_sla_forecast.py`
(KM, geri dönüş, yük, duraklatma, saat dilimi, backtest kalibrasyonu ve sızıntısızlık).

## İzleme ve olay yönetimi: kalıcı bildirim kuyruğu ve arka plan zamanlayıcısı

Göç `migrations/versions/0010_notification_outbox_scheduler.py` (`notification_outbox`,
`scheduler_leases`). Testler `tests/test_notification_dispatch.py`, `tests/test_notification_outbox.py`,
`tests/test_scheduler.py`.

**Kalıcı bildirim kuyruğu** (`notifications.py`, `models.NotificationOutbox`; bellek içi outbox'ın yerine):

- `notifications.stage` bildirimi kaydı oluşturan işlemle **aynı işlemde** `notification_outbox`
  satırı olarak yazar (`event`, PII maskeli `payload`/`details`, `target`=`oncall`, `state`
  `pending/sent/failed/dead`, `attempts`, `next_attempt_at`, `last_error`, tekil `idempotency_key`).
  Rollback / SAVEPOINT geri alması satırı da düşürür: kaydedilmeyen karar için nöbetçi uyarılmaz
  (bellek içi outbox testlerinin güvenceleri aynen geçerli).
- Gönderim **en az bir kez**: satır kısa bir işlemde karşılaştırmalı güncellemeyle kiralanır
  (`next_attempt_at <= şimdi` → `şimdi + 120 sn`, `attempts + 1`; aynı satırı iki gönderici aynı
  anda alamaz), işlem dışında gönderilir, sonuç + denetim (`notification.sent` / `failed` / `dead`)
  ayrı işlemde yazılır. Çökme olursa kira dolunca yeniden denenir; `idempotency_key` alıcıya gider
  (`WebhookNotifier`: `Idempotency-Key` başlığı + gövde alanı).
- Başarısızlıkta üstel geri çekilme: `SZ_NOTIFICATION_BACKOFF_SECONDS * 2^(n-1)` (en fazla 1 saat);
  `SZ_NOTIFICATION_MAX_ATTEMPTS` (5) aşılınca `dead`. `last_error` URL'leri (`[URL]`, webhook belirteci)
  ve `token=`/`password=` biçimli gizlileri ayıklar, PII maskeler.
- Kiralanan satır `sending` olur (kira dolunca yeniden kiralanabilir). İkinci ve sonraki denemelerde
  bildiriciye `possibly_delivered=True` geçer; Slack nöbetçisi kanal geçmişinde anahtarı bulursa
  yeniden göndermez (`notification.sent`, `details.deduplicated=true`) — bkz. "Açık konular".
- Saklama: `sent` satırlar `SZ_OUTBOX_RETENTION_DAYS` (30), `dead` satırlar
  `SZ_OUTBOX_DEAD_RETENTION_DAYS` (90) gün sonra toplu silinir (zamanlayıcı `outbox_retention`).
- `send_pending` (API, commit sonrası) bu oturumun satırlarını hemen gönderir; gönderilemeyen ya da
  süreç çöktüğü için gönderilmeyen satırı zamanlayıcı (`dispatch_due`) gönderir.

**Arka plan zamanlayıcısı** (`scheduler.py`, FastAPI lifespan; `SZ_SCHEDULER_ENABLED`=true,
testlerde false; `SZ_SCHEDULER_TICK_SECONDS`=15):

| İş | Aralık | Ne yapar |
|---|---|---|
| `outbox_dispatch` | 30 sn | zamanı gelmiş kuyruk satırlarını (kirası dolmuş `sending` dahil) gönderir |
| `outbox_retention` | 3600 sn | saklama süresi dolan `sent` / `dead` satırları 500'lük dilimlerle (çalışma başına en çok 20 dilim) siler; açık olayın `sla:*` satırları korunur |
| `action_expiry` | 60 sn | süresi dolan bekleyen eylem isteklerini `expired` yapar (önceden yalnızca `GET /actions`); bağlı talep `failed` |
| `sla_check` | 60 sn | açık **P1/P2** olayda SLA `at_risk` / `breached` için nöbetçi bildirimi (`sla.at_risk` / `sla.breached`) kuyruğa ekler; olay + durum başına bir kez (`idempotency_key = sla:<olay>:<durum>`), denetim `sla.alert` (`system:scheduler`) |
| `request_expiry` | 300 sn | `SZ_REQUEST_APPROVAL_TTL_HOURS` (168) içinde iş onayı verilmeyen talebi **reddeder** (`rejected`; `failed` değil — yeniden deneme iş onayını atlayamasın), kancası kaçmış bekleyen talebi ilerletir |

Birden fazla kopya: iş başına `scheduler_leases` satırı; kira `UPDATE … WHERE job=:j AND
lease_until <= :şimdi` (compare-and-set; PostgreSQL ve SQLite'ta aynı) ile alınır, satır yoksa
`INSERT` (tekil anahtar çakışması = alınamadı). Kira süresi `max(aralık, iş zaman aşımı 300 sn +
60 sn pay)` (zaman aşımında iptal gecikmesi / küçük saat kayması çift çalıştırmasın; kopyaların
saati NTP ile eşit olmalı);
iş bitince `lease_until = başlangıç + aralık` (aralık da kopyalar arasında korunur), çöken kopyanın
kirası dolunca devredilir. İş hatası zamanlayıcıyı durdurmaz (`last_status`/`last_error`, gizlisiz).
Testler işleri sahte saatle (`Scheduler(clock=…)`, `run_job`, `run_due`) doğrudan sürer.

## İzleme ve olay yönetimi: uyarılar, bağıntı ve olay açma

Göç `migrations/versions/0012_monitoring_events.py` (`event_groups`, `events`, `event_deliveries`).
Kod: `services/events.py` (doğrulama, ayrıştırma, CI eşleme, bağıntı, olay planı),
`agents/events.py` (`ai:events`), `tools/builtin/event.py`, `api/events.py`. Testler
`tests/test_events.py`, kapı `tests/test_gate_no_unapproved_risk.py` bölüm 8.

**Webhook'lar** (OIDC değil, kendi doğrulaması; `SZ_EVENTS_WEBHOOK_SECRET` boşsa her istek 401):

| Uç nokta | Gövde |
|---|---|
| `POST /events/alertmanager` | Prometheus Alertmanager webhook **v4** (`version: "4"`, `alerts[]`: `status`, `labels`, `annotations`, `startsAt`, `endsAt`, `fingerprint`) |
| `POST /events/generic` | `{"events": [{"source": "zabbix", "fingerprint": "…", "status": "firing\|resolved", "severity": "critical\|error\|warning\|…", "title": "…", "labels": {}, "annotations": {}, "starts_at": "RFC 3339", "ends_at": null}]}` (`extra` alan reddedilir; `fingerprint` yoksa kaynak + etiketlerden) |

- Kimlik: paylaşılan gizli (`Authorization: Bearer <gizli>` ya da `X-ServisZeka-Token`) **veya** HMAC:
  `X-ServisZeka-Timestamp: <unix sn>` + `X-ServisZeka-Signature: sha256=hex(HMAC-SHA256(gizli,
  "<ts>." + ham gövde))`. Karşılaştırmalar `hmac.compare_digest`. İmzalıda zaman damgası zorunlu,
  `SZ_EVENTS_REPLAY_WINDOW_SECONDS` (300) dışında 401. İmzasız (paylaşılan gizli) istekte belirteç
  gövde okunmadan doğrulanır (kimliksiz istemciye 413 değil 401; gövde okunmaz).
- Yeniden oynatma: teslimat anahtarı (kaynak + kip + ts + gövde SHA-256) `event_deliveries`'te tekil
  ve **iki pencere** saklanır (± pencere kabul edilen ileri tarihli imza alındıktan sonra 2 pencereye
  kadar geçerli kalabilir); aynı teslimat `{"duplicate": true}` döner, hiçbir şey yazılmaz. Uyarı düzeyinde
  (kaynak, parmak izi, `starts_at`) tekildir: tekrar gönderim `received_count` artırır, çözülen uyarı
  eski bir "firing" ile yeniden açılmaz.
- Gövde sınırı `SZ_EVENTS_MAX_BODY_BYTES` (1 MiB, 413), geçersiz JSON/şema ve aşırı iç içe gövde
  422, en fazla 500 uyarı, uyarı başına en fazla 64 etiket / açıklama.
- Etiket/açıklamalar PII maskeli; `password`, `token`, `api_key`… görünümlü anahtarlar `***`.

**CI eşleme** (`SZ_EVENTS_CI_MAPPING`, JSON liste; ilk eşleşen kural kazanır): `label` → `match`
`id` / `name` (büyük-küçük harf duyarsız, `strip_port` ile `host:9100` → `host`, aynı ad birden çok
ortamda ise `environment`/`env` etiketi, yoksa prod) / `external_ref` (`external_refs[key]`) /
`attribute` (`attributes[key]`). Varsayılan: `ci_id`→id, `ci`, `instance` (port atılır), `service`,
`job` → ad.

**Bağıntı** (`SZ_EVENTS_CORRELATION_WINDOW_MINUTES`=30): aday gruplar açık gruplar ve uyarıları
çözülmüş ama **olayı hâlâ açık** gruplardır (çözüm insan onayı beklerken yeniden ateşleme yeni olay
açmaz; grup yeniden açılır, aynı olaya not düşülür). Aynı parmak izi açık grupta ateşleniyorsa o
grup (tekilleştirme); pencere içindeki açık grubun kök CI'ı yeni uyarının CI'ının CMDB
bağımlılığıysa (depends_on / runs_on / connects_to hedefi, part_of bütünü; en fazla 5 adım) uyarı o
gruba katılır; yeni CI açık grupların kökünün bağımlılığıysa grup yeni CI'a **yeniden köklenir** ve
açıkladığı diğer olaysız gruplar birleştirilir (`merged`). Olay açılmış gruplar birleştirilmez (grup
başına tek olay). Çırpınma: aynı parmak izi `SZ_EVENTS_FLAP_WINDOW_MINUTES` (60) içinde
`SZ_EVENTS_FLAP_THRESHOLD` (4) kez başlarsa uyarı `suppressed=flapping` (olay/not tetiklemez).
Susturulmuş grubun yeni uyarıları `suppressed=silenced`.

**Olay açma** (`agents/events.py`, aktör `ai:events`, yalnızca `tools.execute_tool`): açık, susturulmamış,
önemi `SZ_EVENTS_INCIDENT_MIN_SEVERITY` (`high`) ve üstü, bastırılmamış ateşleyen uyarısı olan grup için
`event.create_incident`. Öncelik mevcut kuraldan (`calculate_priority`): etki = grubun CI'larının etki
analizindeki en yüksek kritiklik (1 → high, 2 → medium, diğer → low; kesilen analiz high), aciliyet =
önem (critical → high, high → medium, diğer → low). P1/P2 olacaksa araç **R2** → `ActionRequest`
(bekleyen ya da reddedilen istek varken yenisi açılmaz). Olaylı gruba gelen yeni/çözülen uyarılar
aynı olaya çalışma notu + CI bağı olarak eklenir. Tüm uyarılar çözülünce not + `incident.resolve`
**önerisi** (R2, olay başına bir kez); olay asla otomatik çözülmez. **Olay fırtınası:** son bir
saatte izleme gruplarından `SZ_EVENTS_MAX_INCIDENTS_PER_HOUR` (20) olay açıldıysa ajan P3-P5 olay
açmaz (`skipped`, "olay fırtınası"; gruplar açık kalır, yeniden gönderimde sınır altına inince
açılır); P1/P2 olacak grup yine insan onayına önerilir. İzleme olaylarının arayanı (`caller`)
`system:izleme`'dir (kullanıcı adıyla çakışmaz: self-servis kullanıcı yalnızca `caller` = kendi adı
olan kayıtları görür).

| Araç | Risk | Not |
|---|---|---|
| `event.list_alerts` | R0 | durum / önem / grup / CI süzgeci |
| `event.correlate` | R0 | kök CI, her uyarıya CMDB yolu, önerilen öncelik, bekleyen istek |
| `event.acknowledge` | R1 | grubu üstlenir |
| `event.create_incident` | R1, P1/P2 olay açacaksa **R2** | grup başına tek olay / not |
| `event.silence` | R2 | süreli susturma (5 dk – 7 gün), gerekçe zorunlu |

Okuma API'si (agent / admin): `GET /events?status=&severity=&group_id=&ci_id=&source=&limit=&offset=`,
`GET /events/groups?state=`, `GET /events/groups/{id}` (bağıntı + üye uyarılar).

İnsan eylemleri (agent / admin; yapay zekâ belirteci, onaylayan ve talep eden 403), araç boru
hattından kullanıcının kimliğiyle (`execute_tool`; yanıt `AgentResultRead`):
`POST /events/groups/{id}/acknowledge` (`{note}` → `event.acknowledge`, R1, hemen) ve
`POST /events/groups/{id}/silence` (`{minutes: 5–10080, comment}` → `event.silence`, R2: bekleyen
`ActionRequest`; isteği açan onaylayamaz, başka bir onaylayan `/actions/{id}/approve` ile onaylayınca
grup susturulur). Bilinmeyen grup 404, geçersiz gövde 422.

## Sürekli iyileştirme (ITIL 4 CSI kaydı)

Göç `migrations/versions/0013_continual_improvement.py` (`improvements`). Kod:
`services/improvements.py` (kayıt, durum makinesi, ölçüm), `services/improvement_schemas.py`,
`agents/improvement.py` (`ai:csi`), `tools/builtin/csi.py`, `api/improvements.py`, zamanlayıcı işi
`csi_review`. Testler `tests/test_improvements.py`, `tests/_csi.py`, kapı
`tests/test_gate_no_unapproved_risk.py` bölüm 9, `tests/test_tools.py` (R0/R1 işleyici örnekleri).

**Kayıt** (`CSI0000001`): `title`, `description`, `source` (`kpi` / `problem` / `pir` / `manual` /
`ai`), bağlı KPI `kpi_key` (12 KPI'dan biri) + isteğe bağlı kırılım `kpi_dimension` =
`kpi_dimension_value` (KPI'nın `breakdown` boyutlarından; ör. `mttr` → `priority` / `assignment_group`
/ `category` / `service`, `sla_*_compliance` → + `category`, `reopen_rate` → `assignment_group` /
`category`, `ai_triage_acceptance` → `category`, `change_success_rate` → `change_type`),
`baseline_value` (+ `baseline_start/end`), `target_value`, `measured_value`, `target_met`,
`measurement`, `measure_days` (`SZ_CSI_MEASURE_DAYS`, 30), `owner`, `benefit_estimate` ve `effort`
(1-5), `priority_score = fayda / efor × 20` (4-100), `problem_id`, `change_id`, `evidence`,
`signal_key`, onay/başlatma/uygulama/kapanış izleri, `version` (iyimser kilit, `If-Match`).

**Durumlar:** `idea → approved → in_progress → measured → closed`; `idea` / `approved` /
`in_progress` → `rejected` (gerekçe zorunlu). KPI'lı kayıt ölçülmeden kapatılamaz; KPI'sız kayıt
`in_progress`'ten kapanış notuyla kapanır. KPI tanımı, taban, hedef ve ölçüm penceresi yalnızca
`idea` durumunda değişir (ölçüm sonradan oyunlanamaz). Onay, red, başlatma, uygulandı işareti ve
kapatma **yalnızca insan** (servis `ai:` aktörü / `ai_agent` rolünü reddeder).

**Ölçüm (MTTR'de ölçülebilir düşüşün kanıtı):** taban, kayıt anında (verilmediyse)
`[t - measure_days, t)` penceresinde KPI'nın (kırılımlı) değeridir. Yapay zekâ (`ai:csi` ya da MCP
istemcisi; onaylanan yapay zekâ isteği dahil) taban **veremez** (`csi.create_improvement` 422): taban
yalnızca KPI'dan hesaplanır; uydurma taban iyileşmeyi abartırdı. İnsan uygulamanın bittiği anı
işaretler (`implemented_at`); `[implemented_at, implemented_at + measure_days)` penceresi dolunca
(`csi.measure_improvement` ya da `POST /improvements/{id}/measure`, dolmadıysa 409) aynı KPI hesaplanır:

```json
{"kpi_key": "mttr", "better": "lower", "dimension": {"name": "assignment_group", "value": "network_ops"},
 "before": {"value": 10.0, "window": {"start": "…", "end": "…"}},
 "after": {"value": 5.0, "count": 6, "window": {"start": "…", "end": "…"}},
 "target_value": 6.0, "delta": -5.0, "delta_pct": -50.0, "improved": true, "target_met": true}
```

`target_met`: hedef varsa KPI'nın iyi yönüne göre (`lower`: ölçülen ≤ hedef, `higher`: ≥), yoksa
tabana göre iyileşti mi. Ölçüm penceresinde veri yoksa ölçülmez (422). Kapanan kayıtların
`measurement.delta_pct` değerleri "iyileştirme X, MTTR'yi %Y düşürdü" sorusunu veriden yanıtlar.

**CSI ajanı** (`agents/improvement.py`, aktör `ai:csi`, yazma yalnızca `tools.execute_tool`):
`detect_signals` deterministik ve salt okumadır (pencere `SZ_CSI_WINDOW_DAYS`, 30; önceki eşit pencere):

| Sinyal | Kural | KPI (kırılım) / hedef | Kaynak |
|---|---|---|---|
| `mttr_increase` | grubun MTTR'si önceki pencereye göre > %20 arttı (iki pencerede ≥ 5 olay) | `mttr` (grup) / önceki değer | kpi |
| `sla_breaches` | kategoride ≥ 3 çözüm SLA ihlali ve uyum < %90 | `sla_resolve_compliance` (kategori) / 90 | kpi |
| `ai_triage_acceptance` | kategoride ≥ 5 triage kararı, kabul < %60 | `ai_triage_acceptance` (kategori) / 80 | ai |
| `reopen_rate` | grupta ≥ 5 çözüm/yeniden açma, oran > %15 | `reopen_rate` (grup) / 10 | kpi |
| `kb_unhelpful` | yayındaki makale ≥ 5 olayda önerildi (`AIDecision.sources`), hiçbiri ilk temasta (yeniden atama/açma olmadan) çözülmedi | `fcr_rate` / — | ai |
| `sla_forecast` | grupta ihlal olasılığı ≥ 0.7 olan ≥ 3 açık olay | `sla_resolve_compliance` (grup) / 90 | kpi |
| `known_error_no_fix` | ≥ 14 gündür `known_error`, bağlı RFC yok | `mttr` (kategori) / taban × 0.8 | problem |
| `pir_failed_change` | pencerede PIR notuyla kapanan başarısız değişiklik | `change_success_rate` (tür) / 90 | pir |

`propose_improvements`: yeni her sinyal için `csi.create_improvement` (R1, politika `LOW`; gölge
modda ya da güven eşik altındaysa eylem isteği). Sinyal anahtarı (`signal_key`) açık kayıtta, bekleyen
istekte ya da `SZ_CSI_COOLDOWN_DAYS` (30) içinde kapanan/reddedilen kayıtta varsa yeniden önerilmez.
LLM (`serviszeka.llm`, `llm_fast_model`) yalnızca 2-4 cümlelik Türkçe gerekçe yazar (istem maskeli;
yanıt maskelenir, 4000 karakterle sınırlı); başarısızsa ya da LLM yoksa "Otomatik tespit: …" metni.
Sayılar her zaman deterministik `evidence` + açıklamadaki "Kanıt:" satırıdır. `measure_due`:
ölçüm penceresi dolan `in_progress` kayıtlar için `csi.measure_improvement` (R1, güven 0.95).
`run_review` ikisini birlikte çalıştırır; zamanlayıcı işi `csi_review` (günde bir, tek kopya).

| Araç | Risk | Not |
|---|---|---|
| `csi.list` | R0 | durum / kaynak / KPI / arama |
| `csi.detect_signals` | R0 | sinyaller + kanıt (yazmaz) |
| `csi.create_improvement` | R1 | yalnızca `idea`; durum/onay alanı şemada yok |
| `csi.measure_improvement` | R1 | önce/sonra ölçüm, `measured`; kapatmaz |

| Uç nokta | Rol |
|---|---|
| `GET /improvements?state=&source=&kpi_key=&owner=&q=&open_only=`, `GET /improvements/{id}` | agent / approver / admin |
| `POST /improvements`, `PATCH /improvements/{id}` (`If-Match`) | agent / approver / admin |
| `POST /improvements/{id}/start`, `/implemented` (`at`), `/measure` | agent / approver / admin |
| `POST /improvements/{id}/approve`, `/reject` (not zorunlu), `/close` | approver / admin |
| `POST /improvements/signals` (salt okuma), `POST /improvements/review` (ajanı şimdi çalıştırır) | agent / approver / admin |

requester ve yapay zekâ belirteci (`ai_agent`) her uçta 403. Kapı (bölüm 9): csi alanında yalnızca
bu 4 araç var (onay/kapatma aracı eklenemez), yaratma şemasında durum/onay/ölçüm alanı yok,
servis yapay zekâ aktörünün onay/red/başlatma/uygulandı/kapatma çağrısını reddeder, API'de
agent / requester / `ai_agent` onaylayamaz/kapatamaz, en gevşek politikada ajan yalnızca `idea`
açar ve ölçer, ajan modülü statik taramada doğrudan yazmaz.

KPI kırılımı ekleri (`services/kpi.py`, geriye uyumlu): `sla_*_compliance` → `category`,
`reopen_rate` → `assignment_group` / `category`, `ai_triage_acceptance` → `category`.


## Portal ekranları (Faz 4)

`portal/src/features/{kpi,events,improvements}`; rotalar/roller `portal/src/app/navigation.ts`
(`ROUTE_ROLES.dashboards|slaForecast|events|improvements`), ayrıntı `portal/README.md`.

| Rota | Roller | Uç nokta |
|---|---|---|
| `/panolar` | agent, approver | `GET /kpi` — kutucuklar (değer, önceki döneme göre ▲/▼ % + metin eğilim, örneklem) ve küçük SVG grafikler (kütüphanesiz): MTTR önceliğe göre, SLA uyumu, birikim yaşlanması, otomasyon / triage kabulü / değişiklik başarısı. Zaman serisi = aynı saate sabitlenmiş `end` ile 6-7 ardışık kova (`?days=<kova>&end=<kova sonu>`); her grafikte açıklama, metin özeti (`aria-label`) ve veri tablosu; renk + çizgi deseni (`--viz-*`, açık/koyu) |
| `/sla-tahmin` | agent, approver | `GET /sla/forecast` — olasılığa göre sıralı açık olaylar, gerekçeler (ağırlığa göre), dayanak + örneklem (çözülmüş + açık), olay bağlantısı |
| `/olaylar`, `/olaylar/:id` | agent | `GET /events/groups(/{id})` — kök CI, önem, durum (üstlenen / susturma / çırpınma), bağlı olay, CMDB yolu, bekleyen olay açma önerisi |
| `/iyilestirmeler`, `/iyilestirmeler/:id` | agent, approver | `/improvements*` — KPI + kırılım, taban / hedef / ölçülen, önce-sonra ölçümü, kanıt, zaman çizelgesi; adımlar role göre (onay / red / kapatma yalnızca approver) |

"Üstlen" ve "Sustur" düğmeleri `POST /events/groups/{id}/acknowledge` (`{note}` →
`event.acknowledge`, R1) ve `POST /events/groups/{id}/silence` (`{minutes, comment}` →
`event.silence`, R2) uçlarını çağırır; yanıt `AgentResultRead` (susturmada `pending_approval` +
`action_request_id` → ekranda "onay bekliyor" ve /onaylar bağlantısı). Uçlar backend'de
(`api/events.py`, `tests/test_events.py::test_group_acknowledge_and_silence_endpoints_go_through_tool_pipeline`)
ve E2E'de (`portal/e2e/faz4-events.spec.ts`) sınanır.


## Entegrasyon: göç zinciri, MTTR kanıtı ve uçtan uca senaryolar

**Göçler** tek doğrusal zincirdir (`0001` … `0014`, `tests/test_migrations.py::test_migration_chain_is_linear_single_head`):
`0010` bildirim kuyruğu + zamanlayıcı kiraları, **`0011` KPI indeksleri**
(`ix_incidents_resolved_at`, `ix_incidents_responded_at`, `ix_audit_log_occurred_at`; KPI pencere
sorguları ve denetim geçmişi okumaları için, `0004` ile aynı "yalnızca yoksa" üslubu), `0012` izleme /
olay, `0013` sürekli iyileştirme, `0014` açık konular (`action_requests.event_group_id` +
indeks ve geri doldurma, `ix_audit_log_entity`). Boş veritabanında, `0009`'dan (Faz 3) ve `create_all` ile kurulmuş
güncel şemada `alembic upgrade head` + `alembic check` temizdir; `0010`'a geri alınıp yeniden
yükseltilebilir (`tests/test_migrations.py::test_faz4_upgrade_from_0009_adds_kpi_indexes_and_downgrades`).

**MTTR kanıtı (SENTETİK)** — `uv run python evals/run_mttr_evidence.py` (mantık
`src/serviszeka/mttr_evidence.py`, test `tests/test_mttr_evidence.py`). Gerçek veri değildir; KPI
formüllerinin uçtan uca sınamasıdır. Bellek içi SQLite'ta deterministik olaylar: hedef grup
`network_ops` önceki 30 günde ortalama 9,5 sa (bir kısmı arayan beklemesiyle duraklatılmış), iyileştirme
pencere sınırında uygulanır, sonraki 30 günde 4,25 sa; kontrol grubu `dba` iki pencerede 6 sa. Betik:

1. `kpi.report` ile MTTR'yi (`ort(resolved_at − created_at − sla_paused_seconds)`, önceki eşit
   pencereyle) grup kırılımında hesaplar,
2. aynı değeri üretilen olaylardan bağımsız aritmetikle (duvar saati − duraklatma) yeniden hesaplar
   (±0,01 sa),
3. sürekli iyileştirme kaydını gerçek servisle yürütür (fikir → onay → uygulama → `measure` → kapanış;
   taban önceki pencereden, hedef taban × 0,7) ve ölçümün `delta_pct`'sinin KPI raporuyla aynı olduğunu,
4. kontrol grubunun değişmediğini (|Δ| < %10) denetler.

Çıktının son satırı: `MTTR network_ops: 9.50 sa -> 4.25 sa (%-55.26; 24 -> 24 olay, SENTETİK) -> GEÇTİ`
(`--json` ayrıntı, `--save` → `evals/results/mttr_evidence.json`, `--now` / `--days` pencere). Gerçek
kurulumda aynı hesap `GET /kpi?kpi=mttr&days=30` (eğilim) ve CSI kaydının `measurement` alanıdır.

**Uçtan uca (Playwright, `portal/e2e/faz4-*.spec.ts`)** ayrı bir backend + portal çiftine koşar
(`E2E_PROFILE=faz4`, 8767/5182): gölge mod kapalı, `SZ_EVENTS_WEBHOOK_SECRET` tanımlı, zamanlayıcı
kapalı (CSI incelemesi senaryodan `POST /improvements/review` ile tetiklenir), `SZ_CSI_WINDOW_DAYS=7`;
açılıştan önce `portal/e2e/faz4_data.py seed` SENTETİK geçmiş olayları yazar (üreteç
`serviszeka.mttr_evidence` ile ortak).

| Senaryo | Doğrulanan |
|---|---|
| `faz4-events` | Alertmanager v4 webhook'u (paylaşılan gizli; kimliksiz 401) veritabanı CI'sı ve ona `depends_on` ile bağlı uygulamada ateşleyen iki uyarı getirir → **tek** bağıntılı grup (kök CI veritabanı, CMDB yolu `db → api`) → olay ajanı tek olay önerir, öncelik **P2** (kritiklik 2 + kritik) olduğu için R2 bekler; destek uzmanı üstlenir (R1) ve susturma ister (R2, bekler); onaylayan /onaylar'dan onaylar → olay onaylayanın kimliğiyle P2 açılır; uyarılar çözülünce aynı olaya `ai:events` çalışma notu + `incident.resolve` önerisi (R2), olay açık kalır, ikinci olay yok |
| `faz4-kpi` | `/panolar` tohumlanmış veriyle açılır; MTTR ve birikim kutucukları panonun aldığı `GET /kpi` yanıtıyla aynı değer/örneklem/önceki dönemi gösterir; dört grafik ve veri tabloları; dönem 90 güne çevrilince rapor yenilenir; talep eden 403 |
| `faz4-csi` | Tohumdaki MTTR gerilemesi (`network_ops` ~4 sa → ~10 sa) → CSI ajanı (`ai:csi`) fikri önerir (taban 10 sa, hedef 4 sa; ikinci inceleme yeniden önermez) → onaylayan onaylar → destek uzmanı başlatır, uygulandı işaretler; pencere dolmadan "Ölç" reddedilir → senaryo saati 8 gün ileri (`faz4_data.py time-travel`: hedef grubun sentetik olayları ve iyileştirmenin zaman damgaları geri kayar, uygulama sonrası ~3 sa olaylar eklenir) → ölçülür (3 sa, "Hedef tuttu") → onaylayan kapatır; ölçülen değer kapanıştan sonra da görünür |

## Karşı inceleme (Faz 4 güvenlik / doğruluk)

Her bulgu önce başarısız bir testle yakalandı (`tests/test_faz4_review_regressions.py`), sonra
düzeltildi:

| Alan | Bulgu | Düzeltme |
|---|---|---|
| Webhook yeniden oynatma | İleri tarihli (± pencere) imzalı teslimatın tekilleştirme kaydı tek pencerede siliniyordu; imza hâlâ geçerliyken aynı istek yeniden oynatılabiliyordu | Kayıt iki pencere saklanır (`services/events.register_delivery`) |
| Webhook DoS | Aşırı iç içe JSON (`[[[…]]]`) `RecursionError` → 500 | 422 (`api/events.py`) |
| Webhook DoS | Yanlış paylaşılan gizliyle kimliksiz istemcinin gövdesi (1 MiB'a kadar) okunuyordu | Belirteç gövde okunmadan doğrulanır (`check_preconditions`) |
| Webhook DoS | Her yeni grup için tüm `EVG…` numaraları okunuyordu (webhook başına 500 grup) | En büyük numara tek sorguyla |
| Olay spamı | Olay fırtınasında (500 eşlenmeyen kritik uyarı) 500 otomatik olay | `SZ_EVENTS_MAX_INCIDENTS_PER_HOUR` (P1/P2 yine onaya) |
| Olay spamı | Çözülen grup (olay çözümü onay beklerken) yeniden ateşlenince yeni grup + **ikinci olay** | Olayı açık çözülmüş grup yeniden açılır, aynı olaya not |
| Talep eden verisi | İzleme olaylarının `caller`'ı `izleme`: bu adlı self-servis kullanıcı tüm izleme olaylarını görürdü | `caller = system:izleme` |
| Tahmin sızıntısı | Backtest test kümesi yalnızca `end`'den önce çözülenler: ihlaldeki açık olaylar atılıyordu (sonuca göre seçim, iyimser kalibrasyon) | Bitişte bütçesini aşmış açık olay ihlal sayılır, sonucu bilinmeyen dışarıda |
| Tahmin sızıntısı | Geçmiş `now` ile tahmin, o andan sonra çözülen olayların çözüm süresini kullanıyordu | O anda açık (sansürlü) sayılır; hedef olay `created_at < now` |
| CSI | Yapay zekâ `csi.create_improvement` ile taban değer uydurabiliyordu (R1 otomatik) | Yapay zekâ kaynaklı çağrıda `baseline_value` 422 |
| KPI / SLA API | `end=0001-01-05` gibi tarihte pencere hesabı `OverflowError` → 500 | 422 |
| Zamanlayıcı | Kira = iş zaman aşımı: iptal gecikmesi / saat kaymasında ikinci kopya aynı işi çalıştırabiliyordu | Kira = zaman aşımı + 60 sn pay |

## Açık konuların kapatılması (göç `0014`)

Testler `tests/test_faz4_open_items.py` (her madde önce başarısız testle); kapı bölüm 4'e
`test_gate_tools_ai_versioned_write_without_version_writes_nothing` eklendi.

**1. Kuyruk saklama süresi** (`notifications.purge_expired`, zamanlayıcı işi `outbox_retention`,
saatte bir, tek kopya): `state=sent` ve `sent_at < şimdi − SZ_OUTBOX_RETENTION_DAYS` (30) ile
`state=dead` ve `updated_at < şimdi − SZ_OUTBOX_DEAD_RETENTION_DAYS` (90) satırlar 500'lük dilimlerle
silinir; her dilim ayrı işlemdir, çalışma başına en çok 20 dilim (kalan sonraki saate). `pending` /
`failed` / `sending` satırlara dokunulmaz. **SLA tekilleştirmesi:** `sla:<olay>:<durum>` anahtarlı
satır, olay hâlâ açıkken (`resolved` / `closed` / `cancelled` değilken) silinmez — silinseydi
`sla_check` aynı ihlal için nöbetçiyi yeniden çağırırdı. Olay çözülüp kapandıktan sonra satır
silinebilir; çözülen olay sonradan yeniden açılırsa aynı durum için bir kez daha uyarı gidebilir
(olay yeniden canlıdır; bilinçli). Denetim kayıtları (`notification.*`) silinmez.

**2. Slack nöbetçi tekilliği.** Kuyruk en az bir kez gönderir; kira alınırken satır `sending` olur
(`attempts + 1`, kira 120 sn). İkinci ve sonraki denemelerde (önceki deneme çökmüş, zaman aşımına
uğramış ya da hata almış olabilir — alıcı mesajı almış olabilir) bildiriciye
`possibly_delivered=True` geçer. `SlackOncallNotifier` bot kipinde mesaja `metadata`
(`event_type=serviszeka_oncall`, `event_payload.idempotency_key`) ekler; yeniden denemede önce
`conversations.history` (`include_all_metadata=true`, son 6 saat, en çok 200 mesaj) okur ve anahtar
varsa göndermez (`{"deduplicated": true}` → satır `sent`, denetimde `deduplicated`). Slack
`chat.postMessage` belgelenmiş bir istemci tekillik anahtarı (`client_msg_id`) kabul etmez; bu
yüzden meta veri + geçmiş denetimi. Gerekli ek kapsam: `channels:history` (genel kanal) /
`groups:history` (özel kanal). **Kalan pencere (çift uyarı olabilir):** (a) webhook kipi
(`SZ_SLACK_WEBHOOK_URL`) geçmiş okuyamaz — anahtar yalnızca bağlam satırında `ref …` olarak görünür;
(b) geçmiş okunamazsa (kapsam yok, hız sınırı, Slack hatası) yine gönderilir — çift uyarı, kaçırılan
uyarıya tercih edilir; (c) kanalda son 6 saatte 200'den fazla mesaj varsa ya da Slack mesajı geçmişte
henüz göstermiyorsa; (d) `SZ_ONCALL_WEBHOOK_URL` (genel webhook) yinelemeyi yalnızca alıcı
`Idempotency-Key` ile ayıklarsa önler. İlk denemede geçmiş okunmaz (ek gecikme yok).

**3. SMTP sertleştirme** (`connectors/smtp.py`, `connectors/setup.check_smtp_transport`): TLS
zorunlu — STARTTLS (`SZ_SMTP_STARTTLS`, varsayılan) ya da örtük TLS (`SZ_SMTP_SSL`, 465; ikisi
birlikte geçersiz). Sunucu STARTTLS sunmazsa düz metne düşülmez, kimlik bilgisi gönderilmez
(`ConnectorError`, yalnızca istisna türü). TLS'siz SMTP yalnızca `SZ_SMTP_ALLOW_PLAINTEXT=true` **ve**
`SZ_ENVIRONMENT=development` iken (aksi halde açılış `ConnectorConfigError`; bildirici de
kurulmaz). Konu/alıcıda satır sonu (başlık enjeksiyonu) `email` kütüphanesinin `ValueError`'ı
içeriği (adres, konu, parola) taşımayan ve zinciri kesilmiş `ConnectorError`'a çevrilir; hiçbir şey
gönderilmez.

**4. Olay ajanı sorguları:** `ActionRequest.event_group_id` (indeksli; araç bağı `event_group`,
`event.create_incident` / `event.acknowledge` / `event.silence`'ta `group_id`'den dolar; göç eski
`event.*` isteklerini `args.group_id`'den geri doldurur). `_create_blocked`,
`services.events.pending_create_request` ve `_resolve_proposed` (indeksli `incident_id` bağı) artık
tek satırlık indeksli SQL sorgusudur; tüm isteklerin JSON argümanları Python'a okunmaz.

**5. MTTA formülü:** iptal edilen olaylar MTTR'de olduğu gibi MTTA'dan da hariçtir
(`ort(responded_at − created_at)`, `responded_at ∈ W`, tür=olay, **iptal hariç**). Önceki
sürümle karşılaştırmada MTTA değeri, pencerede yanıtlanıp sonra iptal edilen olay kadar değişebilir.

**6. CSI geçmiş toplamları SQL'de:** `kpi.reassignment_clause` / `reopen_clause` (JSON yol ifadesi:
SQLite `json_extract`, PostgreSQL `#>>`) `is_reassignment` / `is_reopen` ile aynı kuralı SQL'de
uygular; `kpi.not_first_contact_ids` yalnızca eşleşen olay kimliklerini döndürür. CSI ajanının
`kb_unhelpful` sinyali olay durumu ve ilk temas geçmişini SQL'de süzer (denetim satırı ya da olay
nesnesi yüklenmez); makale ↔ olay eşleşmesi (`AIDecision.sources` JSON listesi) taşınabilirlik için
Python'da kalır. Bekleyen CSI istekleri yalnızca `args.signal_key` alanıyla okunur. Yeni indeks
`ix_audit_log_entity (entity_type, entity_id)` olay başına geçmiş sorgularını tam taramadan çıkarır.
KPI formülleri (`fcr_rate`, `reopen_rate`, `ai_triage_acceptance`) değişmedi.

**7. Yapay zekâ / MCP yazmalarında zorunlu sürüm:** olay/değişiklik kaydını değiştiren ve
`expected_version` alanı olan araçlarda (`incident.classify|set_priority|assign|link_ci|set_on_hold|resolve`)
yapay zekâ aktörü (`ai:` / `ai_agent`) ve **her MCP çağıranı** (`execute_tool(require_version=True)`)
sürümü vermek zorundadır; yoksa `validation_error` ve hiçbir şey yazılmaz (R2 isteği de açılmaz).
Uyuşmayan sürüm yürütmede 409 (onaylanan R2 isteği, olay o arada değiştiyse `failed`). Olay ajanının
`incident.resolve` önerisi sürüm taşır. HTTP'de `PATCH /incidents/{id}`, `PUT /incidents/{id}/cis`
ve `PATCH /changes/{id}` yapay zekâ aktöründen `If-Match` / `expected_version` olmadan **428**
döner (değişiklik düzenlemesini servis yapay zekâya zaten kapatır: 403). **İnsanlar için şimdilik
geriye uyumlu:** portal zaten `If-Match` gönderir; REST'te insan çağıranda sürüm isteğe bağlıdır
(sonraki sürümde zorunlu yapılması planlanıyor).

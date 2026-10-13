# Faz 2 — Kontrol: CMDB, Değişiklik Yönetimi + CAB, politika

**Kapı:** *Hiçbir riskli eylem onaysız çalışmıyor.* Kanıt: `tests/test_changes.py` içindeki `test_gate_*`
testleri (politika, yapay zekâ aktörü, uygulama anında yeniden yetki denetimi, risk önerisi) ve durum makinesi
testleri.

Bu belge, üzerine inşa edecek ajan/portal geliştiricileri için **genel servis API'sini** özetler. Tüm servis
fonksiyonları `flush` eder, **commit etmez** (commit çağıranın işidir) ve her yazmayı `audit.record` ile denetim
zincirine yazar.

## Roller (`serviszeka.auth`)

| Rol | Yetki |
|---|---|
| `change_manager` | Değişiklikleri yetkilendirme/zamanlama/kapatma, ECAB onayı, CMDB yazma, şablon ve dondurma penceresi yönetimi, yapay zekâ risk önerisini onaylama |
| `cab` | Değişiklikleri okuma, CAB onayı/reddi |
| `agent` | Değişiklik açma ve kendi taslağını düzenleme/gönderme; atandığı (assignee) değişikliği uygulama; CMDB okuma |
| `admin` | Hepsi |
| `requester`, yalnızca `approver` | Değişiklik ve CMDB uçlarını **göremez** (403) |

Hazır bağımlılıklar: `ChangeViewerUser` (agent/cab/change_manager), `ChangeCreatorUser` (agent/change_manager),
`ChangeManagerUser`, `CabUser` (cab/change_manager), `CMDBReaderUser`, `CMDBWriterUser`.

## CMDB — `serviszeka.services.cmdb`

Modeller: `ConfigurationItem` (`ci_class`: application/service/server/database/network_device/cloud_resource;
`environment`: prod/test/dev; `criticality` 1–4, **1 = en kritik**; `owner_group` = taksonomi
`assignment_group` anahtarı; `status`; `attributes`, `external_refs` JSON; `version`) ve `CIRelationship`
(kaynak → hedef, tür `depends_on`/`runs_on`/`connects_to`/`part_of`; `(source, target, type)` tekil). Aynı
ortamda aynı ad tekildir.

| Fonksiyon | Açıklama |
|---|---|
| `create_ci(session, CICreate, actor)` | Taksonomi dışı `owner_group` / yinelenen ad → `CMDBValidationError` |
| `get_ci`, `get_cis(session, ids)`, `list_cis(session, ci_class=, environment=, status=, owner_group=, q=, limit=, offset=)` | `get_cis` eksik id'de `CINotFoundError` |
| `update_ci(session, id, CIUpdate, actor, expected_version=)` | İyimser kilit → `VersionConflictError` |
| `add_relationship(session, source_id, target_id, type, actor)` | Öz döngü → `CMDBValidationError`, yinelenen → `DuplicateRelationshipError` |
| `remove_relationship`, `list_relationships(session, ci_id)` | `list_relationships` iki yönü de döndürür |
| `impact_analysis(session, ci_ids, max_depth=5)` → `ImpactAnalysis` | Aşağıya bakın |

**Etki analizi** "bu CI değişirse kim bozulur" sorusunu yanıtlar (yukarı yönlü bağımlılar): `depends_on`,
`runs_on`, `connects_to` ilişkisinde **hedef** değişince **kaynak**; `part_of` ilişkisinde **parça** (kaynak)
değişince **bütün** (hedef) etkilenir. Genişlik öncelikli, döngü güvenli (her CI bir kez, en kısa yoldan);
kökler etkilenenler listesinde yer almaz. `ImpactAnalysis`: `root_ci_ids`, `affected[]` (`ci`, `depth`,
`path` kökten CI'a id listesi, `relationship`), `highest_criticality` (kökler + etkilenenler, en küçük sayı),
`business_services` (`ci_class=service` olan kök/etkilenenler). `max_depth` 1–20.

HTTP: `GET/POST /cmdb/cis`, `GET/PATCH /cmdb/cis/{id}` (`ETag` / `If-Match`), `GET /cmdb/cis/{id}/relationships`,
`GET /cmdb/cis/{id}/impact?max_depth=`, `POST /cmdb/impact {ci_ids, max_depth}`, `POST /cmdb/relationships`,
`DELETE /cmdb/relationships/{id}`. Hatalar: bulunamadı 404, sürüm/yinelenen 409, doğrulama 422.

## Değişiklik yönetimi — `serviszeka.services.changes`

Modeller: `ChangeRequest` (`CHG0000001`, `change_type`, `state`, `risk_level`, `risk_score` 0–100, planlar,
`planned_start/end` saat dilimli, `requested_by`, `assignment_group`, `assignee`, `affected_cis`
(`change_affected_cis` tablosu; okumada `affected_ci_ids`), `linked_incident_ids`, `template_id`,
`implementation_outcome`, `review_notes` (PIR), `version`), `ChangeApproval` (değişiklik başına onaylayan
başına tek karar; `approver_roles` karar anındaki roller), `StandardChangeTemplate`, `ChangeFreezeWindow`
(`environments` boş = tüm ortamlar). `AIDecision.change_id` değişiklik kararlarını bağlar.

### Durum makinesi

```
draft ─► assess ─► authorize ─► scheduled ─► implement ─► review ─► closed
            │                       ▲             │          ▲
            └── (uygun standart) ───┘             └► failed ─┘
authorize ─► rejected (tek red otomatik)      draft/assess ─► cancelled (talep eden)
assess ─► draft (yeniden çalışma)             authorize/scheduled ─► cancelled (change_manager)
```

| Geçiş | Kim | Ön koşul |
|---|---|---|
| `draft → assess` | talep eden / change_manager | uygulama + geri dönüş planı + planlanan pencere (**tüm türler**). Risk hesaplanır (`max(hesaplanan, mevcut)`); uygun olmayan standart → `normal` (`change.converted_to_normal`) |
| `assess → authorize` | change_manager | yalnızca normal/acil |
| `assess → scheduled` | talep eden / change_manager | yalnızca hâlâ uygun standart (etkin şablon, risk ≤ `max_risk`, tüm CI sınıfları izinli) |
| `authorize → scheduled` | change_manager | normal: ≥ `SZ_CAB_MIN_APPROVALS` (varsayılan 2) onay ve 0 red; acil: change_manager/admin rolünde ≥ 1 onay. Çakışma yok |
| `scheduled → implement` | change_manager / uygulayıcı (`assignee`, yoksa talep eden) | `planned_start ≤ şimdi < planned_end`; yetki (onaylar ya da standart uygunluk) ve çakışmalar **yeniden** denetlenir |
| `implement → review / failed`, `failed → review` | change_manager / uygulayıcı | `implementation_outcome` succeeded/failed |
| `review → closed` | change_manager | acil **veya** başarısız değişiklikte `review_notes` (PIR) zorunlu |

Her geçiş `version`'ı artırır ve `change.transition` denetim kaydı (`from`, `to`, `version`, notlar) yazar.
`"ai:"` önekli aktör hiçbir geçiş/onay yapamaz (`ChangeForbiddenError`). Taslak dışında düzenleme yapılamaz.

### Fonksiyonlar

| Fonksiyon | Açıklama |
|---|---|
| `create_change(session, ChangeCreate, user)` | Standart: etkin `template_id` zorunlu, boş planlar şablondan dolar. Bilinmeyen CI/olay/grup → `ChangeValidationError` |
| `get_change`, `list_changes(session, state=, change_type=, ci_id=, requested_by=, limit=, offset=)` | |
| `update_change(session, id, ChangeUpdate, user, expected_version=)` | Yalnızca taslak; talep eden ya da change_manager |
| `transition(session, id, to_state, user, *, expected_version=, notes=, review_notes=, now=, settings=)` | `now` testler için (uygulama penceresi) |
| `record_approval(session, id, user, decision, comment="", *, settings=)` | Yalnızca `authorize` durumunda; cab/change_manager; talep eden ve assignee asla. Red → `rejected` |
| `list_approvals`, `approval_status(session, id, *, settings=)` → `ApprovalStatus(required, approvals, rejections, satisfied)` | Acilde yalnızca change_manager onayları sayılır |
| `find_conflicts(session, id)` → `[ScheduleConflict(kind, ref_id, ref_number, start, end, description)]` | `kind`: `freeze_window` (acil hariç; ortam kesişimi, CI'sız değişiklikte her pencere) / `change` (aynı CI'da çakışan `scheduled`/`implement`) |
| `calendar(session, start, end)` → `Calendar(changes, freeze_windows)` | Taslak/iptal/red hariç |
| `compute_risk(session, id)` → `RiskAssessment(score, level, highest_criticality, affected_count, business_services, factors)` | Aşağıya bakın |
| `propose_ai_risk(session, id, *, risk_score, confidence, rationale, model, settings=)` → `AIDecision` | Yapay zekâ risk önerisi (aşağıda) |
| `approve_ai_decision(session, decision_id, user)`, `get_decision`, `list_decisions(session, change_id)` | change_manager; bayat karar → `StaleDecisionError` |
| `create_template`, `update_template`, `get_template`, `list_templates(session, active=)` | |
| `create_freeze_window`, `list_freeze_windows(session, start=, end=)`, `delete_freeze_window` | |

Hatalar: `ChangeNotFoundError` (404), `ChangeValidationError` / `InvalidTransitionError` (422),
`ChangeForbiddenError` (403), `VersionConflictError`, `ApprovalError`, `DuplicateApprovalError`,
`StaleDecisionError` (409), `ScheduleConflictError(conflicts)` (409, gövde
`{"detail": {"message", "conflicts": [...]}}`).

### Risk skoru (deterministik, 0–100)

Tür tabanı (standart 5, normal 15, acil 30) + etkilenen/kök CI'ların en yüksek kritikliği (1: 35, 2: 25,
3: 10, 4: 0) + kökte prod CI (10) + etki alanı (etkilenen CI başına 3, en fazla 15) + iş hizmeti etkileniyor (5)
+ test planı yok (5) + geri dönüş planı yok (10). Seviye: < 30 low, < 55 medium, < 75 high, ≥ 75 critical.

### Politika ve yapay zekâ (`serviszeka.policy`)

| Eylem | `max_auto_risk` | Davranış |
|---|---|---|
| `change.assess_risk` | `low` | Yalnızca risk skoru/seviyesi. Riski **yükselten** öneri low risk → gölge mod kapalı ve güven ≥ eşik ise uygulanır; **düşüren** öneri high risk → change_manager onayı. Yalnızca `draft`/`assess` durumunda; durum asla değişmez. Yükselme standart değişikliği uygunsuz kılarsa `normal`'e çevrilir |
| `change.execute` | `None` | Asla otomatik değil |
| `change.approve` | `None` | Asla otomatik değil; ayrıca servis `ai:` aktörlerinin onayını reddeder |

Değişiklik ajanı yazacaklar için: LLM'i `serviszeka.llm` ile çağırın (maskeleme dahil), sonra
`changes.propose_ai_risk(...)` ile sonucu kaydedin; bu fonksiyon `policy.evaluate` + `AIDecision` +
`audit.record` zincirini uygular. Denetim eylemleri: `ai_decision.change_risk`, `change.risk_update`,
`ai_decision.approve`, `ai_decision.approve_rejected`.

Hazır uygulama: `agents/change_risk.py` (`POST /changes/{id}/assess`, agent/change_manager) — ayrıntı ve
formül README'nin "Faz 2 kapısı" bölümünde ve modül belgesinde. `SZ_LLM_PROVIDER=fake` (geliştirme/E2E) bu ajan
için deterministik skordan türetilmiş anlatı + kontrol listesi döndürür (düzeltme 0).

Eşzamanlı aynı onaylayan: iki istek varlık denetimini birlikte geçse bile `(change_id, approver)` tekil kısıtı
ikinciyi durdurur; servis bunu `DuplicateApprovalError` (409) olarak bildirir, ilk oy korunur.

### HTTP uçları (`/changes`)

`GET/POST /changes` (`?state=&change_type=&ci_id=&mine=`), `GET/PATCH /changes/{id}` (`ETag`/`If-Match`),
`POST /changes/{id}/transition {to_state, expected_version?, notes?, review_notes?}` (`If-Match` da olur),
`GET/POST /changes/{id}/approvals` (`GET` → `{status, items}`; `POST {decision, comment}`),
`GET /changes/{id}/conflicts`, `GET /changes/{id}/impact`, `GET /changes/{id}/decisions`,
`POST /changes/decisions/{decision_id}/approve`, `GET /changes/calendar?from=&to=` (saat dilimli ISO 8601),
`GET/POST /changes/templates`, `PATCH /changes/templates/{id}`, `GET/POST /changes/freeze-windows`,
`DELETE /changes/freeze-windows/{id}`. Saat dilimsiz zaman girdisi 422 döner.

## Göç

`migrations/versions/0005_cmdb_change_enablement.py`: yeni tablolar + `ai_decisions.change_id`. Enum sütunları
PostgreSQL'de adlı tip değil VARCHAR'dır (`native_enum=False`). Tablo/indeks yalnızca yoksa kurulur, sütun
yalnızca yoksa eklenir (`create_all` ile kurulmuş veritabanında da `alembic upgrade head` çalışır). Not: SQLite
için çevrimdışı (`--sql`) üretim, `ai_decisions`'a FK eklemek tablo kopyalama gerektirdiğinden desteklenmez
(PostgreSQL'de sorun yok).

`migrations/versions/0006_incident_cis_cmdb_external_refs.py`: `incident_cis` (olay ↔ CI) ve
`cmdb_external_refs` (harici CMDB eşlemesi; `(system, entity_type, external_id)` tekil). Aynı idempotent üslup.

## Olay ↔ CI bağı ve triage etki kanıtı

- `services.incidents.set_affected_cis(session, incident_id, ci_ids, actor, expected_version=None)` etkilenen CI
  kümesini değiştirir (verilen küme eskisinin yerini alır). Bağ değişince olay sürümü artar ve
  `incident.cis.update` denetim kaydı yazılır; böylece eski bağa dayanan, onay bekleyen yapay zekâ önerisi
  bayatlar (onay → 409 "yeniden triage gerekli"). Kapalı/iptal olayda değiştirilemez; bilinmeyen CI → 422.
- HTTP (agent/admin): `GET /incidents/{id}/cis` → `CIRead[]`, `PUT /incidents/{id}/cis` `{ci_ids, expected_version?}`
  (`If-Match` desteklenir) → `IncidentRead`. `IncidentRead.ci_ids` her olayda döner; self-servis kullanıcıya her zaman `[]`.
- **Triage** (`agents.service_desk.triage`): olaya bağlı CI varsa maskelenmiş CMDB bağlamı (ad, sınıf, ortam,
  kritiklik, durum, kısa açıklama + etki analizindeki prod iş hizmetleri) LLM istemine ve karar girdisine eklenir.
  Ardından deterministik **taban kuralı** uygulanır (yalnızca `kind=incident` önerisinde, yalnızca yükseltir):
  - prod ortamında, emekli olmayan, kritiklik 1 bağlı CI → etki en az `medium`;
  - etki analizinde (derinlik 5, kökler dahil) prod ve emekli olmayan **≥ 2 iş hizmeti** → etki `high`.
  Yükseltilen etki mevcut öncelik politikasından aynen geçer: P3..P5 bandı içinde otomatik uygulanabilir, P1/P2
  yükseltmesi her zaman insan onayı ister (P1/P2'de nöbetçi uyarılır). Kararda `output.llm_impact` ve
  `output.cmdb = {ci_ids, business_service_ids, impact_floor, floor_reasons, floor_applied}` saklanır.

## ServiceNow CMDB çekimi — `serviszeka.integrations.servicenow_cmdb`

- `ServiceNowCMDBConnector` `/api/now/table/cmdb_ci` (`sys_class_nameIN<seçili sınıflar>`) ve
  `/api/now/table/cmdb_rel_ci` okur; sayfalı, `ORDERBYsys_updated_on`, artımlı `sys_updated_on>=<filigran>`.
- Eşleme: sınıf (`CLASS_MAP`, ör. `cmdb_ci_service` → service, `cmdb_ci_appl` → application, sunucu/DB/ağ/bulut
  alt sınıfları), `install_status` → durum (7/8/100 → retired), `environment`/`used_for` → ortam (bilinmeyen →
  ihtiyatla prod), `busines_criticality`/`business_criticality` baş rakamı → kritiklik (yoksa 3), `support_group`
  yalnızca taksonomi `assignment_group` anahtarıyla eşleşirse sahip ekip olur (ham ad `attributes`'ta). İlişki
  türleri: Depends on/Uses → depends_on, Runs on/Hosted on/Virtualized by → runs_on, Connects to → connects_to,
  Contains/Members → part_of (yön çevrilir: çocuk part_of ebeveyn); diğerleri atlanır.
- `pull_cmdb(session, connector, actor=..., full=False)`: CI'lar `cmdb_external_refs` üzerinden `sys_id` ile
  upsert edilir (eşleme yoksa aynı ad + ortamdaki, başka bir ServiceNow kaydına bağlı olmayan yerel CI
  sahiplenilir; bağlıysa kayıt atlanır ve `errors`'a yazılır). Yazmalar `services.cmdb` üzerinden (doğrulama,
  sürüm, `system:cmdb_sync` adına denetim); yerel öznitelikler korunur, değişmeyen kayıt sürüm artırmaz.
  İlişkiler iki ucu da eşlenmiş CI'lar arasında içe alınır. Özet `integration.cmdb_pull` denetim kaydı.
- HTTP: `POST /integrations/servicenow/cmdb/pull?full=false` (admin) → `{system, full, since, cis, relationships, errors}`;
  yapılandırılmamışsa 400, ServiceNow hatasında 502 (hiçbir şey yazılmaz).
- Sınırlar: uzakta silinen ilişki/CI yerelde silinmez (emekliye ayrılan CI `retired` olur); `cmdb_ci` temel tablosu
  alt sınıf alanlarını (ör. `cmdb_ci_service.busines_criticality`) döndürmeyebilir, o durumda kritiklik 3 kalır;
  sonradan eşlenen CI'ya ait önceden atlanmış ilişkiler için `full=true` çekim gerekir.

**Jira Service Management:** CMDB (Assets) ücretli bir eklenti/plan özelliğidir. Yalnızca arayüz taslağı vardır:
`integrations/cmdb_base.CMDBSourceConnector` protokolü ve `integrations/jira_assets.JiraAssetsCMDBConnector`
(her çağrı `NotImplementedError`). Sahte bir uygulama yoktur; HTTP ucu da yoktur.

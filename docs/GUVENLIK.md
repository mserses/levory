# ServisZeka — güvenlik

Tehdit modeli özeti, yapılan güvenlik incelemeleri ve kalan riskler. Kurulum
[`KURULUM.md`](KURULUM.md), işletim [`ISLETIM.md`](ISLETIM.md). Kapı testleri yalnızca genişletilir:
`tests/test_gate_no_unapproved_risk.py` (bölüm 1-9), `tests/test_gate_review_regressions.py`,
`tests/test_auth.py::test_every_api_route_requires_auth`.

## 1. Tehdit modeli

**Korunan varlıklar:** kimlik dizini (grup üyeliği, parola, MFA, hesap durumu), olay/değişiklik/
talep kayıtları ve içlerindeki kişisel veri, onay kararları, denetim zinciri, bağlayıcı gizlileri.

**Aktörler ve güven sınırları:**

| Aktör | Kimlik | Güven |
|---|---|---|
| İnsan kullanıcı | OIDC erişim belirteci (RS256/ES256, `iss`/`aud`/`exp`) | Rolü kadar; aktör adı yalnızca belirteçten (`X-Actor` gibi başlıklar yok sayılır) |
| Yapay zekâ ajanı (iç) | Kod içi aktör `ai:<ajan>` (`ai:fulfilment`, `ai:problem`, `ai:events`, `ai:csi`) | Güvenilmez öneren; yalnızca araç boru hattından yazar |
| MCP istemcisi | Client credentials belirteci + `ai_agent` rolü | Güvenilmez; `ai:<istemci>` + yalnızca `ai_agent` rolüne indirgenir |
| İzleme kaynağı | Paylaşılan gizli ya da HMAC | Yalnızca uyarı verisi; olay açma kararı ajanda ve politikada |
| Dış sistemler (Graph, Keycloak, Slack, SMTP, ServiceNow, Jira, LLM) | Bağlayıcı gizlileri | Yanıtları veri olarak işlenir; bağlayıcı politika/onay vermez |
| Kayıt içeriği (olay metni, uyarı açıklaması) | — | Saldırgan denetiminde olabilir (istem enjeksiyonu) |

### 1.1 Yapay zekâ eylemleri

| Tehdit | Kontrol |
|---|---|
| Ajanın politikayı atlayıp yazması | Her yapay zekâ eylemi `policy.evaluate` → `AIDecision` → `audit.record`; ajan modülleri AST ile taranır (yazan modülde üçü de olmalı, uygulama öncesi `AUTO_APPLIED` denetimi) — kapı bölüm 2 |
| Düşük güvenle / gölge modda uygulama | Gölge mod açıkken hiçbir şey uygulanmaz; güven < `SZ_AI_CONFIDENCE_THRESHOLD`, NaN/±sonsuz ya da aralık dışı güven otomatik uygulanmaz (kapı bölüm 1, kapsamlı ızgara) |
| Riskin yapay zekâca düşürülmesi | P1/P2 ve her öncelik düşürme insan onayı; değişiklik riskini düşürme `change_manager` onayı; `change.execute/approve`, `incident.resolve` asla otomatik |
| Yapay zekânın onay vermesi | `ai:` aktör ve `ai_agent` rolü hiçbir karar/eylem isteği/CAB/iş onayı/CSI onayı veremez (servis düzeyinde reddedilir) |
| Bayat veri üzerine karar | Öneri sonrası değişen kayıtta onay 409; yapay zekâ/MCP kayıt yazmalarında `expected_version` zorunlu |
| Uydurma ölçüm | CSI tabanı yalnızca KPI'dan; yapay zekâ taban veremez; KPI/SLA modülleri salt okunur (statik tarama) |

### 1.2 Onaylar ve R0-R3

| Sınıf | Yapay zekâ | Onay kuralı |
|---|---|---|
| R0 okuma | Serbest (`tool.read` denetimi); admin-yalnız araçlar (denetim) yapay zekâya kapalı | — |
| R1 düşük riskli yazma | Yalnızca gölge kapalı + güven ≥ eşik; aksi halde eylem isteği | 1 onay |
| R2 | Asla doğrudan; eylem isteği | 1 insan onayı |
| R3 | Asla doğrudan; eylem isteği | 2 farklı onaylayan **ya da** CAB onaylı bağlı değişiklik + 1 onay |

- Koşullu risk yalnızca yükseltir (`max(temel, risk_fn)`): ayrıcalıklı/bilinmeyen/izin listesi dışı
  gruba ekleme R3; başkası adına, talepsiz ya da devre dışı hesapta parola sıfırlama, kilit açma,
  grup ekleme, lisans atama R2.
- Eylem isteği: talep eden ve yapay zekâ onaylayamaz (kimlik büyük/küçük harf ve boşluk duyarsız
  karşılaştırılır), kişi başına tek karar, `SZ_ACTION_REQUEST_TTL_HOURS` sonunda düşer. Yürütme
  anında argüman özeti (`args_hash`) onaylananla aynı olmalı, argümanlar yeniden doğrulanır, risk
  yeniden çözülür (yükseldiyse yeni sınıfın onay kuralı aranır); işleyici **son onaylayanın**
  kimliği ve rolleriyle çalışır, alan servisinin kuralları (CAB, ECAB, P1/P2) aynen geçerlidir.
- İşleyicilere yalnızca `tools.execute_tool` ve `tools.actions` ulaşır (statik tarama). İşleyici
  SAVEPOINT'te çalışır; hata kısmi yazmaları ve bekleyen bildirimleri geri alır.
- Değişiklik: talep eden/uygulayıcı kendi değişikliğine oy veremez; zamanlama ve uygulama anında
  onay, standart uygunluk (risk CMDB'den yeniden) ve çakışma (CI satır kilidi) yeniden denetlenir;
  etki analizi kesilirse en kötü durum sayılır.
- Hizmet talebi: iş onayı risk onayından önce; yapay zekâ, talep eden ve açan veremez.

### 1.3 MCP

- `/mcp` OpenAPI dışıdır ama REST ile aynı belirteç doğrulamasını yapar; belirteçsiz/geçersiz 401.
- `ai_agent` taşıyan belirteçteki diğer roller yok sayılır; `ai_agent` REST insan uçlarında 403.
- `tools/list` yalnızca çağıranın kullanabileceği araçları ve risk sınıfını gösterir; yapay
  zekâ yalnızca yönetici olmayan bir insan rolünün de çağırabildiği araçları çağırır.
- Yapay zekâya dönen sonuçlar PII maskelidir; `confidence` verilmezse 0 sayılır (R1 onaya düşer);
  argüman modelleri `extra="forbid"`; idempotency anahtarı aktöre özeldir.

### 1.4 İzleme webhook'ları

- `SZ_EVENTS_WEBHOOK_SECRET` boşsa kapalı (401). Paylaşılan gizli ya da HMAC-SHA256
  (`<ts>.<gövde>`), `hmac.compare_digest`; imzalıda zaman damgası penceresi; teslimat kaydı iki
  pencere saklanır (yeniden oynatma).
- Gövde okunmadan belirteç denetimi (kimliksiz istemci 1 MiB okutamaz), gövde sınırı (413), aşırı
  iç içe JSON ve şema hatası 422, en çok 500 uyarı / 64 etiket.
- Etiketler maskelenir, gizli görünümlü anahtarlar `***`. Olay arayanı `system:izleme` (kullanıcı
  adıyla çakışmaz). Grup başına tek olay, saatlik olay sınırı, P1/P2 açmak R2, olay asla otomatik
  çözülmez.

### 1.5 Bağlayıcılar

- Gizliler `SZ_` ayarlarından; Graph/Keycloak/SMTP/Slack/webhook gizlileri `SecretStr` (`repr`'de
  ve hata mesajlarında görünmez). Keycloak hatası yalnızca HTTP kodu + `error` kodunu, SMTP hatası
  yalnızca istisna türünü taşır; kuyruk hata metninden URL ve `token=`/`password=` biçimli gizliler
  ayıklanır.
- Geçici parola hiçbir sonuçta, denetimde, eylem isteğinde ya da Slack'te yer almaz; yalnızca
  ikincil adrese (Graph `otherMails`, Keycloak yönetici-yalnız öznitelik + SMTP) gider.
- Grup başvuruları tam eşleşmeyle çözülür (NFKC + casefold; bulanık yok), belirsiz başvuru yazma
  yapmaz ve ayrıcalıklı sayılır; Graph yol parçaları kaçışlanır.
- Slack metni kaçışlanır (`<!channel>`, `<@U…>`, bağlantı biçimi çalışmaz; `@channel` bozulur),
  bağlantı önizlemeleri kapalı. SMTP üretimde TLS zorunlu; başlık enjeksiyonu reddedilir.

### 1.6 Portal

Authorization Code + PKCE; belirteç `sessionStorage`'da (localStorage yok); sıkı CSP
(`'unsafe-inline'` yok), yalnızca `/auth/silent` aynı kökenden çerçevelenir; sunucu ve yapay zekâ
metni düz metin (`dangerouslySetInnerHTML` yasak); dönüş yolu yalnızca uygulama içi (açık
yönlendirme yok).

## 2. Yapılan incelemeler ve düzeltilen sorunlar

Her bulgu önce başarısız bir regresyon testiyle yakalandı, sonra düzeltildi.

### Faz 0/1 bağımsız inceleme — 14 hata (`7971060`, `tests/test_review_regressions.py`)
- Politika motoru NaN/sonsuz güveni otomatik uyguluyordu → geçersiz güven asla otomatik değil.
- Triage P1'i yapay zekâyla düşürebiliyordu → büyük olay düşürülemez.
- Eşzamanlı yazımda denetim zinciri çatallanabiliyordu → PostgreSQL danışma kilidi, tekil zincir.
- İyimser kilit bayat oturum verisine bakıyordu; durum değişiminde öncelik yeniden hesaplanıyordu.
- Jira JQL saat dilimi kayması (UTC batısında güncellemeler kaçıyordu).
- Başında 0 olmayan cep telefonu maskelenmiyordu.
- Diğerleri: gelen öncelik değişiminde SLA yeniden hesaplanması, kapalı olayda triage çökmesi,
  denetim/senkron zamanlarının UTC olması, çakışma çözümünde bağlayıcı hatasının 502 dönmesi.

### OIDC ve onay incelemesi (`d01e70c`)
- Aktör/onaylayan belirteçten; `X-Actor` yok sayılır. Yalnızca RS256/ES256; `none`/`HS*` red.
- Bozuk `alg` başlığı 500 yerine 401; JWKS hata geri çekilmesi (IdP'yi boğmama).
- Sürümsüz/bayat öneri onayı 409; flush edilmiş işler bildirimle commit edilmiyor; bildirim yalnızca
  başarılı commit sonrası. Kullanıcı adında `upn`, değiştirilebilir `email`'den önce.

### Portal (`4c25da6`)
- `requester` yalnızca kendi kayıtlarını görür, yapay zekâ iç ayrıntısı görmez, arayanı taklit
  edemez; sahte LLM yalnızca geliştirmede; sıkı CSP'li nginx imajı.

### Faz 2 incelemesi — 9 düzeltme (`10941ac`, `tests/test_gate_review_regressions.py`)
- Zamanlama ve uygulamada risk CMDB'den yeniden hesaplanır (CI sonradan kritik olursa standart
  değişiklik uygulanamaz); CI'sız standart değişiklik CAB'ı atlayamaz.
- Büyük/küçük harf ve boşluk varyantıyla kendi değişikliğini onaylama ve aynı kişinin çift oyu
  engellendi; onaylı değişiklik düzenlenemez/taslağa dönemez.
- Aynı CI'ya eşzamanlı zamanlama CI satır kilidiyle önlendi; etki analizine düğüm sınırı (kesilirse
  en kötü durum); değişiklik kararları olay onay kutusunda görünmez.

### Faz 3 kapı ölçümü ve güvenlik incelemesi (`7a2e9ff`, `5bb78fd`, `tests/test_review_fulfilment_connectors.py`)
- Başkası adına açılan kilit açma talebi onaysız (R1) yürüyordu → `identity.unlock_account`
  koşullu R1/R2 (kapı ölçümü yakaladı).
- Başarısız ya da başkasının talebini iptal etmek onu kapı paydasından düşürüp oranı şişiriyordu
  (yeniden deneme sonrası iptal dahil).
- Onaydan sonra değiştirilen eylem argümanları yürütülüyordu → `args_hash` uyuşmazlığında yürütme
  yok.
- Ayrıcalıklı grup listesi büyük/küçük harf, tam genişlik karakter ve boşluk varyantıyla
  atlatılabiliyordu (R3 → R1).
- Keycloak: başka kullanıcının kimliğine eşit kullanıcı adı belirsiz sayılır; kimlikle tanımlı
  ayrıcalıklı grubun alt grupları da ayrıcalıklı.
- Graph: `.`/`..` yol parçaları kaçışlanır; istemci gizlisi `SecretStr`.
- Ürün sahibi kararları: yapay zekâ admin-yalnız araçları çağıramaz; başkası adına/talepsiz/devre
  dışı hesapta grup ekleme ve lisans atama R2.

### Faz 4 karşı inceleme — 12 düzeltme (`1762f58`, `tests/test_faz4_review_regressions.py`)
- Webhook: ileri tarihli imzalı teslimat tekrar kaydı silindikten sonra yeniden oynatılabiliyordu
  (kayıt iki pencere); aşırı iç içe JSON 500 → 422; yanlış gizliyle gövde okunuyordu (önce belirteç);
  her grup için tüm numaraların okunması (tek sorgu).
- Olay spamı: fırtınada sınırsız olay (saatlik sınır); çözülen grup yeniden ateşlenince ikinci olay
  (aynı olay yeniden açılır).
- `izleme` adlı self-servis kullanıcı tüm izleme olaylarını görürdü (`system:izleme`).
- SLA tahmini: geriye dönük sınamada sonuca göre seçim ve geçmiş an için gelecek veri kullanımı.
- CSI: yapay zekâ taban değer uydurabiliyordu. KPI/SLA API'sinde tarih taşması 500 → 422.
- Zamanlayıcı kirası iş zaman aşımına eşitti (çift çalıştırma) → zaman aşımı + 60 sn.

### Faz 4 açık konular (`tests/test_faz4_open_items.py`)
- SMTP: TLS'siz bağlantı yalnızca geliştirmede ve açık bayrakla; STARTTLS girişten önce, düşürme
  yok; başlık enjeksiyonu içerik sızdırmadan reddedilir.
- Yapay zekâ / MCP kayıt yazmalarında `expected_version` zorunlu (`If-Match` yalnızca yapay zekâ
  aktörü için zorunlu).
- Bildirim kuyruğu saklama süresi (SLA tekilleştirme satırları korunur) ve Slack nöbetçi
  bildiriminde yeniden denemede yineleme denetimi.

## 3. Bilinen kalan riskler

| # | Risk | Etki | Azaltım / öneri |
|---|---|---|---|
| K-1 | Bağlayıcılar gerçek kiracıda/sunucuda sınanmadı (Graph 1-7, Slack 8-13, Keycloak K1-K11, OIDC O1-O4, ServiceNow/Jira yetkileri) | Beklenmeyen hata ya da fazla yetki | Test ortamında doğrulayın; en az yetki (KURULUM.md §6) |
| K-2 | İstem enjeksiyonu: olay/uyarı metni ajanı ya da MCP istemcisini yönlendirebilir | Gölge kapalıyken R1 eylemler (atama, sınıflandırma, standart gruba ekleme, kanal mesajı) onaysız çalışabilir | R2/R3 her zaman insan; standart grup listesini dar tutun; ilk dönem gölge mod; `ai:%` denetimini izleyin |
| K-3 | Self-servis doğrulaması yalnızca oturum kimliğine dayanır (MFA adım yükseltme yok) | Ele geçirilmiş oturum kendi hesabında R1 parola sıfırlama/kilit açma yapabilir; geçici parola ikincil adrese gider | IdP'de koşullu erişim/MFA; ikincil e-posta özniteliği yönetici-yalnız (K11) |
| K-4 | Denetim zinciri anahtarsız SHA-256'dır: zincirin **sonundan silme** ya da tümüyle yeniden hesaplama `verify` ile yakalanmaz | Veritabanı yöneticisi iz silebilir | Son halkayı günlük olarak dış WORM depoya yazın (ISLETIM.md §4); DB erişim günlüğü |
| K-5 | Bazı gizliler düz metin türünde: `SZ_DATABASE_URL`, `SZ_ANTHROPIC_API_KEY`, `SZ_SERVICENOW_PASSWORD`, `SZ_JIRA_API_TOKEN`, `SZ_ONCALL_WEBHOOK_URL`, `SZ_TEAMS_WEBHOOK_URLS` | Ayar nesnesi günlüğe/hata izine düşerse sızar | `SecretStr`'e taşınmalı; gizli yönetici + günlük erişim denetimi |
| K-6 | Uygulamada hız sınırlama yok; `/docs`, `/openapi.json`, `/health` (sürüm) kimliksiz | Kaba kuvvet / DoS, keşif | Ters vekilde sınır ve erişim kuralı (KURULUM.md §8) |
| K-7 | JWT iptali yok: devre dışı kullanıcı/ajan belirteci `exp`'e kadar geçerli | Kısa süreli yetkisiz erişim | Kısa erişim belirteci ömrü; acil durumda vekilde `/mcp` kapatma |
| K-8 | Webhook gizlisi tek ve tüm kaynaklarda ortak; paylaşılan gizli kipinde zaman damgası yok | Sızan gizliyle sahte uyarı (P1/P2 yine onaya) | HMAC kipi, TLS, düzenli döndürme |
| K-9 | KVKK: ad-soyad, kullanıcı adı, adres ve serbest metin maskelenmez; veri yurt dışındaki LLM'e gider; kayıtlar için otomatik saklama/imha yok; denetim zincirinden silme doğrulamayı bozar | Uyum riski | ISLETIM.md §7; hukuki değerlendirme, saklama politikası |
| K-10 | Bildirim teslimi en az bir kez | Yineleme denetlemeyen alıcıda çift uyarı | Alıcıda `Idempotency-Key` ile ayıklama |
| K-11 | Zamanlayıcı kiraları ve imza penceresi saate dayanır | Saat kaymasında çift iş / reddedilen webhook | NTP |
| K-12 | Göçler CI'da yalnızca SQLite'ta sınanır | PostgreSQL'e özgü göç hatası | Üretim kopyasında prova (KURULUM.md §10) |
| K-13 | Entra'da bulutta kilit açma ve paylaşılan posta kutusu izni yok; Keycloak'ta posta/lisans yok | İlgili talepler insana düşer | Beklenen davranış (`failed`); ilgili katalog öğelerini kapatın |
| K-14 | Eylem isteğinin ham argümanları (`args`) veritabanında maskesiz | DB erişimi olan kişisel veriyi görür | DB erişimini kısıtlayın; gizli bilgi argüman olarak tasarlanmaz |
| K-15 | ServiceNow / Jira bağlayıcı hataları uzak yanıtın ilk 300 karakterini taşır | Uzak sistemin hata gövdesindeki veri günlüğe/admin yanıtına düşebilir | Entegrasyon kullanıcısının yetkisini dar tutun; günlük erişimini kısıtlayın |

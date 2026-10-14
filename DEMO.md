# ServisZeka demosu — 10 dakikalık rehberli tur

Bu demo, ServisZeka'yı **tek komutla** ayağa kaldırır ve gerçekçi (ama **SENTETİK**) Türkçe verilerle
doldurur: her ITIL 4 uygulamasını ve yapay zekâ onay akışlarını ilk açılışta görebilirsiniz.

> **Yalnızca demo / geliştirme içindir.** Kimlik doğrulaması imzasızdır (`dev:<kullanıcı>:<roller>`),
> LLM sahte ve deterministiktir (API anahtarı gerekmez, ağ çağrısı yok), kimlik/posta/lisans
> bağlayıcıları bellek içi sahte dizindir. Demo tohumu `SZ_ENVIRONMENT=development` dışında
> çalışmayı reddeder; demo verisi içermeyen dolu bir veritabanına da yüklenmez (gerçek kayıtlarla
> karışmaz). Docker portları yalnızca bu makineye (`127.0.0.1`) açılır. Ekrandaki sayılar gerçek
> bir kurumdan gelmez.

## Başlatma

**Docker ile** (PostgreSQL 16 + API + nginx portal):

```bash
docker compose -f docker-compose.demo.yml up --build
# Portal: http://localhost:8080/giris   API: http://localhost:8000/docs
# Sıfırlamak için: docker compose -f docker-compose.demo.yml down -v
```

**Docker'sız** (SQLite + uv + npm; Python 3.11+, uv ve Node 22 gerekir):

```bash
./scripts/demo.sh                 # Portal: http://localhost:5173/giris   API: http://localhost:8000/docs
./scripts/demo.sh --check         # duman testi: başlatır, /health ve portalı doğrular, durdurur
DEMO_RESET=1 ./scripts/demo.sh    # demo veritabanını (.demo/) silip baştan yükler
```

Her iki yolda da arka uç geliştirme kipinde çalışır: dev kimlik doğrulaması, sahte LLM
(`SZ_LLM_PROVIDER=fake`), sahte dizin (`SZ_IDENTITY_PROVIDER=memory`), gölge mod kapalı (R1 eylemler
uygulanır, R2/R3 her zaman insan onayına düşer) ve **arka plan zamanlayıcısı açık** (SLA uyarıları,
eylem isteği süre aşımı, günlük sürekli iyileştirme incelemesi açılışta bir kez çalışır).

Veri yalnızca bir kez yüklenir: `uv run python -m serviszeka.demo_seed` (compose ve betik bunu
kendisi çalıştırır) ikinci çalıştırmada hiçbir şey eklemez. Onay bekleyen eylem istekleri 24 saatte
düşer; ertesi gün tura baştan başlamak için yukarıdaki sıfırlama komutunu kullanın.

## Kullanıcılar

Portalın giriş sayfasında (`/giris`) kullanıcı adını yazın ve rolleri işaretleyin. Kullanıcı
değiştirmek için sağ üstten çıkış yapın.

| Kullanıcı | Roller | Kim? |
|---|---|---|
| `zeynep` | requester | Son kullanıcı (Zeynep Kaya): self-servis olay ve talepler; Ali'nin yöneticisi |
| `ayse` | agent | Hizmet masası uzmanı (Ayşe Yılmaz): olay, sorun, bilgi bankası, izleme |
| `sef.ali` | approver, cab | Onaylayan + CAB üyesi: yapay zekâ önerilerini ve eylem isteklerini onaylar |
| `kurul1` | cab | CAB üyesi (CRM değişikliğine oyunu vermiş) |
| `kurul2` | cab | CAB üyesi |
| `mehmet` | change_manager | Değişiklik yöneticisi (Mehmet Şahin) |
| `yonetici` | admin | Sistem yöneticisi: denetim zinciri, entegrasyonlar |

## Demoda neler var?

Numaralar boş veritabanına yüklenen demo içindir.

- **CMDB:** 26 CI — iş hizmetleri (ERP, CRM, VPN, e-posta, İK portalı), uygulamalar, PostgreSQL
  kümesi `pg-cluster-01`, Kubernetes düğümleri, VPN ağ geçidi, güvenlik duvarı, çekirdek anahtar ve
  bağımlılıkları.
- **Olaylar:** son 60 güne yayılmış 40 kayıt (yanıt/çözüm süreleri, birkaç SLA ihlali, arayan
  beklemesinde duran SLA saati); 8'i açık. `INC0000039` "Tüm şirkette ERP'ye erişilemiyor":
  yapay zekâ P5 → **P1** önerdi, insan onayı bekliyor.
- **Sorunlar:** `PRB0000001` VPN kopmaları (**bilinen hata**, kök neden + geçici çözüm, KEDB
  makalesi, başarısız firmware değişikliği bağlı), `PRB0000002` ay sonu ERP raporları (inceleniyor,
  **yapay zekâ RCA taslağı** insan kabulünü bekliyor), `PRB0000003` yazıcı kuyruğu (yeni).
- **Bilgi bankası:** 7 yayımlanmış makale + 1 taslak + bilinen hata makalesi.
- **Hizmet talepleri:** 6 talep **insansız** karşılandı (şifre sıfırlama, VPN, lisans, yazılım
  grubu, Teams); `REQ0000007` (Ali, paylaşılan posta kutusu) yöneticisi Zeynep'in **iş onayını**,
  `REQ0000008` (Zeynep, MFA sıfırlama) **BT onayını** (R2 eylem isteği) bekliyor.
- **Değişiklikler:** kapanmış başarılı / başarısız (PIR notlu) / standart / acil değişiklikler,
  zamanlanmış Kubernetes yaması, taslak ve **CAB onayı bekleyen** `CHG0000006` CRM 8.2 yükseltmesi
  (yapay zekâ risk değerlendirmesi yapıldı, 1/2 oy); takvimde çeyrek sonu dondurma penceresi.
- **İzleme:** `EVG0000001` — veritabanı kümesi + ERP uygulaması + rapor servisinden gelen üç uyarı
  tek grupta bağıntılandı (kök CI `pg-cluster-01`); olay ajanının P1 olay açma önerisi (R2) bekliyor.
- **Sürekli iyileştirme:** `CSI0000001` Outlook çözüm rehberi — uygulandı, ölçüm penceresi doldu
  (MTTR 10 sa → 3,5 sa). Açılışta zamanlayıcı CSI ajanını (`ai:csi`) çalıştırır: kaydı ölçer ve
  verideki sinyallerden yeni fikirler önerir (ör. `network_ops` MTTR artışı).

## 10 dakikalık tur

### 1. Self-servis — `zeynep` (requester) · ~1,5 dk

1. **Taleplerim**: kendi olaylarınız ve hizmet talepleriniz. Şifre sıfırlama ve VPN taleplerinin
   **insansız** karşılandığını görün; MFA sıfırlama talebi "BT onayı bekliyor".
2. **Onayımı bekleyenler** sekmesi: Ali'nin paylaşılan posta kutusu talebi (`REQ0000007`) →
   **Onayla**. İş onayı risk onayından önce gelir; onaydan sonra karşılama adımları yapay zekâ
   (`ai:fulfilment`) tarafından araç boru hattından çalıştırılır. Posta kutusu izni R2 olduğu için
   adım şimdi **Onaylar**'da BT onayını bekler (3. bölüm).
3. **Hizmet kataloğu** → *Standart yazılım grubu erişimi* → grup `sg-yazilim-standart` → gönder:
   talep saniyeler içinde insansız karşılanır.
4. **Yeni talep**: başlık "Tüm ofiste Wi-Fi çalışmıyor", açıklama "Tüm ofiste kablosuz ağ
   çalışmıyor, toplantılar duruyor." → gönder. (Son kullanıcı etkiyi seçemez; triage belirler.)

### 2. Olay ve sorun yönetimi — `ayse` (agent) · ~2,5 dk

1. **Kayıtlar**: kuyrukta az önce açılan Wi-Fi kaydını açın → **Yapay zekâ ile sınıflandır**.
   Sahte LLM "tüm ofis" + "çalışmıyor" ifadelerinden etki/aciliyet yüksek → **P1** önerir; öncelik
   yükseltmesi otomatik uygulanmaz, karar onaylayana gider (nöbetçi bilgilendirilir). Güven skoru,
   gerekçe ve kaynak bilgi bankası makaleleri kararda görünür; metin LLM'e PII maskeli gider.
2. `INC0000040` (Outlook): yapay zekâ kararı düşük riskli olduğu için **otomatik uygulandı**.
3. **Sorunlar** → `PRB0000001`: bağlı 7 VPN olayı, 5 Neden kök neden analizi, yayımlanmış bilinen
   hata makalesi ve geri alınan firmware değişikliği.
4. `PRB0000002` → **RCA taslağı**: yapay zekâ (`ai:problem`) yalnızca taslak yazabilir →
   **Taslağı olduğu gibi kabul et** (kesin RCA yalnızca insandır).
5. **Bilgi bankası**: "VPN" arayın; bilinen hata makalesi ve geçici çözüm görünür.

### 3. Yapay zekâ onay akışları — `sef.ali` (approver, cab) · ~2 dk

**Onaylar** ekranı:

1. **Yapay zekâ kararları**: `INC0000039` ve Wi-Fi kaydı için P1 önerileri → birini **Onayla ve
   uygula**. Onaylayan her zaman oturumdaki kullanıcıdır; kayıt değiştiyse bayat öneri reddedilir.
2. **Eylem istekleri**:
   - *MFA sıfırlama* (R2, `REQ0000008`) → **Onayla ve yürüt**: araç onaylayanın kimliğiyle
     çalışır, talep karşılanır (insan onayı olduğu için "insansız" sayılmaz).
   - *İzleme grubundan olay aç* (R2, `EVG0000001`) → onaylayın: P1 olay açılır ve uyarı grubuna
     bağlanır.
   - *Paylaşılan posta kutusu izni* (R2, `REQ0000007`; Zeynep'in iş onayından sonra) → onaylayın:
     Ali'nin talebi karşılanır.
   - CSI ajanının önerdiği iyileştirme fikirleri (R1, güven eşiği altında kalanlar onaya düşer).
3. Yapay zekâ ve isteği açan kişi hiçbir isteği onaylayamaz; R3 eylemler ikinci bir onaylayan
   ister.

### 4. Değişiklik yönetimi ve CMDB — `sef.ali` → `mehmet` · ~2 dk

1. `sef.ali` olarak **Değişiklikler** → `CHG0000006` *CRM 8.2 sürüm yükseltmesi*: yapay zekâ risk
   anlatısı ve kontrol listesi, CMDB etki analizi, CAB oyları (1/2, `kurul1`). **Onayla** ile ikinci
   oyu verin (talep eden `mehmet` ve uygulayıcı `ayse` oy veremez).
2. `mehmet` (change_manager) olarak aynı değişiklik → **Zamanla**. Takvim görünümünde zamanlanmış
   Kubernetes yamasını ve çeyrek sonu dondurma penceresini görün; kapanmış acil ve başarısız
   değişikliklerde PIR notları vardır.
3. **CMDB** → `pg-cluster-01`: etki grafiği (ERP uygulaması, rapor servisi, ERP iş hizmeti).

### 5. İzleme, KPI ve SLA tahmini — `ayse` · ~1 dk

1. **Olaylar** → `EVG0000001`: kök CI, CMDB yolu (`pg-cluster-01 → sap-erp-app`), üye uyarılar →
   **Üstlen** (R1, hemen) ya da **Sustur** (R2, onaya gider).
2. **Panolar**: MTTR, MTTA, SLA uyumu, birikim yaşlanması, otomasyon oranı, triage kabulü,
   değişiklik başarısı; önceki döneme göre eğilim (▲/▼). Dönemi 90 güne çevirin.
3. **SLA tahmini**: açık olayların çözüm SLA'sını kaçırma olasılığı ve açıklanabilir gerekçeleri.

İsteğe bağlı — canlı bir izleme uyarısı gönderin (VPN ağ geçidi, kritik):

```bash
curl -s -X POST http://localhost:8000/events/generic \
  -H 'Authorization: Bearer demo-olay-gizli' -H 'Content-Type: application/json' \
  -d '{"events":[{"source":"zabbix","severity":"critical","title":"VPN ağ geçidi CPU %99","labels":{"ci":"vpn-gw-01"}}]}'
```

Yeni grup **Olaylar**'da, olay açma önerisi **Onaylar**'da belirir.

### 6. Sürekli iyileştirme — `sef.ali` · ~0,5 dk

**İyileştirmeler** → `CSI0000001`: taban (KPI'dan hesaplanan, 10 sa), hedef (6 sa), ölçülen değer ve
önce/sonra pencereleri. Durum "Ölçüldü" değilse önce **Ölç**'e basın, sonra **Kapat**. Yapay zekânın
(`ai:csi`) önerdiği fikirlerden birini **Onayla**: onay, başlatma ve kapatma yalnızca insandır.

### 7. Yönetişim — `yonetici` (admin) · ~0,5 dk

**Denetim** → **Zinciri doğrula**: tur boyunca yaptığınız her işlem (yapay zekâ kararları, onaylar,
değişiklik geçişleri) hash zincirine bağlıdır ve zincir bozulmamıştır.

## ITIL 4 uygulamaları — nerede görülür?

| Uygulama | Ekran | Kullanıcı |
|---|---|---|
| Hizmet masası / olay yönetimi | Kayıtlar, Onaylar (triage) | `ayse`, `sef.ali` |
| Hizmet talebi yönetimi | Hizmet kataloğu, Taleplerim | `zeynep` |
| Sorun yönetimi | Sorunlar | `ayse` |
| Bilgi yönetimi | Bilgi bankası | `ayse` |
| Değişiklik etkinleştirme | Değişiklikler (takvim, CAB) | `mehmet`, `sef.ali`, `kurul1`, `kurul2` |
| Hizmet yapılandırma yönetimi | CMDB | `mehmet`, `ayse` |
| İzleme ve olay yönetimi | Olaylar | `ayse` |
| Hizmet düzeyi yönetimi | Panolar, SLA tahmini | `ayse`, `sef.ali` |
| Sürekli iyileştirme | İyileştirmeler | `sef.ali` |
| Bilgi güvenliği / yönetişim | Denetim | `yonetici` |

Ayrıntılı sözleşmeler: [`docs/faz2.md`](docs/faz2.md), [`docs/faz3.md`](docs/faz3.md),
[`docs/faz4.md`](docs/faz4.md).

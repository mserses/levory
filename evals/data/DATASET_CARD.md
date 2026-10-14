# ServisZeka değerlendirme veri seti: veri kartı

**Dosya:** `dataset.jsonl` (200 satır, UTF-8, satır başına bir JSON nesnesi)
**Amaç:** ServisZeka'nın BT kayıtlarını ITIL 4'e uygun sınıflandırmasını (tür, kategori, hizmet, atama grubu, etki, aciliyet) ölçmek.
**Etiket şeması:** `../../src/serviszeka/taxonomy.json` ve `../LABELING.md`

## Alanlar

| Alan | Açıklama |
|---|---|
| `id` | Kayıt kimliği (`T1-001` … `T4-050`; ön ek, kaydı yazan yazar grubunu gösterir) |
| `title`, `description` | Kullanıcının yazdığı başlık ve açıklama |
| `labels` | `kind`, `category`, `service`, `assignment_group`, `impact`, `urgency` (her biri taksonomiden tek değer) |
| `rationale` | Etiketlerin kısa gerekçesi |
| `adjudicated` | Yazar ve kör etiketçi bu kayıtta ayrıştıysa ve karar hakem incelemesiyle verildiyse `true` |
| `edited` | Hakem incelemesinde, belirsizliği gidermek için açıklama metni değiştirildiyse `true` |

## Nasıl oluşturuldu

1. **Sentetik üretim:** Dört yazar (her biri 50 kayıt, `T1`–`T4`) kayıtları gerçek kullanıcı dilini taklit ederek yazdı: kısa, kimi zaman yazım hatalı, kimi zaman duygusal. Kayıtları yazanlar aynı zamanda etiketledi ve gerekçe ekledi. Bazı kayıtlar maskelemeyi sınamak için **sahte** kişisel veri içerir (TC kimlik no, telefon, e-posta). Gerçek kişi veya kurum adı kullanılmadı.
2. **Kör etiketleme:** Her 50'lik parça, yazarın etiketlerini ve gerekçesini görmeyen ayrı bir etiketçi tarafından aynı kurallarla yeniden etiketlendi (4 kör etiketçi).
3. **Uyum ölçümü:** Her alan için yüzde uyum ve Cohen's kappa hesaplandı (aşağıda).
4. **Hakem kararı:** Ayrışan her kayıt, metin ve kurallar yeniden okunarak karara bağlandı. Varsayılan olarak yazar etiketi seçilmedi. Metin gerçekten belirsizse ve iki etiket de açıkça doğru değilse, açıklama en az değişiklikle düzenlendi; böylece yalnızca bir etiket savunulabilir hâle geldi.

## Etiketçiler arası uyum (hakem kararından ÖNCE, n = 200)

| Alan | Uyum | Cohen's κ |
|---|---|---|
| kind | %100,0 | 1,000 |
| category | %100,0 | 1,000 |
| service | %100,0 | 1,000 |
| assignment_group | %100,0 | 1,000 |
| impact | %99,5 | 0,991 |
| urgency | %96,5 | 0,947 |
| **Altı alanın tamamı** | **192 / 200 (%96,0)** | — |

Ayrışmaların tamamı `impact` ve `urgency` alanlarında oldu (1 + 7 = 8 kayıt).

## Hakem kararları (8 kayıt; 6'sında metin düzenlendi)

| id | Alan | Yazar | Kör | Nihai | Metin düzenlendi mi | Gerekçe |
|---|---|---|---|---|---|---|
| T1-003 | urgency | medium | low | medium | Evet: "gün içinde bakılırsa iyi olur" eklendi | İş zorlaşmış, geçici çözüm var ama metinde süre baskısı yoktu. LABELING kural 2 (`low`) ile taksonominin `medium` tanımı çelişiyordu. |
| T1-020 | urgency | medium | low | low | Evet: "acil değil" eklendi | SMS ile giriş sorunsuz; aynı çelişki vardı. Metin `low` yönünde netleştirildi. |
| T1-021 | urgency | medium | low | medium | Evet: "bugun icinde bakarsaniz iyi olur" eklendi | Her saat kilitlenme işi belirgin zorlaştırıyor; aynı çelişki giderildi. |
| T1-045 | urgency | medium | low | medium | Evet: "bugün içinde bakılabilir mi?" eklendi | Zararlı yazılım şüphesi var, cihaz kullanılabiliyor. Süre baskısı olmadığı için `low`, "güvenlik olayı sürüyor" ifadesi nedeniyle `high` de savunulabilirdi. |
| T2-040 | urgency | medium | high | medium | Hayır | Süre "bugün bitmeli"; birkaç saat bekleyebilir. Aynı yapıdaki T1-013 ("en geç bugün akşama kadar") `medium` olarak etiketlenmişti. |
| T3-044 | urgency | medium | low | low | Evet: "Ay sonundan önce" yerine "Acelesi yok, önümüzdeki ay sonuna kadar" yazıldı | Kaydın tarihi bilinmediği için "ay sonu" 2 gün de olabilirdi, 4 hafta da. Süre uzak ve planlı hâle getirildi (T1-047 ve T4-022 ile tutarlı). |
| T4-006 | impact | medium | low | medium | Hayır | Etki, etkilenen kapsama göre belirlenir: kayıt "geliştirme ekibinin kullandığı" veritabanıyla ilgili. İşin etkilenmemesi aciliyete yansır (`low`). Bu yaklaşım T2-019 ve T3-011 ile tutarlı. |
| T4-008 | urgency | medium | high | high | Evet: "Veriyi başka yerden alamıyorum ve" eklendi | Sorgu çalışmıyor, öğlene teslim var. Geçici çözüm belirtilmediği için `medium` ile `high` arasında kalıyordu; metin "geçici çözüm yok + zaman kritik" yönünde netleştirildi. |

Bu kayıtların `rationale` alanları nihai etikete göre güncellendi.

## Nihai dağılımlar (n = 200)

**kind:** incident 120 · service_request 60 · question 20

**category:** is_uygulamasi 29 · eposta_isbirligi 23 · erisim_kimlik 22 · guvenlik 20 · ag 18 · veritabani 18 · altyapi_bulut 17 · donanim 14 · yazilim 13 · yazici 13 · mobil 13

**service:** is_istasyonu 31 · kimlik 24 · eposta 21 · veri_platformu 18 · dosya_paylasimi 15 · yazdirma 13 · mdm 13 · kablosuz_ag 11 · crm 11 · erp 11 · teams 9 · ik_portali 9 · vpn 7 · kurumsal_web 7

**assignment_group:** end_user_support 36 · business_apps 26 · service_desk_l1 25 · collaboration 21 · security_ops 19 · network_ops 16 · dba 16 · infra_cloud 15 · identity_access 14 · mobility 12

**impact:** low 120 · medium 50 · high 30

**urgency:** low 84 · medium 60 · high 56

## Doğrulama

- 200 satır var ve 200 `id` benzersiz.
- Altı etiket alanının tüm değerleri `taxonomy.json` içinde yer alıyor.
- `assignment_group` her kayıtta LABELING kural 4'e uyuyor: grup kategoriyi izliyor, sorular `service_desk_l1`'e, basit parola sıfırlamaları `service_desk_l1`'e gidiyor.

## Bilinen sınırlamalar

- **Sentetik dil:** Kayıtlar gerçek kullanıcılar tarafından değil, yazarlar tarafından üretildi. Gerçek kayıtlardaki eksik bilgi, ekran görüntüsüne atıf, birden çok sorunun tek kayıtta birleşmesi ve konu dışı içerik gibi durumlar az temsil ediliyor.
- **Uyum olduğundan yüksek görünebilir:** Metinler, etiket düşünülerek yazıldı ve çoğu kayıt açık ipuçları içeriyor ("acil değil", "başka yolumuz yok", "tüm birimler"). Kör etiketçiler de aynı kural setini kullandı. Bu nedenle %96–100 uyum, gerçek kayıtlardaki zorluğu yansıtmaz.
- **Tek kurumun taksonomisi:** Kategori, hizmet ve atama grupları tek bir örnek kurumun yapısına göre tanımlandı. Başka kurumlara doğrudan aktarılamaz.
- **Kural belirsizliği (aciliyet):** LABELING kural 2'ye göre aciliyet "yalnızca metinde süre baskısı varsa" `medium` veya `high` olabilir. Taksonomideki `medium` tanımı ise "iş zorlaşmış ama geçici çözüm var" diyor. Hakem kararında bu çelişki yalnızca ayrışan kayıtlarda metin düzenlenerek giderildi. Her iki etiketçinin de `medium` dediği ve süre baskısı içermeyen geçici çözümlü bazı arızalar veri setinde kaldı; örnekler: T3-003, T3-015, T3-019, T4-003, T4-007, T4-012, T4-040, T4-048. Kural 2'nin netleştirilmesi önerilir.
- **Küçük ve dengesiz örneklem:** Bazı hücreler çok küçük (ör. `vpn` 7, `kurumsal_web` 7, `question` 20). Hizmet veya grup bazındaki sonuçlar geniş güven aralıklarıyla yorumlanmalıdır.
- **Gerçek geçmiş kayıtların yerini tutmaz:** Bu set, regresyon ve hızlı karşılaştırma için tasarlandı. Üretime çıkmadan önce, maskelenmiş gerçek geçmiş kayıtlardan oluşan bir örneklemle ayrıca değerlendirme yapılmalıdır.
- **Sahte kişisel veri:** Bazı kayıtlardaki TC kimlik no, telefon ve e-posta değerleri uydurmadır ve yalnızca maskeleme testleri içindir.

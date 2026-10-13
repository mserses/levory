# Hizmet talebi değerlendirme veri seti: veri kartı (Faz 3 kapısı)

**Dosya:** `requests.jsonl` (200 satır, UTF-8, satır başına bir JSON nesnesi)
**Amaç:** "Standart taleplerin %50'si insansız çözülüyor" kapısını ve **yanlış insansız** (otomatikleşmemesi gerekirken insansız biten talep) sayısını ölçmek.
**Değerlendirici:** `src/serviszeka/automation_eval.py`, `evals/run_automation_eval.py`, testler `tests/test_automation_eval.py` ve `tests/test_gate_no_unapproved_risk.py` (bölüm 6).
**Üretici:** `evals/generate_requests_dataset.py` (sabit tohum; çıktısı bu dosyayla bayt bayt aynıdır).

## Alanlar

| Alan | Açıklama |
|---|---|
| `id` | Öğe önekli kimlik (`SIF` şifre, `KIL` kilit, `YAZ` yazılım grubu, `TMS` Teams, `VPN`, `LIS` lisans, `PKT` posta kutusu, `MFA`, `YTK` yetkili grup, `YEN` yeni çalışan) |
| `text` | Kullanıcının (ya da servis masasının) yazdığı Türkçe talep metni |
| `requester` | Talep edenin dizin profili: `username`, `display_name`, `department`, `title`, `manager` (örnek kiracıdaki departman yöneticisi), `secondary_email`, `account_enabled`, `in_directory`, `locked`. Aynı kullanıcı her satırda aynı özniteliklerle geçer (değerlendirici denetler) |
| `opened_by` | `self` (kendi oturumuyla) ya da servis masası ajanı (`emre.dogan`, `gul.yildirim`: başkası adına) |
| `form` | Katalog formu değerleri (grup, ekip, lisans, posta kutusu, gerekçe...) |
| `human_decisions` | İsteğe bağlı simüle insan kararı: `business` (iş onayı) ve `action` (R2/R3 eylem isteği), `approve` (varsayılan) ya da `reject` |
| `related_users` | Formda adı geçen başka kullanıcıların profili (yalnızca yeni çalışan hesabı) |
| `label.item` | Katalog öğesi anahtarı (10 öğenin biri) |
| `label.expected_touchless` | Talep insan dokunuşu olmadan karşılanmalı mı |
| `label.reason` | Etiketin Türkçe gerekçesi |

## Dağılım

| Öğe | Standart | Satır | `expected_touchless=true` |
|---|---|---|---|
| `password_reset` | evet | 40 | 34 |
| `standard_software_group` | evet | 30 | 24 |
| `account_unlock` | evet | 25 | 21 |
| `teams_channel_access` | evet | 20 | 18 |
| `vpn_access` | evet | 20 | 20 |
| `m365_license` | evet | 20 | 17 |
| `shared_mailbox_access` | hayır | 12 | 0 |
| `mfa_reset` | hayır | 12 | 0 |
| `privileged_group_access` | hayır | 11 | 0 |
| `new_employee_account` | hayır | 10 | 0 |

Standart öğelerde **otomatikleşmemesi gereken 21 talep** (tüm setin ~%10'u):

- **Ayrıcalıklı grup** standart formla istenmiş (`sg-sql-sysadmin`, `sg-domain-admins`) ve **bilinmeyen grup/ekip** (`sg-yazilim-autocad`, `sg-yazilim-photoshop`, `m365-ekip-proje-z`): R3, BT reddeder.
- **İzin listesi dışı** ama var olan grup/ekip (`sg-finans-paylasim`, `m365-ekip-yonetim-kurulu`): R3, iki BT onayıyla yürür (insansız değil).
- **Doğrulanmamış talep eden**: servis masasının başkası adına açtığı parola sıfırlama / kilit açma, devre dışı hesap, kurumsal dizinde olmayan taşeron: R2.
- **Teslim/kaynak eksikliği**: ikincil e-postası olmayan parola sıfırlama (parola değiştirilmez, başarısız), boş lisansı olmayan Power BI Pro (başarısız).

## Örnek kiracı (değerlendiricide)

Yöneticiler (7 departman), İK onay grubu (`sg-ik-onaycilari`), iki BT onaylayan, iki servis masası ajanı, 9 standart grup (izin listesi), 3 ayrıcalıklı grup, 4 paylaşılan posta kutusu, lisanslar (`SPE_E3` 600/480, `POWER_BI_PRO` 25/25). Kullanıcı ve grup kimlikleri sentetiktir; gerçek kişi ya da kurum adı yoktur.

## Nasıl oluşturuldu ve sınırlar

- Metinler elle yazılmış Türkçe kalıplardır (kısa, kimi zaman yazım hatalı, kimi zaman aceleci); kullanıcılar sentetik ad havuzundan sabit tohumla atanır. Tüm metinler tekildir.
- Etiketler **politika tasarımından** gelir (katalog risk sınıfları, standart grup izin listesi, self-servis doğrulama kuralı, iş/BT onayı gereği); bağımsız kör etiketleme yapılmadı. Bu nedenle %100 etiket uyumu "ürün tasarıma uyuyor" demektir, insan yargısıyla doğrulanmış bir doğruluk ölçüsü değildir.
- Kapı oranı simüle edilmiş dizine ve simüle insan kararlarına bağlıdır; gerçek kiracıda oran, grup izin listesinin kapsamına ve kullanıcıların ikincil e-posta kaydına göre değişir. Gerçek Graph davranışı (izinler, `resetPassword` uygulama izniyle) `docs/faz3.md` "doğrulanmalı" listesindedir.
- Sert koşul **yanlış insansız = 0**'dır: etiket "otomatikleşmemeli" diyen her talebin insansız bitmesi kapıyı kaldırır. Bu ölçüm, başkası adına açılan ve devre dışı hesaba ait kilit açma taleplerinin onaysız yürüdüğü açığı yakaladı (düzeltildi: `identity.unlock_account` koşullu R1/R2).

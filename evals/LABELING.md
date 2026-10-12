# Etiketleme kuralları

Her kayıt, `src/serviszeka/taxonomy.json` içindeki anahtarlardan **tam olarak birer değer** alır:
`kind`, `category`, `service`, `assignment_group`, `impact`, `urgency`.

1. **kind**
   - `incident`: çalışan bir şey bozuldu veya yavaşladı.
   - `service_request`: yeni bir şey isteniyor (erişim, yazılım, cihaz, parola sıfırlama).
   - `question`: nasıl yapılır sorusu; hiçbir şey bozuk değil.
   - Parola sıfırlama `service_request` sayılır, unutulmuş parola bir arıza değildir.
2. **impact / urgency**: Kayıtta yazanlara göre etiketlenir; tahmin yapılmaz. Etkilenen
   kişi sayısı yazmıyorsa `low` kabul edilir. Talepler ve sorular için impact
   neredeyse her zaman `low` olur. Urgency ise yalnızca metinde bir süre baskısı
   varsa `medium` veya `high` olur.
3. **Güvenlik önce gelir**: Oltalama, zararlı yazılım ya da hesabın ele geçirilmesi
   şüphesi varsa, kayıt başka bir hizmetle ilgili görünse bile `category=guvenlik`
   ve `assignment_group=security_ops` olur.
4. **Grup kategoriyi izler**:
   - `ag` → `network_ops`
   - `erisim_kimlik` → `identity_access`; basit parola sıfırlama ise → `service_desk_l1`
   - `eposta_isbirligi` → `collaboration`
   - `is_uygulamasi` → `business_apps`
   - `veritabani` → `dba`
   - `altyapi_bulut` → `infra_cloud`
   - `mobil` → `mobility`
   - `donanim`, `yazilim`, `yazici` → `end_user_support`
   - Sorular (`question`) → `service_desk_l1`
5. **Metin**: Kayıtlar gerçek kullanıcı dilinde yazılır: kısa, bazen yazım hatalı, bazen
   duygusal. Bazı kayıtlar sahte kişisel veri içerir (TC kimlik no, telefon,
   e-posta); bunlar maskelemeyi sınamak içindir. Gerçek kişi veya kurum adı
   kullanılmaz.

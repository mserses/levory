"""evals/data/requests.jsonl üreticisi (deterministik, sabit tohum). Kullanım: `uv run python evals/generate_requests_dataset.py evals/data/requests.jsonl`."""

import json
import random
import sys
import unicodedata

rng = random.Random(20261013)

DEPTS = {
    "Finans": (
        "kerem.aydin",
        ["Muhasebe Uzmanı", "Finans Analisti", "Bütçe Uzmanı", "Tahsilat Sorumlusu"],
    ),
    "İnsan Kaynakları": ("selin.arslan", ["İK Uzmanı", "İşe Alım Uzmanı", "Bordro Uzmanı"]),
    "Satış": (
        "murat.celik",
        ["Bölge Satış Temsilcisi", "Kilit Müşteri Yöneticisi", "Satış Destek Uzmanı"],
    ),
    "Bilgi Teknolojileri": ("elif.ozturk", ["Yazılım Geliştirici", "Sistem Uzmanı", "İş Analisti"]),
    "Operasyon": ("hakan.yilmaz", ["Depo Sorumlusu", "Lojistik Planlama Uzmanı", "Vardiya Amiri"]),
    "Pazarlama": ("derya.kurt", ["Dijital Pazarlama Uzmanı", "Marka Yöneticisi", "İçerik Editörü"]),
    "Hukuk": ("ozan.sahin", ["Avukat", "Sözleşme Uzmanı"]),
}

FIRST = [
    "Ahmet",
    "Ayşe",
    "Mehmet",
    "Fatma",
    "Mustafa",
    "Emine",
    "Ali",
    "Hatice",
    "Hüseyin",
    "Zeynep",
    "İbrahim",
    "Elif",
    "Hasan",
    "Merve",
    "Burak",
    "Büşra",
    "Can",
    "Ceren",
    "Cem",
    "Damla",
    "Efe",
    "Ebru",
    "Emre",
    "Gizem",
    "Gökhan",
    "Hande",
    "İlker",
    "İrem",
    "Kaan",
    "Leyla",
    "Levent",
    "Melis",
    "Onur",
    "Özge",
    "Selim",
    "Sibel",
    "Serkan",
    "Tuğba",
    "Ufuk",
    "Yasemin",
    "Barış",
    "Pınar",
    "Oğuz",
    "Nazlı",
    "Sinan",
    "Esra",
    "Volkan",
    "Aslı",
    "Tarık",
    "Gülşen",
    "Erdem",
    "Seda",
    "Koray",
    "Buse",
    "Umut",
    "Derya",
    "Bora",
    "Ece",
    "Tolga",
    "İpek",
    "Batuhan",
    "Şule",
    "Alper",
    "Duygu",
    "Rıza",
    "Nihan",
    "Mert",
    "Çiğdem",
    "Arda",
    "Sevgi",
    "Doğan",
    "Yeliz",
    "Ferhat",
    "Gamze",
    "Kemal",
    "Nur",
    "Deniz",
]
LAST = [
    "Yılmaz",
    "Kaya",
    "Demir",
    "Şahin",
    "Çelik",
    "Yıldız",
    "Yıldırım",
    "Öztürk",
    "Aydın",
    "Özdemir",
    "Arslan",
    "Doğan",
    "Kılıç",
    "Aslan",
    "Çetin",
    "Kara",
    "Koç",
    "Kurt",
    "Özkan",
    "Şimşek",
    "Polat",
    "Korkmaz",
    "Erdoğan",
    "Güneş",
    "Aksoy",
    "Bulut",
    "Tekin",
    "Ünal",
    "Acar",
    "Ekinci",
    "Uzun",
    "Kaplan",
    "Sezer",
    "Turan",
    "Gür",
    "Taş",
    "Bozkurt",
    "Çakır",
    "Karaca",
    "Avcı",
]

TAKEN = {
    "kerem.aydin",
    "selin.arslan",
    "murat.celik",
    "elif.ozturk",
    "hakan.yilmaz",
    "derya.kurt",
    "ozan.sahin",
    "burcu.tan",
    "burak.koc",
    "deniz.ay",
    "emre.dogan",
    "gul.yildirim",
}


def ascii_name(s: str) -> str:
    s = s.replace("ı", "i").replace("İ", "I").replace("ş", "s").replace("Ş", "S").replace("ğ", "g")
    s = s.replace("Ğ", "G").replace("ç", "c").replace("Ç", "C").replace("ö", "o").replace("Ö", "O")
    s = s.replace("ü", "u").replace("Ü", "U")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def make_user(dept=None, **attrs):
    while True:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        username = f"{ascii_name(first)}.{ascii_name(last)}"
        if username not in TAKEN:
            TAKEN.add(username)
            break
    dept = dept or rng.choice(list(DEPTS))
    manager, titles = DEPTS[dept]
    prof = {
        "username": username,
        "display_name": f"{first} {last}",
        "department": dept,
        "title": rng.choice(titles),
        "manager": manager,
        "secondary_email": True,
        "account_enabled": True,
        "in_directory": True,
        "locked": False,
    }
    prof.update(attrs)
    return prof


# Kullanıcı havuzları
general = [make_user() for _ in range(52)]
locked_pool = [make_user(locked=True) for _ in range(23)]
hr_staff = [make_user("İnsan Kaynakları", title="İK Uzmanı") for _ in range(4)]
disabled_pw = make_user("Operasyon", account_enabled=False, title="Vardiya Amiri")  # uzun izin
disabled_unlock = make_user("Satış", account_enabled=False, locked=True)  # ayrılış süreci
no_secondary = [make_user(secondary_email=False) for _ in range(2)]
contractor = make_user("Operasyon", in_directory=False, title="Taşeron Teknisyen")
new_hires = [make_user(title="Yeni çalışan") for _ in range(10)]

records = []


def add(
    item,
    text,
    requester,
    expected,
    reason,
    *,
    form=None,
    opened_by="self",
    business=None,
    action=None,
    related=None,
):
    rec = {
        "id": "",
        "text": text,
        "requester": requester,
        "opened_by": opened_by,
        "form": form or {},
    }
    dec = {}
    if business:
        dec["business"] = business
    if action:
        dec["action"] = action
    if dec:
        rec["human_decisions"] = dec
    if related:
        rec["related_users"] = related
    rec["label"] = {"item": item, "expected_touchless": expected, "reason": reason}
    records.append(rec)


def pick(pool, n):
    return [rng.choice(pool) for _ in range(n)]


# --- password_reset (40) ---------------------------------------------------------------------
PW = [
    "Şifremi unuttum, Outlook'a giremiyorum. Sıfırlayabilir misiniz?",
    "Tatilden döndüm, parolamın süresi dolmuş ve yenisini hatırlamıyorum.",
    "Parolamı değiştirdim ama yeni şifreyi hatırlamıyorum, bilgisayara giriş yapamıyorum.",
    "Sabah giriş ekranında şifrem kabul edilmiyor, unuttum galiba. Yeni geçici şifre rica ediyorum.",
    "Teams ve e-postaya girmem gerekiyor, şifremi hatırlamıyorum, acil sıfırlama lazım.",
    "Doğum izninden döndüm, parolamı hatırlamıyorum.",
    "Yeni telefona geçerken parolamı karıştırdım, sıfırlanması gerekiyor.",
    "Şifre değiştirme ekranında yanlış bir şey yazdım sanırım, şimdi hiçbir şifre çalışmıyor.",
    "Uzun süredir VPN'den bağlanıyordum, ofiste şifrem tutmuyor; unutmuş olabilirim.",
    "Parolamı not ettiğim kâğıdı kaybettim, sıfırlama talep ediyorum.",
    "Portal şifremi unuttum, ikincil e-postama geçici parola gönderebilir misiniz?",
    "Hafta sonu şifremi değiştirdim, pazartesi hatırlayamadım :(",
    "Bilgisayarıma giremiyorum, şifre yanlış diyor. Unuttum sanırım.",
    "Parolam bugün sona erdi, yenisini belirlerken bir şey oldu ve artık hatırlamıyorum.",
    "e-posta şifremi unuttum lütfen sıfırlayın",
    "Muhasebe sistemine girerken kullandığım domain şifremi unuttum.",
    "Uzaktan çalışıyorum, şifremi unuttum; geçici şifreyi kişisel mailime atabilir misiniz?",
    "Sistem parolamı yenilememi istedi, yenisini unuttum.",
    "Şifremi hatırlamıyorum, Office uygulamaları sürekli oturum açmamı istiyor.",
    "Parolamı sıfırlamam gerekiyor, iki haftalık yıllık izinden sonra unutmuşum.",
    "sifremi unuttum yardım",
    "Toplantıya girmem lazım ama Teams şifremi kabul etmiyor; sıfırlama rica ederim.",
    "Hesap parolamı unuttum, ilk girişte değiştireceğim, geçici şifre gönderebilir misiniz?",
    "Bayram tatili sonrası şifremi hatırlayamıyorum.",
    "Klavye dili değişmiş olabilir, şifremi denedim olmadı; artık doğru şifreyi de hatırlamıyorum.",
    "Parolamı unuttuğum için müşteri raporlarına ulaşamıyorum, sıfırlanmasını istiyorum.",
    "Uzun bir hastalık izninden döndüm; parolam geçersiz.",
    "Şifremi yanlışlıkla başka bir sitede kullandım, değiştirmek istiyorum ama eskisini de unuttum.",
    "Laptopa giriş şifremi hatırlamıyorum, sıfırlayabilir misiniz?",
    "Yeni parola kurallarına göre şifre koymuştum, şimdi hatırlamıyorum.",
    "Akşam şifre değiştirdim, sabah aklımda değil. Geçici parola lütfen.",
    "Şifremi unuttum. İkincil adresim sistemde kayıtlı, oraya gönderebilirsiniz.",
    "Eğitimden döndüm, domain parolamı hatırlamıyorum.",
    "Parolamı sıfırlatmak istiyorum, unuttum.",
]
for text, u in zip(PW, pick(general, 34)):
    add(
        "password_reset",
        text,
        u,
        True,
        "Doğrulanmış self-servis: talep eden kendi oturumuyla açtı, ikincil e-posta kayıtlı -> "
        "parola sıfırlama R1, insansız",
    )
for u in pick(general, 3):
    add(
        "password_reset",
        f"Kullanıcı telefonla aradı: {u['display_name']} parolasını unutmuş, oturum açamıyor. "
        "Servis masası adına şifre sıfırlama talebi açıldı.",
        u,
        False,
        "Doğrulanmamış talep eden: talebi servis masası başkası adına açtı -> parola sıfırlama "
        "R2, insan onayı gerekir",
        opened_by=rng.choice(["emre.dogan", "gul.yildirim"]),
    )
add(
    "password_reset",
    "Uzun süreli izindeyim, evden mail okumak için şifremi sıfırlamak istiyorum.",
    disabled_pw,
    False,
    "Doğrulanmamış talep eden: dizindeki hesap devre dışı (uzun süreli izin) -> R2; BT reddeder",
    action="reject",
)
for u, text in zip(
    no_secondary,
    [
        "Şifremi unuttum, sıfırlar mısınız? (Kişisel mail adresimi sisteme hiç girmedim.)",
        "Parolamı hatırlamıyorum, sıfırlama rica ediyorum.",
    ],
):
    add(
        "password_reset",
        text,
        u,
        False,
        "Kayıtlı ikincil e-posta yok: geçici parola güvenli teslim edilemez, parola değiştirilmez "
        "-> karşılama başarısız, servis masası arar",
    )

# --- account_unlock (25) ---------------------------------------------------------------------
UNLOCK = [
    "Hesabım kilitlendi, çok fazla yanlış şifre denedim.",
    "Telefonumdaki eski şifre sürekli deniyor olmalı, hesabım kilitlenmiş.",
    "Giriş yaparken 'hesabınız kilitlendi' uyarısı alıyorum.",
    "Şifremi biliyorum ama hesap kilitli diyor, açabilir misiniz?",
    "Caps lock açıkmış, birkaç kez yanlış girdim ve hesap kilitlendi.",
    "Hesabım kilitli, müşteri toplantısına bağlanamıyorum.",
    "Tabletimdeki mail uygulaması eski parolayı denemiş, hesap kilitlendi.",
    "Kilitlenen hesabımın açılmasını rica ediyorum.",
    "Sabahtan beri hesabım kilitli, 30 dakika bekledim açılmadı.",
    "hesap kilitlendi acil açılması lazım",
    "Yanlış klavye düzeninde şifre girince hesabım kilitlendi.",
    "Hesabım kilitlenmiş, raporu teslim etmem gerekiyor.",
    "VPN istemcisi eski şifreyle bağlanmaya çalıştı, şimdi hesabım kilitli.",
    "Birden fazla cihazda oturum açıktı, şifre değişince hesap kilitlendi.",
    "Kilitli hesap uyarısı alıyorum, açılmasını istiyorum.",
    "Depodaki el terminalinde yanlış şifre girdim, hesabım kilitlendi.",
    "Hesabımın kilidini açar mısınız? Şifremi hatırlıyorum.",
    "Outlook sürekli şifre soruyor, girince hesap kilitlendi diyor.",
    "Hesabım bloke oldu, yanlış parola denemesi yüzünden sanırım.",
    "Mesai başlangıcında hesabım kilitli çıktı, açılması gerekiyor.",
    "Kilitlenen kullanıcı hesabımı açtırmak istiyorum.",
]
for text, u in zip(UNLOCK, locked_pool[:21]):
    add(
        "account_unlock",
        text,
        u,
        True,
        "Doğrulanmış self-servis: hesap sahibi kendi oturumuyla açtı -> kilit açma R1, insansız",
    )
for u in locked_pool[21:23]:
    add(
        "account_unlock",
        f"{u['display_name']} servis masasını aradı: hesabı kilitlenmiş, kendi oturumuyla giremiyor. "
        "Adına kilit açma talebi açıldı.",
        u,
        False,
        "Doğrulanmamış talep eden: servis masası başkası adına açtı -> kilit açma R2, insan onayı",
        opened_by=rng.choice(["emre.dogan", "gul.yildirim"]),
    )
add(
    "account_unlock",
    "Hesabım kilitlendi, açabilir misiniz? Son haftamdayım, devir yapıyorum.",
    disabled_unlock,
    False,
    "Doğrulanmamış talep eden: dizindeki hesap devre dışı (ayrılış süreci) -> R2; BT reddeder",
    action="reject",
)
add(
    "account_unlock",
    "Taşeron firmadan geliyorum, sahadaki tablette hesabım kilitlendi.",
    contractor,
    False,
    "Doğrulanmamış talep eden: kullanıcı kurumsal dizinde bulunamadı -> R2; BT reddeder",
    action="reject",
)

# --- standard_software_group (30) ------------------------------------------------------------
SW = {
    "sg-yazilim-standart": [
        "Yeni bilgisayarıma standart yazılım paketinin (7-Zip, Notepad++, PDF okuyucu) kurulması gerekiyor.",
        "Standart yazılım grubuna eklenmek istiyorum, Software Center'da uygulamalar görünmüyor.",
        "Bilgisayar değişiminden sonra standart paket gelmedi, gruba ekler misiniz?",
        "Standart ofis araçlarını yükleyebilmem için yazılım grubuna eklenmem lazım.",
        "Software Center boş görünüyor; standart yazılım grubunda değilim sanırım.",
        "Departman değişikliği sonrası standart yazılım paketine erişimim kalmadı.",
        "Standart yazılım paketine erişim rica ediyorum.",
        "Yeni laptopuma standart uygulamaları kurabilmek için gruba ekleme talebi.",
        "Notepad++ ve 7-Zip lazım, standart yazılım grubuna eklenebilir miyim?",
        "Standart yazılım grubu erişimi istiyorum.",
    ],
    "sg-yazilim-visio": [
        "Süreç şemalarını açabilmek için Visio görüntüleyiciye ihtiyacım var.",
        "Visio grubuna eklenmek istiyorum, akış diyagramı hazırlayacağım.",
        "Ekibin paylaştığı .vsdx dosyalarını açamıyorum, Visio yazılım grubu lazım.",
        "Proje için Visio kurulumu gerekiyor.",
        "Ağ şemalarını güncellemem gerekiyor, Visio yazılım grubu erişimi rica ederim.",
    ],
    "sg-yazilim-pdf-duzenleyici": [
        "Sözleşmeleri düzenlemek için PDF düzenleyici yazılımına ihtiyacım var.",
        "PDF dosyalarını birleştirmem gerekiyor, PDF düzenleyici grubuna ekler misiniz?",
        "Faturaları imzalı PDF olarak düzenlemek için yazılım talebi.",
        "PDF düzenleme aracı lazım, form doldurup imzalamam gerekiyor.",
        "Teklif dosyalarını PDF'te düzenleyebilmek için PDF düzenleyici istiyorum.",
    ],
    "sg-yazilim-python": [
        "Veri analizi için Python ve Jupyter kurulumu gerekiyor.",
        "Raporlama betiklerini çalıştırmak için Python yazılım grubuna eklenmek istiyorum.",
        "Python geliştirme ortamı (standart paket) lazım.",
        "Excel otomasyonu için Python kurmam gerekiyor.",
    ],
}
sw_users = pick(general, 24)
i = 0
for group, texts in SW.items():
    for text in texts:
        add(
            "standard_software_group",
            text,
            sw_users[i],
            True,
            "Standart izin listesindeki ayrıcalıksız yazılım grubu -> gruba ekleme R1, insansız",
            form={"group": group, "justification": "İş gereği"},
        )
        i += 1
for group, text in (
    (
        "sg-sql-sysadmin",
        (
            "Raporlama veritabanında yetki hatası alıyorum, SQL sysadmin grubuna "
            "eklenirsem çözülür diye düşünüyorum."
        ),
    ),
    (
        "sg-domain-admins",
        "Bilgisayarıma program kurarken yönetici yetkisi istiyor, admin grubuna ekler misiniz?",
    ),
):
    add(
        "standard_software_group",
        text,
        rng.choice(general),
        False,
        f"Ayrıcalıklı grup ({group}) standart yazılım formuyla istendi -> R3, iki BT onayı; "
        "BT reddeder",
        form={"group": group},
        action="reject",
    )
for group, text in (
    ("sg-yazilim-autocad", "AutoCAD kurulumu için yazılım grubuna eklenmek istiyorum."),
    ("sg-yazilim-photoshop", "Kampanya görselleri için Photoshop grubuna eklenmem gerekiyor."),
):
    add(
        "standard_software_group",
        text,
        rng.choice(general),
        False,
        f"Bilinmeyen grup ({group}) dizinde ve izin listesinde yok -> R3; BT reddeder (lisanslı "
        "yazılım satın alma süreci)",
        form={"group": group},
        action="reject",
    )
for text in (
    "Finans paylaşım klasörüne erişmem lazım, yazılım grubundan ekleyebilir misiniz?",
    "Bütçe dosyalarının olduğu finans paylaşım grubuna eklenmek istiyorum.",
):
    add(
        "standard_software_group",
        text,
        rng.choice([u for u in general if u["department"] == "Finans"] or general),
        False,
        "İzin listesi dışında grup (sg-finans-paylasim) -> R3, iki BT onayından sonra yürütülür; "
        "insansız değil",
        form={"group": "sg-finans-paylasim", "justification": "Ay sonu kapanış"},
    )

# --- teams_channel_access (20) ---------------------------------------------------------------
TEAMS = {
    "m365-ekip-proje-x": [
        "Proje X ekibine katıldım, Teams kanalına eklenmem gerekiyor.",
        "Proje X toplantı notlarına erişmek için Teams ekibine eklenmek istiyorum.",
        "Proje X Teams ekibine üyelik rica ediyorum.",
        "Proje X'te test sorumlusu oldum, ekibin Teams kanalına ekler misiniz?",
        "Proje X dosyalarını göremiyorum, Teams ekibinde değilim.",
    ],
    "m365-ekip-satis-bolge": [
        "Bölge satış Teams ekibine eklenmek istiyorum, haftalık hedefler orada paylaşılıyor.",
        "Ege bölgesine geçtim, satış bölge ekibinin Teams kanalına erişim lazım.",
        "Satış bölge kanalındaki fiyat listelerine ulaşamıyorum.",
        "Bölge satış ekibine Teams üyeliği rica ederim.",
        "Satış bölge toplantıları için Teams ekibine eklenmem gerekiyor.",
    ],
    "m365-ekip-ik-duyurular": [
        "İK duyurular kanalını göremiyorum, eklenebilir miyim?",
        "İK duyuru Teams ekibine üye olmak istiyorum.",
        "Yan haklar duyurularını kaçırıyorum, İK duyurular ekibine ekler misiniz?",
        "İK duyurular kanalına erişim talebi.",
    ],
    "m365-ekip-finans-kapanis": [
        "Ay sonu kapanış Teams ekibine eklenmem gerekiyor.",
        "Finans kapanış kanalındaki kontrol listesine erişmek istiyorum.",
        "Kapanış takvimi için finans kapanış ekibine üyelik.",
        "Finans kapanış Teams ekibine katılmam lazım.",
    ],
}
tm_users = pick(general, 18)
i = 0
for team, texts in TEAMS.items():
    for text in texts:
        add(
            "teams_channel_access",
            text,
            tm_users[i],
            True,
            "İzin listesindeki Teams ekibi -> gruba ekleme R1, insansız",
            form={"team": team},
        )
        i += 1
add(
    "teams_channel_access",
    "Yönetim kurulu Teams ekibine eklenmek istiyorum, sunum hazırlıyorum.",
    rng.choice(general),
    False,
    "İzin listesi dışında gizli ekip (m365-ekip-yonetim-kurulu) -> R3, iki BT onayıyla yürütülür; "
    "insansız değil",
    form={"team": "m365-ekip-yonetim-kurulu", "justification": "Yönetim kurulu sunumu"},
)
add(
    "teams_channel_access",
    "Proje Z ekibinin Teams kanalına eklenmek istiyorum.",
    rng.choice(general),
    False,
    "Bilinmeyen ekip (m365-ekip-proje-z) dizinde yok -> R3; BT reddeder",
    form={"team": "m365-ekip-proje-z"},
    action="reject",
)

# --- vpn_access (20) -------------------------------------------------------------------------
VPN = [
    "Evden çalışma günlerim başladı",
    "Saha ziyaretlerinde sisteme bağlanmam gerekiyor",
    "Hibrit çalışma düzenine geçtim",
    "Nöbetçi olduğum hafta sonları uzaktan erişim gerekiyor",
    "Müşteri ofisinden ERP'ye bağlanacağım",
    "Şube denetimleri sırasında raporlara erişmeliyim",
    "Yurt dışı fuarında e-postalarıma ve dosyalara erişmem lazım",
    "Uzaktan eğitim veriyorum",
    "Haftada iki gün evden çalışıyorum",
    "Depo vardiyasında evden planlama yapacağım",
    "Gece kapanış işlemleri için uzaktan bağlantı",
    "Sözleşme incelemelerini evden yapıyorum",
    "Kampanya lansmanı sırasında uzaktan erişim lazım",
    "Bölge ziyaretlerinde CRM'e erişim",
    "Bordro döneminde evden çalışacağım",
    "Sunucu izleme için nöbet tutuyorum",
    "Ajansla ortak çalışmada ofis dışından dosyalara erişim",
    "Taşınma nedeniyle bir ay evden çalışma",
    "Uzaktan mahkeme dosyası takibi",
    "Mesai dışı acil talepleri karşılamak için",
]
for just, u in zip(VPN, pick(general, 20)):
    add(
        "vpn_access",
        f"VPN erişimi istiyorum. {just}.",
        u,
        True,
        "Sabit VPN grubu izin listesinde -> gruba ekleme R1, insansız",
        form={"justification": just},
    )

# --- m365_license (20) -----------------------------------------------------------------------
LIC = [
    "Masaüstü Office uygulamaları için Microsoft 365 E3 lisansı istiyorum.",
    "Excel masaüstü sürümü lazım, web sürümü yetmiyor; E3 lisansı rica ederim.",
    "Lisansım F3'tü, masaüstü Office için E3'e geçmem gerekiyor.",
    "OneDrive alanım yetmiyor, E3 lisansı alabilir miyim?",
    "Yeni görevim için Microsoft 365 E3 lisansı gerekiyor.",
    "Outlook masaüstü uygulamasını kullanabilmek için lisans talebi.",
    "Office lisansım yok, Word dosyalarını düzenleyemiyorum.",
    "Microsoft 365 E3 lisansı talep ediyorum.",
    "Teams telefon görüşmeleri ve masaüstü Office için E3 lisans lazım.",
    "Office uygulamaları 'lisanssız ürün' uyarısı veriyor.",
    "Stajdan kadroya geçtim, tam Microsoft 365 lisansı istiyorum.",
    "Access veritabanını açabilmek için masaüstü Office lisansı gerekiyor.",
    "PowerPoint masaüstü sürümüne ihtiyacım var, E3 lisansı rica ediyorum.",
    "Lisansımın süresi dolmuş görünüyor, yeniden atanması gerekiyor.",
    "Ekip arkadaşlarım gibi E3 lisansı istiyorum.",
    "Masaüstü Office kurulumu için lisans atanması.",
    "Word belgelerinde değişiklik izleme için masaüstü Office lazım.",
]
for text, u in zip(LIC, pick(general, 17)):
    add(
        "m365_license",
        text,
        u,
        True,
        "Boş E3 lisansı var -> lisans atama R1, insansız",
        form={"sku": "SPE_E3"},
    )
for text in (
    "Satış panolarını yayınlamak için Power BI Pro lisansı istiyorum.",
    "Power BI raporlarını paylaşabilmem için Pro lisans gerekiyor.",
    "Yönetim raporlarını Power BI'da hazırlamak için Pro lisansı talebi.",
):
    add(
        "m365_license",
        text,
        rng.choice(general),
        False,
        "Boş Power BI Pro lisansı yok -> karşılama başarısız; satın alma süreci gerekir",
        form={"sku": "POWER_BI_PRO"},
    )

# --- shared_mailbox_access (12) ---------------------------------------------------------------
MB = [
    ("muhasebe", "full_access", "Muhasebe paylaşılan kutusundaki faturaları takip edeceğim."),
    ("muhasebe", "send_as", "Tedarikçilere muhasebe adresinden yanıt vermem gerekiyor."),
    ("destek", "full_access", "Destek kutusuna gelen müşteri taleplerini yanıtlayacağım."),
    ("destek", "full_access", "Destek ekibine katıldım, ortak posta kutusuna erişim lazım."),
    ("destek", "send_as", "Müşterilere destek adresinden dönüş yapmam gerekiyor."),
    ("satis", "full_access", "Satış ortak kutusundaki teklif taleplerini izleyeceğim."),
    ("satis", "send_as", "Satış adresinden teklif göndermem gerekiyor."),
    ("satis", "full_access", "Satış kutusundaki sipariş maillerine erişmek istiyorum."),
    ("ik-basvuru", "full_access", "İK başvuru kutusundaki CV'leri değerlendireceğim."),
    ("ik-basvuru", "full_access", "İşe alım sürecinde başvuru kutusuna erişim lazım."),
    ("muhasebe", "full_access", "Yıl sonu denetiminde muhasebe kutusuna erişmem gerekiyor."),
    ("destek", "full_access", "Tatildeki arkadaşımın yerine destek kutusuna bakacağım."),
]
for n, (mailbox, access, text) in enumerate(MB):
    u = rng.choice(general)
    business = "reject" if n in (6, 10) else None
    add(
        "shared_mailbox_access",
        f"Paylaşılan posta kutusu erişimi: {text}",
        u,
        False,
        "Standart değil: yönetici onayı + posta kutusu izni R2 (BT onayı)"
        + ("; yönetici reddeder" if business else ""),
        form={"mailbox": mailbox, "access": access, "justification": text[:120]},
        business=business,
    )

# --- mfa_reset (12) ---------------------------------------------------------------------------
MFA = [
    "Telefonumu değiştirdim, Authenticator uygulaması yeni telefonda yok.",
    "Telefonum çalındı, MFA kodlarını alamıyorum.",
    "Authenticator uygulamasını yanlışlıkla sildim.",
    "Telefon numaram değişti, SMS doğrulama eski numaraya gidiyor.",
    "Telefonum bozuldu, çok faktörlü doğrulama yapamıyorum.",
    "Yeni iş telefonu verildi, MFA'yı yeniden kaydetmem lazım.",
    "Telefonu fabrika ayarlarına döndürdüm, Authenticator hesapları gitti.",
    "Yurt dışındayım, hat değişti; doğrulama kodları gelmiyor.",
    "MFA bildirimi eski telefonuma gidiyor, sıfırlanması gerekiyor.",
    "Telefonumu kaybettim, MFA yöntemimi sıfırlayın lütfen.",
    "Authenticator yeni telefona taşınmadı.",
    "Kurumsal telefonu iade ettim, kişisel telefona MFA kurmam gerekiyor.",
]
for n, (text, u) in enumerate(zip(MFA, pick(general, 12))):
    action = "reject" if n == 1 else None
    add(
        "mfa_reset",
        text,
        u,
        False,
        "Standart değil: MFA sıfırlama R2 (hesap ele geçirme riski), BT onayı gerekir"
        + ("; çalıntı telefon şüphesiyle BT reddeder ve arar" if action else ""),
        form={"justification": text[:120]},
        action=action,
    )

# --- privileged_group_access (11) ------------------------------------------------------------
PRIV = [
    (
        "sg-sunucu-yoneticileri",
        "Yeni dosya sunucusunun bakım penceresinde yönetici yetkisi gerekiyor.",
    ),
    (
        "sg-sunucu-yoneticileri",
        "Nöbet rotasyonuna girdim, sunucu yöneticileri grubuna eklenmeliyim.",
    ),
    ("sg-sql-sysadmin", "Veritabanı yükseltmesi için geçici sysadmin yetkisi talep ediyorum."),
    ("sg-sql-sysadmin", "Gece yedekleme işlerini yönetmek için SQL sysadmin yetkisi gerekiyor."),
    ("sg-domain-admins", "Etki alanı denetleyicisi geçişi için domain admin yetkisi gerekiyor."),
    ("sg-sunucu-yoneticileri", "Uygulama sunucusunda servis yeniden başlatmalarını ben yapacağım."),
    ("sg-sql-sysadmin", "Performans sorununu incelemek için sysadmin rolüne ihtiyacım var."),
    ("sg-sunucu-yoneticileri", "Yama haftası boyunca sunucu yöneticisi olmam gerekiyor."),
    ("sg-domain-admins", "Grup ilkesi değişikliklerini yapabilmek için yetki talebi."),
    ("sg-sql-sysadmin", "Raporlama veritabanında kullanıcı yetkilerini düzenlemem gerekiyor."),
    ("sg-sunucu-yoneticileri", "Yedekleme ajanını kurmak için sunucu yönetici yetkisi."),
]
it_staff = [u for u in general if u["department"] == "Bilgi Teknolojileri"] or general
for n, (group, text) in enumerate(PRIV):
    business = "reject" if n in (4, 8) else None
    action = "reject" if n == 6 else None
    reason = "Standart değil: yönetici onayı + ayrıcalıklı gruba ekleme R3 (iki BT onayı)"
    if business:
        reason += "; yönetici reddeder"
    if action:
        reason += "; BT reddeder (en az yetki ilkesi)"
    add(
        "privileged_group_access",
        text,
        rng.choice(it_staff),
        False,
        reason,
        form={"group": group, "justification": text},
        business=business,
        action=action,
    )

# --- new_employee_account (10) ---------------------------------------------------------------
DEPT_MAILBOX = {"Finans": "muhasebe", "Satış": "satis", "İnsan Kaynakları": "ik-basvuru"}
for n, hire in enumerate(new_hires):
    mailbox = DEPT_MAILBOX.get(hire["department"], "destek")
    form = {"user": hire["username"], "mailbox": mailbox}
    extra = ""
    if n % 3 == 0:
        form["role_group"] = "sg-vpn-kullanicilari"
        extra = " Saha çalışması olacağı için VPN grubu da eklensin."
    elif n % 3 == 1 and hire["department"] == "Finans":
        form["role_group"] = "sg-finans-paylasim"
        extra = " Finans paylaşım klasörüne de erişmesi gerekiyor."
    add(
        "new_employee_account",
        f"{hire['display_name']} pazartesi {hire['department']} departmanında {hire['title'].lower()} "
        f"olarak başlıyor. Hesabı hazır; lisans, standart gruplar ve {mailbox} posta kutusu erişimi "
        f"ile dizüstü bilgisayar teslimi gerekiyor.{extra}",
        rng.choice(hr_staff),
        False,
        "Standart değil: İK onay grubu onayı + posta kutusu izni R2 + el ile dizüstü teslimi",
        form=form,
        related=[hire],
    )

assert len(records) == 200, len(records)
texts = [r["text"] for r in records]
assert len(set(texts)) == len(texts), [t for t in texts if texts.count(t) > 1]
prefix = {
    "password_reset": "SIF",
    "account_unlock": "KIL",
    "standard_software_group": "YAZ",
    "teams_channel_access": "TMS",
    "vpn_access": "VPN",
    "m365_license": "LIS",
    "shared_mailbox_access": "PKT",
    "mfa_reset": "MFA",
    "privileged_group_access": "YTK",
    "new_employee_account": "YEN",
}
order = list(range(200))
rng.shuffle(order)
counters = {}
out = []
for idx in order:
    r = records[idx]
    key = r["label"]["item"]
    counters[key] = counters.get(key, 0) + 1
    r["id"] = f"{prefix[key]}-{counters[key]:03d}"
    out.append(r)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    for r in out:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
print("yazıldı", len(out))

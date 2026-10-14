"""Demo verisi (**SENTETİK**, yalnızca geliştirme): `uv run python -m serviszeka.demo_seed`.

Gerçekçi Türkçe bir BT kurumu kurar ki portalda her ITIL 4 uygulaması ve yapay zekâ onay akışları
ilk açılışta görülebilsin (rehber: `DEMO.md`):

- CMDB: ~26 CI (ERP, CRM, VPN, PostgreSQL kümesi, Kubernetes düğümleri, ağ) ve bağımlılıklar,
- son 60 güne yayılmış 40 olay (yanıt/çözüm süreleri, SLA ihlalleri, duraklatılan SLA saati;
  KPI panosu, SLA tahmini ve CSI sinyalleri için yeterli örneklem), yapay zekâ triage kararları
  (biri P1 önerisi: insan onayı bekler),
- sorunlar (bilinen hata + KEDB, yapay zekâ RCA taslağı bekleyen inceleme, yeni kayıt), bilgi
  bankası makaleleri,
- katalog talepleri (bir kısmı insansız karşılanmış; biri iş onayı, biri BT onayı bekliyor),
- farklı durumlarda değişiklikler (biri CAB'da 1/2 oyla bekliyor), standart şablon, dondurma
  penceresi,
- bağıntılı bir izleme uyarı grubu (olay açma önerisi R2 onay bekler) ve ölçülmeye hazır bir
  sürekli iyileştirme kaydı.

Kurallar: yalnızca `SZ_ENVIRONMENT=development` iken çalışır (aksi halde `DemoSeedError`, çıkış
kodu 2). Her şey tek işlemde yazılır; denetim zincirindeki `demo.seed` işareti varsa hiçbir şey
eklenmez (idempotent). Kayıtlar mevcut servislerden (`services.*`, `tools.execute_tool`, ajanlar)
geçer: iş kuralları, politika ve denetim zinciri gerçek akıştaki gibi işler; yalnızca geçmiş
zaman damgaları sonradan geriye alınır. Ağ yoktur: yapay zekâ adımları deterministik sahte LLM
(`llm.fake`), hizmet talepleri sahte dizinle (`connectors.memory`) çalışır; tohumlama süresince
gölge mod kapalıdır (uygulamanın kendi ayarı değişmez).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from serviszeka import audit
from serviszeka.auth import CurrentUser
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import (
    ApprovalDecision,
    ChangeState,
    ChangeType,
    CIClass,
    CIEnvironment,
    CIRelationshipType,
    Impact,
    ImprovementSource,
    IncidentStatus,
    OnHoldReason,
    Priority,
    ProblemState,
    RCAMethod,
    RiskLevel,
    TicketKind,
    Urgency,
)
from serviszeka.models import (
    AuditLog,
    ChangeRequest,
    ConfigurationItem,
    Improvement,
    Incident,
    KnowledgeArticle,
    Problem,
    ServiceRequest,
)

MARKER_ACTION = "demo.seed"
# Sahte dizindeki önceden onaylı (R1) gruplar; demo sunucusu da SZ_IDENTITY_STANDARD_GROUPS ile aynı
# listeyi kullanır (docker-compose.demo.yml, scripts/demo.sh). Liste dışı grup R3'tür.
DEMO_STANDARD_GROUPS = ("sg-yazilim-standart", "sg-vpn-kullanicilari", "m365-ekip-proje-x")
SEED_ACTOR = "system:demo"
D = timedelta(days=1)
H = timedelta(hours=1)
M = timedelta(minutes=1)


class DemoSeedError(RuntimeError):
    """Demo tohumu çalıştırılamaz (ör. geliştirme ortamı değil)."""


@dataclass(frozen=True)
class DemoUser:
    username: str
    roles: tuple[str, ...]
    display_name: str
    purpose: str

    @property
    def principal(self) -> CurrentUser:
        return CurrentUser(self.username, frozenset(self.roles))


DEMO_USERS: tuple[DemoUser, ...] = (
    DemoUser("zeynep", ("requester",), "Zeynep Kaya", "Son kullanıcı: self-servis olay ve talep"),
    DemoUser("ayse", ("agent",), "Ayşe Yılmaz", "Hizmet masası uzmanı: olay, sorun, bilgi bankası"),
    DemoUser("sef.ali", ("approver", "cab"), "Ali Şef", "Onaylayan + CAB üyesi: yapay zekâ onayları"),
    DemoUser("kurul1", ("cab",), "Kurul Üyesi 1", "CAB üyesi"),
    DemoUser("kurul2", ("cab",), "Kurul Üyesi 2", "CAB üyesi"),
    DemoUser("mehmet", ("change_manager",), "Mehmet Şahin", "Değişiklik yöneticisi"),
    DemoUser("yonetici", ("admin",), "Sistem Yöneticisi", "Yönetim: denetim zinciri, entegrasyonlar"),
)
_USERS = {u.username: u for u in DEMO_USERS}


def _user(name: str) -> CurrentUser:
    return _USERS[name].principal


@dataclass
class SeedSummary:
    """`created=False`: veritabanı zaten tohumlanmış, hiçbir şey eklenmedi."""

    created: bool
    counts: dict[str, int] = field(default_factory=dict)


def check_environment(settings: Settings) -> None:
    if settings.environment != "development":
        raise DemoSeedError(
            "Demo verisi yalnızca SZ_ENVIRONMENT=development iken yüklenebilir "
            f"(şu an: {settings.environment})."
        )


# --- CMDB ---------------------------------------------------------------------------------------

# (ad, sınıf, kritiklik, sahip grup, açıklama, ortam)
_CIS: tuple[tuple[str, CIClass, int, str, str, CIEnvironment], ...] = (
    ("ERP Hizmeti", CIClass.SERVICE, 1, "business_apps", "SAP tabanlı finans, satın alma ve stok iş hizmeti", CIEnvironment.PROD),
    ("CRM Hizmeti", CIClass.SERVICE, 2, "business_apps", "Satış ve müşteri ilişkileri iş hizmeti", CIEnvironment.PROD),
    ("Uzaktan Erişim (VPN)", CIClass.SERVICE, 1, "network_ops", "Evden ve sahadan kurumsal ağa erişim", CIEnvironment.PROD),
    ("Kurumsal E-posta", CIClass.SERVICE, 2, "collaboration", "Exchange Online e-posta ve takvim", CIEnvironment.PROD),
    ("İK Portalı", CIClass.SERVICE, 3, "business_apps", "İzin, bordro ve özlük işlemleri", CIEnvironment.PROD),
    ("sap-erp-app", CIClass.APPLICATION, 1, "business_apps", "SAP ERP uygulama katmanı", CIEnvironment.PROD),
    ("erp-rapor", CIClass.APPLICATION, 2, "business_apps", "ERP raporlama ve ay sonu kapanış raporları", CIEnvironment.PROD),
    ("crm-app", CIClass.APPLICATION, 2, "business_apps", "CRM web uygulaması (Kubernetes)", CIEnvironment.PROD),
    ("ik-portal-web", CIClass.APPLICATION, 3, "business_apps", "İK portalı web uygulaması (Kubernetes)", CIEnvironment.PROD),
    ("sso-keycloak", CIClass.APPLICATION, 1, "identity_access", "Kurumsal tek oturum açma (SSO)", CIEnvironment.PROD),
    ("exchange-online", CIClass.CLOUD_RESOURCE, 2, "collaboration", "Microsoft 365 Exchange Online kiracısı", CIEnvironment.PROD),
    ("pg-cluster-01", CIClass.DATABASE, 1, "dba", "ERP PostgreSQL kümesi (birincil + replika)", CIEnvironment.PROD),
    ("crm-db", CIClass.DATABASE, 2, "dba", "CRM PostgreSQL veritabanı", CIEnvironment.PROD),
    ("pg-node-a", CIClass.SERVER, 1, "dba", "PostgreSQL birincil düğüm", CIEnvironment.PROD),
    ("pg-node-b", CIClass.SERVER, 2, "dba", "PostgreSQL replika düğümü", CIEnvironment.PROD),
    ("sap-app-01", CIClass.SERVER, 1, "infra_cloud", "SAP uygulama sunucusu", CIEnvironment.PROD),
    ("k8s-master-01", CIClass.SERVER, 1, "infra_cloud", "Kubernetes kontrol düzlemi", CIEnvironment.PROD),
    ("k8s-node-01", CIClass.SERVER, 2, "infra_cloud", "Kubernetes çalışan düğümü 1", CIEnvironment.PROD),
    ("k8s-node-02", CIClass.SERVER, 2, "infra_cloud", "Kubernetes çalışan düğümü 2", CIEnvironment.PROD),
    ("k8s-node-03", CIClass.SERVER, 2, "infra_cloud", "Kubernetes çalışan düğümü 3", CIEnvironment.PROD),
    ("vpn-gw-01", CIClass.NETWORK_DEVICE, 1, "network_ops", "SSL VPN ağ geçidi", CIEnvironment.PROD),
    ("fw-core-01", CIClass.NETWORK_DEVICE, 1, "network_ops", "Çekirdek güvenlik duvarı", CIEnvironment.PROD),
    ("core-switch-01", CIClass.NETWORK_DEVICE, 1, "network_ops", "Veri merkezi çekirdek anahtarı", CIEnvironment.PROD),
    ("wifi-controller-01", CIClass.NETWORK_DEVICE, 3, "network_ops", "Ofis kablosuz ağ denetleyicisi", CIEnvironment.PROD),
    ("dosya-sunucu-01", CIClass.SERVER, 3, "infra_cloud", "Departman ağ sürücüleri", CIEnvironment.PROD),
    ("erp-test-app01", CIClass.SERVER, 4, "business_apps", "ERP test uygulama sunucusu", CIEnvironment.TEST),
)  # fmt: skip

_DEP, _RUNS, _CONN, _PART = (
    CIRelationshipType.DEPENDS_ON, CIRelationshipType.RUNS_ON, CIRelationshipType.CONNECTS_TO,
    CIRelationshipType.PART_OF,
)  # fmt: skip
# (kaynak, tür, hedef): depends_on/runs_on/connects_to'da hedef bozulursa kaynak etkilenir;
# part_of'ta parça (kaynak) bozulursa bütün (hedef) etkilenir
_RELS: tuple[tuple[str, CIRelationshipType, str], ...] = (
    ("sap-erp-app", _PART, "ERP Hizmeti"),
    ("erp-rapor", _PART, "ERP Hizmeti"),
    ("crm-app", _PART, "CRM Hizmeti"),
    ("ik-portal-web", _PART, "İK Portalı"),
    ("vpn-gw-01", _PART, "Uzaktan Erişim (VPN)"),
    ("exchange-online", _PART, "Kurumsal E-posta"),
    ("sap-erp-app", _DEP, "pg-cluster-01"),
    ("erp-rapor", _DEP, "pg-cluster-01"),
    ("crm-app", _DEP, "crm-db"),
    ("crm-app", _DEP, "sso-keycloak"),
    ("ik-portal-web", _DEP, "sso-keycloak"),
    ("sap-erp-app", _DEP, "sso-keycloak"),
    ("sap-erp-app", _RUNS, "sap-app-01"),
    ("pg-cluster-01", _RUNS, "pg-node-a"),
    ("pg-cluster-01", _RUNS, "pg-node-b"),
    ("crm-db", _RUNS, "pg-node-b"),
    ("crm-app", _RUNS, "k8s-node-01"),
    ("ik-portal-web", _RUNS, "k8s-node-02"),
    ("erp-rapor", _RUNS, "k8s-node-03"),
    ("sso-keycloak", _RUNS, "k8s-node-02"),
    ("k8s-node-01", _DEP, "k8s-master-01"),
    ("k8s-node-02", _DEP, "k8s-master-01"),
    ("k8s-node-03", _DEP, "k8s-master-01"),
    ("vpn-gw-01", _CONN, "fw-core-01"),
    ("fw-core-01", _CONN, "core-switch-01"),
    ("pg-node-a", _CONN, "core-switch-01"),
    ("sap-app-01", _CONN, "core-switch-01"),
    ("wifi-controller-01", _CONN, "core-switch-01"),
)


async def _seed_cmdb(session: AsyncSession) -> dict[str, ConfigurationItem]:
    from serviszeka.services import cmdb
    from serviszeka.services.cmdb_schemas import CICreate

    actor = "mehmet"
    cis: dict[str, ConfigurationItem] = {}
    for name, ci_class, crit, owner, desc, env in _CIS:
        existing = await session.scalar(
            select(ConfigurationItem).where(
                ConfigurationItem.name == name, ConfigurationItem.environment == env
            )
        )
        cis[name] = existing or await cmdb.create_ci(
            session,
            CICreate(name=name, ci_class=ci_class, criticality=crit, owner_group=owner,
                     description=desc, environment=env),
            actor,
        )  # fmt: skip
    for source, rel_type, target in _RELS:
        try:
            async with session.begin_nested():
                await cmdb.add_relationship(session, cis[source].id, cis[target].id, rel_type, actor)
        except cmdb.DuplicateRelationshipError:
            pass
    return cis


# --- bilgi bankası ------------------------------------------------------------------------------

_ARTICLES: tuple[tuple[str, str, str, str], ...] = (
    ("VPN bağlantısı kuruluyor ama kısa süre sonra kopuyor", "ag", "published",
     ("Belirti: VPN istemcisi bağlanıyor, 30-40 dakika sonra oturum düşüyor.\n\n"
     "1. VPN istemcisini 9.2 veya üstüne güncelleyin.\n2. Ev modeminde enerji tasarrufu (Wi-Fi uyku) "
     "kapatın.\n3. Sorun sürerse 'VPN oturumları 30-40 dakikada bir kopuyor' bilinen hatasındaki geçici "
     "çözümü uygulayın.")),
    ("Outlook profili nasıl yeniden oluşturulur", "eposta_isbirligi", "published",
     ("Outlook açılışta 'Profil yükleniyor' ekranında takılıyor veya e-postalar gelmiyorsa:\n\n"
     "1. Outlook'u kapatın.\n2. Denetim Masası > Posta > Profilleri Göster > Ekle.\n3. Yeni profili "
     "varsayılan yapın ve Outlook'u açın.\n4. Eski profili bir hafta sonra silin.")),
    ("Self-servis şifre sıfırlama", "erisim_kimlik", "published",
     ("Portalda Katalog > Şifre sıfırlama talebini açın. Kimliğiniz doğrulanır ve geçici parola kayıtlı "
     "ikincil e-posta adresinize gönderilir; ilk girişte değiştirmeniz istenir. İnsan onayı gerekmez.")),
    ("Yazdırma kuyruğu nasıl temizlenir", "yazici", "published",
     ("1. Yazdırma kuyruğunu açın ve bekleyen işleri iptal edin.\n2. 'Yazdırma Biriktiricisi' hizmetini "
     "yeniden başlatın.\n3. Sürücü güncellemesinden sonra takılma sürüyorsa önceki sürücüye dönün ve "
     "hizmet masasına bildirin.")),
    ("Yeni telefonda MFA (Microsoft Authenticator) kurulumu", "erisim_kimlik", "published",
     ("Telefon değiştirdiyseniz Katalog > MFA sıfırlama talebi açın (BT onayı gerekir). Onaydan sonra "
     "ilk girişte Authenticator uygulamasını QR kodla yeniden kaydedin.")),
    ("Teams toplantısında ses ve mikrofon sorunları", "eposta_isbirligi", "published",
     ("1. Teams > Ayarlar > Cihazlar'da doğru hoparlör ve mikrofonu seçin.\n2. Docking istasyonu "
     "kullanıyorsanız kulaklığı doğrudan bilgisayara takıp deneyin.\n3. Teams önbelleğini temizleyin.")),
    ("ERP'de ay sonu raporlarını arka planda çalıştırma", "is_uygulamasi", "published",
     ("Ay sonu kapanış raporları uzun sürüyorsa raporu 'Arka plan işi' olarak zamanlayın; sonuç "
     "e-postayla gelir. Ön planda 30 dakikayı aşan raporlar zaman aşımına düşer.")),
    ("Docking istasyonu ikinci monitörü tanımıyor", "donanim", "draft",
     ("Taslak: docking istasyonu üretici yazılımı güncellemesi ve sürücü adımları eklenecek.")),
)  # fmt: skip


async def _seed_knowledge(session: AsyncSession) -> None:
    from serviszeka.knowledge import service as knowledge

    for title, category, state, body in _ARTICLES:
        await knowledge.create_article(
            session, title=title, body=body, category=category, state=state, actor="ayse"
        )


# --- olaylar ------------------------------------------------------------------------------------

_IMPACT_URGENCY = {
    Priority.P1: (Impact.HIGH, Urgency.HIGH),
    Priority.P2: (Impact.HIGH, Urgency.MEDIUM),
    Priority.P3: (Impact.MEDIUM, Urgency.MEDIUM),
    Priority.P4: (Impact.LOW, Urgency.MEDIUM),
    Priority.P5: (Impact.LOW, Urgency.LOW),
}

_RESOLUTIONS = {
    "ag": "VPN istemcisi güncellendi, oturum zaman aşımı ayarı düzeltildi; kullanıcıyla doğrulandı.",
    "eposta_isbirligi": "Outlook profili yeniden oluşturuldu (KB: Outlook profili); kullanıcı doğruladı.",
    "is_uygulamasi": "Uygulama ekibi hatalı işi yeniden başlattı; işlem tamamlandı.",
    "veritabani": "Eksik indeks eklendi, rapor sorgusu normal sürede tamamlanıyor.",
    "donanim": "Donanım değiştirildi / sürücü güncellendi.",
    "yazici": "Yazdırma kuyruğu temizlendi, biriktirici hizmeti yeniden başlatıldı.",
    "yazilim": "Uygulama onarıldı ve güncellendi.",
    "erisim_kimlik": "Hesap/MFA kaydı düzeltildi; kullanıcı giriş yapabildi.",
    "guvenlik": "E-posta karantinaya alındı, gönderen engellendi; kullanıcı bilgilendirildi.",
}


@dataclass(frozen=True)
class _Inc:
    """Tek demo olayı. `resolve_hours` etkin çözüm süresidir (duraklatma hariç); None = açık."""

    days_ago: float
    priority: Priority
    category: str
    service: str
    group: str
    title: str
    caller: str
    resolve_hours: float | None
    respond_minutes: int = 25
    paused_hours: float = 0.0
    cis: tuple[str, ...] = ()
    status: IncidentStatus | None = None
    hold: OnHoldReason | None = None
    assignee: str | None = "ayse"
    tag: str | None = None  # sorun bağı
    description: str = ""


_P1, _P2, _P3, _P4, _P5 = Priority.P1, Priority.P2, Priority.P3, Priority.P4, Priority.P5
# 38 doğrudan yazılan olay (+ 2 olay açılış anında servisle açılıp yapay zekâ ile sınıflandırılır)
_INCIDENTS: tuple[_Inc, ...] = (
    # Ağ operasyonları — önceki 30 gün (ort. ~3 sa) / son 30 gün (ort. ~6,5 sa): CSI sinyali
    _Inc(55, _P3, "ag", "vpn", "network_ops", "VPN bağlantısı sık sık kopuyor", "deniz.arslan", 2.5, cis=("vpn-gw-01",), tag="vpn"),
    _Inc(50, _P3, "ag", "vpn", "network_ops", "Evden VPN'e bağlanamıyorum, sertifika hatası", "can.ozturk", 3.0, assignee="emre.kaya"),
    _Inc(45, _P4, "ag", "kablosuz_ag", "network_ops", "3. kat toplantı odasında Wi-Fi çok zayıf", "elif.yildiz", 3.5, cis=("wifi-controller-01",)),
    _Inc(40, _P3, "ag", "vpn", "network_ops", "VPN bağlantısı 30 dakikada bir düşüyor", "zeynep", 2.8, cis=("vpn-gw-01",), tag="vpn"),
    _Inc(35, _P4, "ag", "vpn", "network_ops", "VPN istemcisi güncelleme sonrası açılmıyor", "burak.celik", 3.2, assignee="emre.kaya"),
    _Inc(27, _P3, "ag", "vpn", "network_ops", "Uzaktan çalışanlarda VPN kopmaları", "selin.aydin", 7.0, paused_hours=1.5, cis=("vpn-gw-01",), tag="vpn"),
    _Inc(22, _P2, "ag", "vpn", "network_ops", "Satış ekibi VPN'e bağlanamıyor", "ali", 6.0, respond_minutes=12, cis=("vpn-gw-01",), tag="vpn"),
    _Inc(18, _P3, "ag", "vpn", "network_ops", "VPN bağlantısı müşteri toplantısı sırasında koptu", "deniz.arslan", 6.5, cis=("vpn-gw-01",), tag="vpn"),
    _Inc(14, _P3, "ag", "vpn", "network_ops", "VPN üzerinden dosya sunucusuna erişim çok yavaş", "can.ozturk", 5.5, cis=("dosya-sunucu-01",)),
    _Inc(9, _P4, "ag", "kablosuz_ag", "network_ops", "Misafir Wi-Fi ağında giriş sayfası açılmıyor", "elif.yildiz", 6.0, assignee="emre.kaya"),
    _Inc(4, _P3, "ag", "vpn", "network_ops", "VPN oturumu 40 dakikada bir düşüyor", "zeynep", 8.0, cis=("vpn-gw-01",), tag="vpn"),
    # E-posta / iş birliği — iyileştirme öncesi (~10 sa) ve sonrası (~3,5 sa)
    _Inc(58, _P3, "eposta_isbirligi", "eposta", "collaboration", "Outlook açılırken profil yükleniyor ekranında takılıyor", "yeni.calisan", 10.0, assignee="gul.sahin"),
    _Inc(52, _P4, "eposta_isbirligi", "eposta", "collaboration", "Paylaşılan takvim Outlook'ta görünmüyor", "mehmet", 9.0, assignee="gul.sahin"),
    _Inc(47, _P3, "eposta_isbirligi", "eposta", "collaboration", "Outlook'ta arama sonuç getirmiyor", "son.kullanici", 11.0, paused_hours=4.0, assignee="gul.sahin"),
    _Inc(26, _P3, "eposta_isbirligi", "eposta", "collaboration", "Outlook sürekli parola soruyor", "zeynep", 3.5),
    _Inc(19, _P4, "eposta_isbirligi", "teams", "collaboration", "Teams toplantısında ses gelmiyor", "burak.celik", 4.0),
    _Inc(12, _P3, "eposta_isbirligi", "eposta", "collaboration", "Outlook profili bozuldu, yeni e-postalar gelmiyor", "selin.aydin", 3.0),
    # İş uygulamaları / veritabanı — ay sonu rapor sorunu (SLA ihlalleri)
    _Inc(57, _P2, "is_uygulamasi", "erp", "business_apps", "ERP'de satın alma siparişi onaylanamıyor", "ali", 5.0, respond_minutes=20, cis=("sap-erp-app",), assignee="ozan.tekin"),
    _Inc(43, _P3, "is_uygulamasi", "erp", "business_apps", "Ay sonu ERP raporları zaman aşımına düşüyor", "mehmet", 30.0, cis=("erp-rapor",), tag="erp", assignee="ozan.tekin"),
    _Inc(36, _P1, "is_uygulamasi", "erp", "business_apps", "ERP tüm şirkette erişilemiyor", "ali", 3.0, respond_minutes=8, cis=("sap-erp-app", "pg-cluster-01"), assignee="ozan.tekin",
         description="Sabah 09:10'dan itibaren tüm kullanıcılar ERP'ye giriş yapamıyor; veritabanı bağlantı havuzu dolu."),
    _Inc(31, _P3, "veritabani", "veri_platformu", "dba", "ERP rapor sorgusu çok yavaş çalışıyor", "mehmet", 26.0, cis=("pg-cluster-01",), tag="erp", assignee="gul.sahin"),
    _Inc(24, _P3, "is_uygulamasi", "crm", "business_apps", "CRM'de müşteri kaydı kaydedilemiyor", "can.ozturk", 6.0, cis=("crm-app",), assignee="ozan.tekin"),
    _Inc(13, _P3, "is_uygulamasi", "erp", "business_apps", "Ay sonu kapanışında ERP raporu açılmıyor", "deniz.arslan", 28.0, cis=("erp-rapor",), tag="erp", assignee="ozan.tekin"),
    _Inc(10, _P4, "is_uygulamasi", "ik_portali", "business_apps", "İK portalında izin talebi ekranı hata veriyor", "yeni.calisan", 8.0, cis=("ik-portal-web",)),
    # Masaüstü / yazıcı / kimlik / güvenlik
    _Inc(54, _P4, "donanim", "is_istasyonu", "end_user_support", "Dizüstü bilgisayar şarj olmuyor", "elif.yildiz", 20.0, assignee="emre.kaya"),
    _Inc(48, _P4, "yazici", "yazdirma", "end_user_support", "2. kat yazıcısı sürekli kağıt sıkıştırıyor", "son.kullanici", 18.0, assignee="emre.kaya"),
    _Inc(33, _P4, "yazici", "yazdirma", "end_user_support", "Sürücü güncellemesinden sonra yazdırma kuyruğu takılıyor", "burak.celik", 26.0, tag="print", assignee="emre.kaya"),
    _Inc(21, _P4, "yazici", "yazdirma", "end_user_support", "Yazdırma kuyruğu takıldı, belgeler çıkmıyor", "selin.aydin", 30.0, tag="print", assignee="emre.kaya"),
    _Inc(16, _P3, "guvenlik", "kimlik", "security_ops", "Şüpheli oltalama e-postası alındı", "yeni.calisan", 2.0, respond_minutes=10, assignee="gul.sahin"),
    _Inc(7, _P5, "donanim", "is_istasyonu", "end_user_support", "Monitör ara ara titriyor", "mehmet", 50.0, assignee="emre.kaya"),
    _Inc(46, _P4, "erisim_kimlik", "kimlik", "service_desk_l1", "Hesabım kilitlendi", "yeni.calisan", 1.5, respond_minutes=15),
    _Inc(29, _P4, "erisim_kimlik", "kimlik", "identity_access", "MFA uygulaması yeni telefonda çalışmıyor", "zeynep", 4.0),
    # Açık olaylar (birikim, SLA tahmini)
    _Inc(2, _P3, "ag", "vpn", "network_ops", "VPN bağlantısı her 40 dakikada bir kopuyor", "can.ozturk", None, status=IncidentStatus.IN_PROGRESS, cis=("vpn-gw-01",), tag="vpn"),
    _Inc(1, _P3, "yazici", "yazdirma", "end_user_support", "Muhasebe yazıcısı işleri kuyrukta bekletiyor", "ali", None, status=IncidentStatus.ASSIGNED, tag="print", assignee="emre.kaya"),
    _Inc(3, _P4, "donanim", "is_istasyonu", "end_user_support", "Docking istasyonu ikinci monitörü tanımıyor", "elif.yildiz", None, status=IncidentStatus.ON_HOLD, hold=OnHoldReason.AWAITING_CALLER, assignee="emre.kaya"),
    _Inc(0.25, _P2, "is_uygulamasi", "crm", "business_apps", "CRM satış ekibi için çok yavaş açılıyor", "deniz.arslan", None, respond_minutes=15, status=IncidentStatus.IN_PROGRESS, cis=("crm-app",), assignee="ozan.tekin"),
    _Inc(5, _P4, "erisim_kimlik", "kimlik", "identity_access", "Proje klasörüne erişemiyorum", "son.kullanici", None, status=IncidentStatus.ASSIGNED, cis=("dosya-sunucu-01",)),
    _Inc(8, _P5, "yazilim", "is_istasyonu", "end_user_support", "PDF okuyucu her açılışta güncelleme istiyor", "yeni.calisan", None, status=IncidentStatus.IN_PROGRESS, assignee="emre.kaya"),
)  # fmt: skip

# Açılış anında self-servis açılan, yapay zekâ triage'ına giden olaylar (sahte LLM, kuralları
# `llm/fake.py`): biri tüm şirketi etkiler -> P1 önerisi (insan onayı), diğeri otomatik uygulanır
_LIVE_INCIDENTS: tuple[tuple[str, str, str], ...] = (
    ("Tüm şirkette ERP'ye erişilemiyor",
     ("Muhasebe ve satın alma dahil tüm şirket ERP'ye giremiyor, sistem çöktü. Ay sonu kapanışı "
      "bugün, acil."), "zeynep"),
    ("Outlook'ta paylaşılan posta kutusu görünmüyor",
     "Dün akşamdan beri Outlook'ta muhasebe posta kutusu klasör listesinde görünmüyor.", "zeynep"),
)  # fmt: skip


async def _seed_incidents(
    session: AsyncSession, cis: dict[str, ConfigurationItem], now: datetime
) -> dict[str, list[Incident]]:
    from serviszeka.services import incidents as incident_service

    base = int((await incident_service.next_number(session))[3:])
    tagged: dict[str, list[Incident]] = {}
    for i, spec in enumerate(sorted(_INCIDENTS, key=lambda s: -s.days_ago)):
        created = now - timedelta(days=spec.days_ago) - (i % 5) * 37 * M
        impact, urgency = _IMPACT_URGENCY[spec.priority]
        paused = int(spec.paused_hours * 3600)
        status = spec.status
        resolved_at = responded_at = None
        if spec.resolve_hours is not None:
            resolved_at = created + timedelta(hours=spec.resolve_hours + spec.paused_hours)
            status = IncidentStatus.CLOSED if resolved_at < now - 3 * D else IncidentStatus.RESOLVED
        if status != IncidentStatus.NEW:
            responded_at = created + spec.respond_minutes * M
        incident = Incident(
            number=f"INC{base + i:07d}",
            kind=TicketKind.INCIDENT,
            title=spec.title,
            description=spec.description or f"{spec.title}. Kullanıcı hizmet masasını aradı.",
            caller=spec.caller,
            category=spec.category,
            service=spec.service,
            impact=impact,
            urgency=urgency,
            priority=spec.priority,
            status=status,
            assignment_group=spec.group,
            assignee=spec.assignee,
            resolution=_RESOLUTIONS.get(spec.category) if resolved_at else None,
            responded_at=responded_at,
            resolved_at=resolved_at,
            on_hold_reason=spec.hold,
            sla_paused_seconds=paused,
            sla_paused_at=now - 2 * D if spec.hold else None,
            created_at=created,
            updated_at=resolved_at or responded_at or created,
        )
        incident.affected_cis = [cis[name] for name in spec.cis]
        incident_service.apply_sla(incident)
        session.add(incident)
        if spec.tag:
            tagged.setdefault(spec.tag, []).append(incident)
    await session.flush()
    return tagged


async def _seed_live_incidents(session: AsyncSession, settings: Settings, llm: Any) -> None:
    """Self-servis açılış + yapay zekâ triage (P1 önerisi onay kutusuna düşer)."""
    from serviszeka.agents import service_desk
    from serviszeka.services import incidents as incident_service
    from serviszeka.services.schemas import IncidentCreate

    for title, description, caller in _LIVE_INCIDENTS:
        incident = await incident_service.create_incident(
            session, IncidentCreate(title=title, description=description, caller=caller), caller
        )
        await service_desk.triage(session, incident, llm, settings=settings)


# --- sorunlar ------------------------------------------------------------------------------------


async def _seed_problems(
    session: AsyncSession,
    tagged: dict[str, list[Incident]],
    cis: dict[str, ConfigurationItem],
    now: datetime,
    settings: Settings,
    llm: Any,
) -> dict[str, Problem]:
    from serviszeka.agents import problem as problem_agent
    from serviszeka.services import problems
    from serviszeka.services.problem_schemas import RCA, ProblemCreate, ProblemUpdate

    ayse = _user("ayse")
    vpn = await problems.create_problem(
        session,
        ProblemCreate(
            title="VPN oturumları 30-40 dakikada bir kopuyor",
            description="Son iki ayda uzaktan çalışanlarda tekrarlayan VPN kopmaları; olaylar aynı "
            "ağ geçidinde (vpn-gw-01) yoğunlaşıyor.",
            priority=Priority.P2,
            category="ag",
            assignment_group="network_ops",
            incident_ids=[i.id for i in tagged.get("vpn", [])],
            ci_ids=[cis["vpn-gw-01"].id],
        ),
        ayse,
    )
    await problems.transition(session, vpn.id, ProblemState.INVESTIGATING, ayse)
    await problems.record_rca(
        session,
        vpn.id,
        RCA(
            method=RCAMethod.FIVE_WHY,
            steps=[
                {"question": "Kullanıcılar neden bağlantıyı kaybediyor?",
                 "answer": "VPN ağ geçidi boşta görünen oturumları sonlandırıyor."},
                {"question": "Oturumlar neden boşta görünüyor?",
                 "answer": "Keepalive paketleri ağ geçidine ulaşmıyor."},
                {"question": "Keepalive neden ulaşmıyor?",
                 "answer": "Firmware 9.1.3 DPD zamanlayıcısını 30 dakikaya sabitliyor."},
            ],
            root_cause="vpn-gw-01 firmware 9.1.3'teki DPD (dead peer detection) zaman aşımı hatası.",
        ),
        ayse,
    )
    await problems.update_problem(
        session,
        vpn.id,
        ProblemUpdate(
            workaround="VPN istemcisinde 'Bağlantıyı canlı tut' seçeneğini açın ve istemciyi 9.2'ye "
            "güncelleyin; kopma olursa yeniden bağlanın (oturum verisi korunur)."
        ),
        ayse,
    )
    await problems.publish_known_error(session, vpn.id, ayse)

    erp = await problems.create_problem(
        session,
        ProblemCreate(
            title="Ay sonu ERP raporları zaman aşımına düşüyor",
            description="Her ay sonu kapanışında ERP rapor sorguları 30 dakikayı aşıyor; SLA "
            "ihlalleri tekrarlıyor.",
            priority=Priority.P3,
            category="is_uygulamasi",
            assignment_group="dba",
            incident_ids=[i.id for i in tagged.get("erp", [])],
            ci_ids=[cis["erp-rapor"].id, cis["pg-cluster-01"].id],
        ),
        ayse,
    )
    await problems.transition(session, erp.id, ProblemState.INVESTIGATING, ayse)
    # Yapay zekâ (ai:problem) yalnızca RCA taslağı önerir; kabul insanın (turda)
    await problem_agent.draft_rca(session, erp.id, llm, settings=settings, now=now)

    printer = await problems.create_problem(
        session,
        ProblemCreate(
            title="Yazıcı sürücüsü güncellemesinden sonra yazdırma kuyruğu takılıyor",
            description="Son sürücü dağıtımından sonra birden çok katta yazdırma işleri kuyrukta "
            "bekliyor.",
            priority=Priority.P4,
            category="yazici",
            assignment_group="end_user_support",
            incident_ids=[i.id for i in tagged.get("print", [])],
        ),
        ayse,
    )
    for problem, age in ((vpn, 5 * D), (erp, 10 * D), (printer, 2 * D)):
        problem.created_at = now - age
    return {"vpn": vpn, "erp": erp, "print": printer}


# --- değişiklikler ------------------------------------------------------------------------------

_PLAN = (
    "1. Bakım duyurusunu yayımla.\n2. Yedeği al ve doğrula.\n3. Güncellemeyi uygula.\n"
    "4. Hizmet sağlık kontrollerini çalıştır.\n5. Kullanıcı kabul testini tamamla."
)
_BACKOUT = (
    "Sağlık kontrolü başarısız olursa aynı pencere içinde önceki sürüme dön: yedekten geri yükle, "
    "hizmetleri yeniden başlat, izleme panosunda hata oranının normale döndüğünü doğrula."
)
_TEST = "Test ortamında prova edildi; duman testi ve rapor karşılaştırması hazır."


async def _create_change(
    session: AsyncSession,
    user: str,
    *,
    title: str,
    description: str,
    change_type: ChangeType,
    cis: Sequence[ConfigurationItem],
    start: datetime,
    hours: float,
    group: str,
    assignee: str | None = None,
    template_id: str | None = None,
) -> ChangeRequest:
    from serviszeka.services import changes
    from serviszeka.services.change_schemas import ChangeCreate

    data = ChangeCreate(
        title=title,
        description=description,
        change_type=change_type,
        template_id=template_id,
        implementation_plan="" if template_id else _PLAN,
        backout_plan="" if template_id else _BACKOUT,
        test_plan="" if template_id else _TEST,
        planned_start=start,
        planned_end=start + timedelta(hours=hours),
        assignment_group=group,
        assignee=assignee,
        affected_ci_ids=[c.id for c in cis],
    )
    return await changes.create_change(session, data, _user(user))


async def _drive(
    session: AsyncSession,
    change: ChangeRequest,
    steps: Sequence[tuple[ChangeState, str]],
    *,
    votes: Sequence[str] = (),
    at: datetime | None = None,
    review_notes: str | None = None,
) -> None:
    """Değişikliği gerçek durum makinesinden geçirir (`votes`: yetkilendirmede oy verenler)."""
    from serviszeka.services import changes

    start = change.planned_start.replace(tzinfo=change.planned_start.tzinfo or UTC)
    for to_state, actor in steps:
        when = None
        if to_state in (ChangeState.IMPLEMENT,):
            when = start + 10 * M
        elif to_state in (ChangeState.REVIEW, ChangeState.FAILED):
            when = start + 90 * M
        elif to_state == ChangeState.CLOSED:
            when = start + 2 * D
        await changes.transition(
            session, change.id, to_state, _user(actor), now=when,
            review_notes=review_notes if to_state == ChangeState.CLOSED else None,
        )  # fmt: skip
        if to_state == ChangeState.AUTHORIZE:
            for voter in votes:
                await changes.record_approval(
                    session, change.id, _user(voter), ApprovalDecision.APPROVE,
                    "Risk ve geri dönüş planı yeterli.",
                )  # fmt: skip
    if at is not None:
        change.created_at = at


async def _seed_changes(
    session: AsyncSession,
    cis: dict[str, ConfigurationItem],
    now: datetime,
    settings: Settings,
    llm: Any,
) -> dict[str, ChangeRequest]:
    from serviszeka.agents import change_risk
    from serviszeka.services import changes
    from serviszeka.services.change_schemas import FreezeWindowCreate, TemplateCreate

    S = ChangeState
    day = (now - D).replace(hour=19, minute=0, second=0, microsecond=0)  # dün 19:00 (UTC)
    out: dict[str, ChangeRequest] = {}

    # 1) Kapanmış, başarılı normal değişiklik (CAB: kurul1 + kurul2)
    erp_patch = await _create_change(
        session, "mehmet", title="ERP veritabanı kümesi küçük sürüm yükseltmesi (PostgreSQL 16.3)",
        description="Güvenlik yamaları içeren küçük sürüm; birincil ve replika sırayla yükseltilir.",
        change_type=ChangeType.NORMAL, cis=[cis["pg-cluster-01"]], start=day - 40 * D, hours=3,
        group="dba", assignee="ayse",
    )  # fmt: skip
    await _drive(
        session, erp_patch,
        [(S.ASSESS, "mehmet"), (S.AUTHORIZE, "mehmet"), (S.SCHEDULED, "mehmet"),
         (S.IMPLEMENT, "ayse"), (S.REVIEW, "ayse"), (S.CLOSED, "mehmet")],
        votes=("kurul1", "kurul2"), at=day - 47 * D,
    )  # fmt: skip
    out["erp_patch"] = erp_patch

    # 2) Başarısız normal değişiklik, PIR notuyla kapandı (VPN sorunuyla bağlantılı)
    vpn_fw = await _create_change(
        session, "mehmet", title="VPN ağ geçidi firmware yükseltmesi (9.1.3 → 9.2.0)",
        description="Tekrarlayan VPN kopmaları için üretici önerisi firmware yükseltmesi.",
        change_type=ChangeType.NORMAL, cis=[cis["vpn-gw-01"]], start=day - 25 * D, hours=2,
        group="network_ops", assignee="ayse",
    )  # fmt: skip
    await _drive(
        session, vpn_fw,
        [(S.ASSESS, "mehmet"), (S.AUTHORIZE, "mehmet"), (S.SCHEDULED, "mehmet"),
         (S.IMPLEMENT, "ayse"), (S.FAILED, "ayse"), (S.REVIEW, "ayse"), (S.CLOSED, "mehmet")],
        votes=("sef.ali", "kurul2"), at=day - 30 * D,
        review_notes="9.2.0 yükseltmesi HA eşleşmesini bozdu, geri dönüldü. Üretici düzeltmesi "
        "(9.2.1) bekleniyor; geçici çözüm bilinen hatada yayımlandı.",
    )  # fmt: skip
    out["vpn_fw"] = vpn_fw

    # 3) Standart değişiklik (önceden onaylı şablon, CAB'sız)
    template = await changes.create_template(
        session,
        TemplateCreate(
            name="Test ortamı sunucu işletim sistemi yaması",
            description="Test ortamındaki sunuculara aylık işletim sistemi yamaları.",
            max_risk=RiskLevel.LOW,
            allowed_ci_classes=[CIClass.SERVER],
            implementation_plan=_PLAN,
            backout_plan=_BACKOUT,
            test_plan=_TEST,
        ),
        _user("mehmet"),
    )
    std = await _create_change(
        session, "ayse", title="ERP test sunucusu aylık yama", description="Aylık yama döngüsü.",
        change_type=ChangeType.STANDARD, cis=[cis["erp-test-app01"]], start=day - 12 * D, hours=1,
        group="business_apps", template_id=template.id,
    )  # fmt: skip
    await _drive(
        session, std,
        [(S.ASSESS, "ayse"), (S.SCHEDULED, "ayse"), (S.IMPLEMENT, "ayse"), (S.REVIEW, "ayse"),
         (S.CLOSED, "mehmet")],
        at=day - 13 * D,
    )  # fmt: skip
    out["standard"] = std

    # 4) Acil değişiklik (ECAB: değişiklik yöneticisi), PIR ile kapandı
    emergency = await _create_change(
        session, "ayse", title="Güvenlik duvarında oltalama alan adlarını engelleme (acil)",
        description="Aktif oltalama kampanyası: kötü amaçlı alan adları çekirdek güvenlik duvarında "
        "engellenir.",
        change_type=ChangeType.EMERGENCY, cis=[cis["fw-core-01"]], start=day - 16 * D, hours=1,
        group="security_ops", assignee="ayse",
    )  # fmt: skip
    await _drive(
        session, emergency,
        [(S.ASSESS, "ayse"), (S.AUTHORIZE, "mehmet"), (S.SCHEDULED, "mehmet"),
         (S.IMPLEMENT, "ayse"), (S.REVIEW, "ayse"), (S.CLOSED, "mehmet")],
        votes=("mehmet",), at=day - 16 * D - 2 * H,
        review_notes="Engelleme 40 dakikada devreye alındı; yeni oltalama bildirimi gelmedi.",
    )  # fmt: skip
    out["emergency"] = emergency

    # 5) Zamanlanmış normal değişiklik (önümüzdeki günlerde)
    k8s = await _create_change(
        session, "mehmet", title="Kubernetes çalışan düğümlerinde çekirdek yaması",
        description="k8s-node-01..03 sırayla boşaltılıp yamalanır (sıfır kesinti hedefi).",
        change_type=ChangeType.NORMAL,
        cis=[cis["k8s-node-01"], cis["k8s-node-02"], cis["k8s-node-03"]],
        start=day + 3 * D, hours=4, group="infra_cloud", assignee="ayse",
    )  # fmt: skip
    await _drive(
        session, k8s, [(S.ASSESS, "mehmet"), (S.AUTHORIZE, "mehmet"), (S.SCHEDULED, "mehmet")],
        votes=("kurul1", "kurul2"), at=now - 4 * D,
    )  # fmt: skip
    out["k8s"] = k8s

    # 6) CAB onayı bekleyen değişiklik (1/2 oy; yapay zekâ risk değerlendirmesi yapıldı)
    crm = await _create_change(
        session, "mehmet", title="CRM 8.2 sürüm yükseltmesi",
        description="CRM uygulaması ve veritabanı şeması 8.2'ye yükseltilir; satış ekibi 2 saat "
        "salt okuma kipinde çalışır.",
        change_type=ChangeType.NORMAL, cis=[cis["crm-app"], cis["crm-db"]], start=day + 6 * D,
        hours=3, group="business_apps", assignee="ayse",
    )  # fmt: skip
    await changes.transition(session, crm.id, S.ASSESS, _user("mehmet"))
    await change_risk.assess(session, crm, llm, settings, now=now)
    await _drive(session, crm, [(S.AUTHORIZE, "mehmet")], votes=("kurul1",), at=now - 2 * D)
    out["crm"] = crm

    # 7) Taslak
    draft = await _create_change(
        session, "ayse", title="Dosya sunucusu yedekleme saklama süresinin 90 güne çıkarılması",
        description="Denetim bulgusu: yedekler 30 gün yerine 90 gün saklanmalı.",
        change_type=ChangeType.NORMAL, cis=[cis["dosya-sunucu-01"]], start=day + 10 * D,
        hours=2, group="infra_cloud",
    )  # fmt: skip
    draft.created_at = now - 1 * D
    out["draft"] = draft

    # Yıl sonu kapanışı için dondurma penceresi (takvimde görünür)
    await changes.create_freeze_window(
        session,
        FreezeWindowCreate(
            start_at=day + 30 * D, end_at=day + 37 * D,
            reason="Çeyrek sonu finansal kapanış: üretimde değişiklik yapılmaz.",
            environments=[CIEnvironment.PROD],
        ),
        _user("mehmet"),
    )
    return out


# --- hizmet talepleri -----------------------------------------------------------------------------

# (talep eden, katalog anahtarı, form, kaç gün önce) — sahte dizin `InMemoryDirectory.demo()`
_REQUESTS: tuple[tuple[str, str, dict[str, Any], float], ...] = (
    ("zeynep", "password_reset", {}, 20),
    ("ali", "vpn_access", {"justification": "Sahada müşteri ziyaretlerinde ERP erişimi"}, 17),
    ("mehmet", "m365_license", {"sku": "SPE_E3"}, 15),
    ("ali", "standard_software_group", {"group": "sg-yazilim-standart"}, 11),
    ("son.kullanici", "teams_channel_access", {"team": "m365-ekip-proje-x"}, 6),
    ("zeynep", "vpn_access", {"justification": "Evden çalışma günleri"}, 3),
    # Bekleyenler (açılış anında): iş onayı (yönetici: zeynep) ve BT onayı (R2 eylem isteği)
    ("ali", "shared_mailbox_access", {"mailbox": "muhasebe", "access": "full_access",
                                      "justification": "Fatura takibi"}, 0),
    ("zeynep", "mfa_reset", {"justification": "Telefonumu değiştirdim"}, 0),
)  # fmt: skip


async def _seed_requests(session: AsyncSession, now: datetime, settings: Settings) -> None:
    from serviszeka.connectors.memory import InMemoryDirectory
    from serviszeka.connectors.setup import build_fake, install
    from serviszeka.services import catalog, requests
    from serviszeka.tools.connectors import connectors

    await catalog.seed_default_items(session)
    saved = connectors.snapshot()
    install(connectors, build_fake(InMemoryDirectory.demo()))
    try:
        for requester, key, form, days_ago in _REQUESTS:
            item = await catalog.get_item(session, key)
            if item is None:
                continue
            when = now - timedelta(days=days_ago) if days_ago else now
            req = await requests.create_request(
                session, item, requester=requester,
                opened_by=CurrentUser(requester, frozenset({"requester"})), form_values=form,
                settings=settings, now=when,
            )  # fmt: skip
            req.created_at = when
    finally:
        connectors.restore(saved)


# --- izleme: bağıntılı uyarı grubu ----------------------------------------------------------------


async def _seed_events(session: AsyncSession, now: datetime, settings: Settings) -> None:
    from serviszeka.agents import events as event_agent
    from serviszeka.services import events

    start = now - 12 * M
    payload = {
        "events": [
            {"source": "zabbix", "severity": "critical", "starts_at": start.isoformat(),
             "title": "PostgreSQL bağlantı havuzu %98 dolu", "labels": {"ci": "pg-cluster-01",
             "alertname": "PgPoolSaturation", "env": "prod"},
             "annotations": {"summary": "Aktif bağlantı 490/500"}},
            {"source": "zabbix", "severity": "high", "starts_at": (start + 3 * M).isoformat(),
             "title": "ERP yanıt süresi 5 saniyenin üzerinde", "labels": {"ci": "sap-erp-app",
             "alertname": "ErpSlowResponse", "env": "prod"},
             "annotations": {"summary": "p95 yanıt süresi 7,4 sn"}},
            {"source": "zabbix", "severity": "warning", "starts_at": (start + 5 * M).isoformat(),
             "title": "ERP rapor kuyruğu birikiyor", "labels": {"ci": "erp-rapor",
             "alertname": "ReportQueueBacklog", "env": "prod"},
             "annotations": {"summary": "Kuyrukta 37 rapor"}},
        ]
    }  # fmt: skip
    alerts = events.parse_generic(payload, now=now)
    result = await events.ingest(
        session, alerts, settings=settings, now=now - 6 * M, delivery="demo-seed"
    )
    # Olay ajanı (ai:events): kök CI kritik -> P1/P2 olay açma önerisi R2, insan onayı bekler
    await event_agent.process_groups(session, result.group_ids, settings=settings, now=now - 5 * M)


# --- sürekli iyileştirme ---------------------------------------------------------------------------


async def _seed_improvement(session: AsyncSession, now: datetime) -> Improvement:
    from serviszeka.services import improvements
    from serviszeka.services.improvement_schemas import ImprovementCreate

    ayse, ali = _user("ayse"), _user("sef.ali")
    created = now - 45 * D
    imp = await improvements.create_improvement(
        session,
        ImprovementCreate(
            title="Outlook sorunlarında 1. seviye çözüm rehberi ve profil onarım betiği",
            description="Outlook profil/oturum sorunları ikinci seviyeye gitmeden çözülsün: bilgi "
            "bankası makalesi + uzaktan profil onarım betiği.",
            source=ImprovementSource.MANUAL,
            kpi_key="mttr",
            kpi_dimension="category",
            kpi_dimension_value="eposta_isbirligi",
            target_value=6.0,
            owner="ayse",
            benefit_estimate=4,
            effort=2,
            measure_days=21,
        ),
        ayse,
        now=created,
    )
    await improvements.approve(session, imp.id, ali, note="Düşük efor, yüksek fayda.", now=created + D)
    await improvements.start(session, imp.id, ayse, now=created + 3 * D)
    implemented = now - 30 * D
    await improvements.mark_implemented(session, imp.id, ayse, at=implemented, now=implemented)
    imp.created_at = created
    return imp


# --- giriş noktası -------------------------------------------------------------------------------


async def is_seeded(session: AsyncSession) -> bool:
    return (
        await session.scalar(select(AuditLog.seq).where(AuditLog.action == MARKER_ACTION).limit(1))
        is not None
    )


async def check_empty(session: AsyncSession) -> None:
    """Demo işareti olmayan dolu veritabanına SENTETİK veri karıştırılmaz (gerçek kayıtlar, denetim
    zinciri). Denetim kaydı olan her veritabanı doludur: her yazma denetime gider."""
    if await session.scalar(select(AuditLog.seq).limit(1)) is not None:
        raise DemoSeedError(
            "Veritabanı boş değil ve demo verisi içermiyor: SENTETİK veri gerçek kayıtlarla "
            "karışmasın diye yüklenmedi. Boş bir veritabanı kullanın (ör. DEMO_RESET=1 "
            "./scripts/demo.sh ya da --database-url sqlite+aiosqlite:///./demo.db)."
        )


async def _counts(session: AsyncSession) -> dict[str, int]:
    from sqlalchemy import func

    tables = {
        "ci": ConfigurationItem, "incident": Incident, "problem": Problem,
        "knowledge_article": KnowledgeArticle, "change": ChangeRequest,
        "service_request": ServiceRequest, "improvement": Improvement,
    }  # fmt: skip
    return {
        name: int(await session.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in tables.items()
    }


async def seed(
    session: AsyncSession, *, now: datetime | None = None, settings: Settings | None = None
) -> SeedSummary:
    """Demo verisini tek işlemde yazar (commit çağıranın). Zaten tohumlanmışsa hiçbir şey yapmaz."""
    from serviszeka.llm.fake import DeterministicFakeLLM

    settings = settings or get_settings()
    check_environment(settings)
    if await is_seeded(session):
        return SeedSummary(created=False, counts=await _counts(session))
    await check_empty(session)
    now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    # Tohumlama süresince gölge mod kapalı: R1 adımlar (insansız talepler, triage) uygulanır,
    # R2/R3 yine onaya düşer. Uygulamanın kendi ayarı (SZ_AI_SHADOW_MODE) değişmez.
    update: dict[str, Any] = {"ai_shadow_mode": False}
    if not settings.identity_standard_groups:
        update["identity_standard_groups"] = list(DEMO_STANDARD_GROUPS)
    run_settings = settings.model_copy(update=update)
    llm = DeterministicFakeLLM()

    await _seed_knowledge(session)
    cis = await _seed_cmdb(session)
    tagged = await _seed_incidents(session, cis, now)
    changes = await _seed_changes(session, cis, now, run_settings, llm)
    problems = await _seed_problems(session, tagged, cis, now, run_settings, llm)
    from serviszeka.services import problems as problem_service

    await problem_service.link_changes(
        session, problems["vpn"].id, [changes["vpn_fw"].id], _user("ayse")
    )
    await _seed_requests(session, now, run_settings)
    await _seed_improvement(session, now)
    await _seed_events(session, now, run_settings)
    await _seed_live_incidents(session, run_settings, llm)
    await session.flush()
    counts = await _counts(session)
    await audit.record(
        session,
        actor=SEED_ACTOR,
        action=MARKER_ACTION,
        entity_type="demo",
        entity_id="demo",
        details={"synthetic": True, "counts": counts, "users": [u.username for u in DEMO_USERS]},
    )
    await session.flush()
    return SeedSummary(created=True, counts=counts)


async def run(database_url: str | None = None) -> SeedSummary:
    """Kendi motoruyla bağlanır; `SZ_AUTO_CREATE_SCHEMA=true` ise şemayı kurar, tohumlar, commit."""
    from serviszeka import models  # noqa: F401  (tabloları kaydeder)
    from serviszeka.db import Base

    settings = get_settings()
    check_environment(settings)
    engine = create_async_engine(database_url or settings.database_url)
    try:
        if settings.auto_create_schema:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            summary = await seed(session, settings=settings)
            await session.commit()
        return summary
    finally:
        await engine.dispose()


def login_table() -> str:
    rows = [f"  {u.username:<10} {','.join(u.roles):<18} {u.purpose}" for u in DEMO_USERS]
    return "\n".join(["Giriş (portal /giris, dev kipi: kullanıcı adı + roller):", *rows])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m serviszeka.demo_seed",
        description="SENTETİK Türkçe demo verisi yükler (yalnızca SZ_ENVIRONMENT=development).",
    )
    parser.add_argument(
        "--database-url", help="Varsayılan SZ_DATABASE_URL (ör. sqlite+aiosqlite:///./demo.db)"
    )
    args = parser.parse_args(argv)
    try:
        check_environment(get_settings())
        summary = asyncio.run(run(args.database_url))
    except DemoSeedError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    counts = ", ".join(f"{k}={v}" for k, v in summary.counts.items())
    if summary.created:
        print(f"Demo verisi yüklendi (SENTETİK): {counts}")
    else:
        print(f"Demo verisi zaten yüklü; hiçbir şey eklenmedi ({counts}).")
    print(login_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

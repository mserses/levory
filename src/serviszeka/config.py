"""Uygulama ayarları (ortam değişkenleri, SZ_ önekiyle)."""

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class EventCIRule(BaseModel):
    """İzleme etiketi -> CMDB CI eşleme kuralı (bkz. services/events.resolve_ci)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    match: Literal["id", "name", "external_ref", "attribute"] = "name"
    key: str | None = Field(default=None, max_length=100)
    strip_port: bool = False


DEFAULT_EVENT_CI_RULES: list[dict[str, Any]] = [
    {"label": "ci_id", "match": "id"},
    {"label": "ci", "match": "name"},
    {"label": "instance", "match": "name", "strip_port": True},
    {"label": "service", "match": "name"},
    {"label": "job", "match": "name"},
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SZ_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./serviszeka.db"
    # Açılışta create_all ile şema kurulsun mu (geliştirme/test). Üretimde false yapın ve
    # şemayı `alembic upgrade head` ile kurun.
    auto_create_schema: bool = True

    # LLM
    anthropic_api_key: str | None = None
    # LLM sağlayıcısı: anthropic (varsayılan) | fake. fake, API anahtarı olmadan deterministik
    # sınıflandırma döndürür (yerel deneme ve E2E testleri); yalnızca environment=development
    # iken kabul edilir, aksi halde uygulama açılmaz.
    llm_provider: Literal["anthropic", "fake"] = "anthropic"
    llm_reasoning_model: str = "claude-opus-5-5"
    llm_fast_model: str = "claude-haiku-5-5"
    # Yapay zekâ kararları bu güven eşiğinin altındaysa insana yönlendirilir.
    ai_confidence_threshold: float = 0.75
    # Gölge mod: ajanlar önerir ama uygulamaz.
    ai_shadow_mode: bool = True

    # Değişiklik yönetimi: normal değişikliğin yetkilendirilmesi için gereken en az CAB onayı
    cab_min_approvals: int = Field(default=2, ge=1)

    # Faz 3: onay bekleyen eylem isteğinin (ActionRequest) geçerlilik süresi (saat); dolunca
    # istek "expired" olur ve artık onaylanıp yürütülemez
    action_request_ttl_hours: int = Field(default=24, ge=1, le=720)

    # Faz 3: dış sistem bağlayıcıları (kimlik, posta, lisans, mesajlaşma) — serviszeka.connectors
    # none: bağlayıcı yok (identity.* / mailbox.* / license.* / notify.send_* araçları 503);
    # fake: bellek içi sahte dizin (yalnızca environment=development; yerel deneme ve E2E);
    # graph: Microsoft Entra ID + Microsoft 365 (Graph, client credentials) + Teams webhook
    # keycloak: Keycloak Admin REST API (yalnızca `identity`; connectors/keycloak.py)
    connector_provider: Literal["none", "fake", "graph", "keycloak"] = "none"
    # Ayrı sağlayıcı seçimi (boşsa SZ_CONNECTOR_PROVIDER'dan türetilir; ikisi çelişirse açılış
    # reddedilir). Kimlik: graph (Entra ID + M365 posta/lisans) | keycloak (yalnızca kimlik) |
    # memory (sahte dizin; yalnızca environment=development). Bildirim (`messaging`): teams (Graph
    # posta + Teams webhook; kimlik graph olmalı) | slack (SZ_SLACK_*) | log (bağlayıcı yok:
    # notify.send_* araçları 503, nöbetçi uyarısı yalnızca log/SZ_ONCALL_WEBHOOK_URL). Boşsa eski
    # davranış: kimlik yığınının bildiricisi, Slack ayarlıysa Slack. Ayrıntı connectors/setup.py.
    identity_provider: Literal["graph", "keycloak", "memory"] | None = None
    notifier_provider: Literal["teams", "slack", "log"] | None = None
    # Microsoft Graph uygulama kaydı (client credentials). Gizli bilgi: yalnızca ortamdan.
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_client_secret: SecretStr | None = None  # repr/günlükte görünmez
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    graph_login_url: str = "https://login.microsoftonline.com"
    graph_timeout: float = Field(default=10.0, gt=0)
    # Kullanıcı bildirimlerinin (notify.send_to_user) gönderildiği posta kutusu (Mail.Send)
    graph_sender_mailbox: str | None = None
    # Teams kanalı -> gelen webhook URL'si (JSON nesnesi): {"servis-masasi": "https://..."}
    teams_webhook_urls: dict[str, str] = {}
    # identity.add_to_group: bu izin listesindeki (grup kimliği) ve ayrıcalıklı olmayan gruplar R1;
    # listede olmayan ya da ayrıcalıklı gruplar R3. Virgülle ayrılmış liste ya da JSON dizisi.
    identity_standard_groups: Annotated[list[str], NoDecode] = []
    # Her zaman ayrıcalıklı sayılan gruplar (Graph'ta ayrıca isAssignableToRole=true gruplar)
    identity_privileged_groups: Annotated[list[str], NoDecode] = []
    # Keycloak (connector_provider=keycloak): hizmet hesabı etkin gizli istemci (client
    # credentials). Sunucu adresi /admin ve /realms öncesi taban (Keycloak <17: .../auth).
    keycloak_server_url: str | None = None
    keycloak_realm: str | None = None
    keycloak_client_id: str | None = None
    keycloak_client_secret: SecretStr | None = None
    # Yöneticinin kullanıcı adını taşıyan kullanıcı özniteliği (Keycloak'ta yerleşik yönetici yok)
    keycloak_manager_attribute: str = "manager"
    # İkincil (kişisel) e-posta adresini taşıyan kullanıcı özniteliği: geçici parola yalnızca bu
    # adrese gider (birincil e-postayla aynı ya da e-posta biçiminde olmayan değerler yok sayılır)
    keycloak_secondary_email_attribute: str = "secondary_email"
    # SMTP e-posta bildiricisi (connectors/smtp.py): posta sağlayıcısı olmayan kimlik yığınında
    # (Keycloak) kullanıcı bildirimi ve geçici parolanın ikincil adrese teslimi. Host + gönderen
    # doluysa etkin; parola gizli (yalnızca ortamdan).
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_sender: str | None = None
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    # Örtük TLS (SMTPS, genellikle 465): STARTTLS yerine bağlantı baştan TLS'tir (ikisi birlikte
    # geçersiz). TLS'siz (düz metin) SMTP yalnızca SZ_SMTP_ALLOW_PLAINTEXT=true VE
    # SZ_ENVIRONMENT=development iken kabul edilir; aksi halde uygulama açılmaz.
    smtp_ssl: bool = False
    smtp_allow_plaintext: bool = False
    # Slack (connectors/slack.py): bot belirteci (xoxb-..., gizli) varsa `messaging` bağlayıcısı
    # Slack olur (kanal mesajı + DM); hassas bildirim yine ikincil adrese (Graph/sahte) gider.
    slack_bot_token: SecretStr | None = None
    # notify.post_to_channel "default" / "oncall" kanalları (kanal kimliği önerilir, ör. C0123...)
    slack_default_channel: str | None = None
    slack_oncall_channel: str | None = None
    # İsteğe bağlı gelen webhook (gizli; tek kanala bağlı): bot yoksa kanal mesajı ve nöbetçi uyarısı
    slack_webhook_url: SecretStr | None = None

    # ITSM entegrasyonları
    servicenow_instance_url: str | None = None
    servicenow_username: str | None = None
    servicenow_password: str | None = None
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_service_desk_id: str | None = None
    # Boşsa hizmet masasının ilk istek türü kullanılır.
    jira_request_type_id: str | None = None
    # Senkron çakışma politikası: manual | remote_wins | local_wins
    sync_conflict_policy: Literal["manual", "remote_wins", "local_wins"] = "manual"

    # Nöbetçi bildirim webhook'u (boşsa bildirimler yalnızca loglanır)
    oncall_webhook_url: str | None = None
    # Kalıcı bildirim kuyruğu (notification_outbox): en fazla gönderim denemesi (sonra "dead") ve
    # üstel geri çekilme tabanı (sn; deneme n için taban * 2^(n-1), en fazla 1 saat)
    notification_max_attempts: int = Field(default=5, ge=1, le=50)
    notification_backoff_seconds: float = Field(default=30.0, gt=0)
    # Kuyruk saklama süresi (zamanlayıcı işi outbox_retention): gönderilmiş satırlar bu kadar gün,
    # ölü (dead) satırlar ayrı süre sonra toplu silinir. Açık olayın SLA tekilleştirme satırları
    # (`sla:*`) olay açık kaldıkça silinmez. Denetim kayıtları (notification.*) etkilenmez.
    outbox_retention_days: int = Field(default=30, ge=1, le=3650)
    outbox_dead_retention_days: int = Field(default=90, ge=1, le=3650)
    # Arka plan zamanlayıcısı (scheduler.py; FastAPI lifespan): kuyruk gönderimi, eylem isteği süre
    # aşımı, SLA ihlal/risk uyarısı, bekleyen hizmet talepleri. Birden fazla kopyada her işi tek
    # kopya çalıştırır (scheduler_leases). Testlerde kapalı.
    scheduler_enabled: bool = True
    scheduler_tick_seconds: float = Field(default=15.0, gt=0)
    # İş onayı bekleyen hizmet talebinin geçerlilik süresi (saat); dolunca talep reddedilir
    request_approval_ttl_hours: int = Field(default=168, ge=1, le=2160)

    # Faz 4 izleme / olay yönetimi (services/events.py, POST /events/alertmanager|generic).
    # Paylaşılan gizli: `Authorization: Bearer <gizli>` / `X-ServisZeka-Token` ya da HMAC-SHA256
    # imza (`X-ServisZeka-Signature: sha256=<hex>`, `X-ServisZeka-Timestamp`). Boşsa webhook kapalı.
    events_webhook_secret: SecretStr | None = None
    events_max_body_bytes: int = Field(default=1_048_576, ge=1024, le=20_971_520)
    # Zaman damgası penceresi ve yinelenen teslimat (gövde özeti) saklama süresi (saniye)
    events_replay_window_seconds: int = Field(default=300, ge=10, le=86_400)
    # Etiket -> CI eşleme kuralları (sırayla, ilk eşleşen kazanır), JSON dizisi:
    # [{"label": "instance", "match": "name", "strip_port": true}, ...]; match: id | name |
    # external_ref (key = external_refs anahtarı) | attribute (key = CI özniteliği)
    events_ci_mapping: list[EventCIRule] = Field(
        default_factory=lambda: [EventCIRule(**r) for r in DEFAULT_EVENT_CI_RULES]
    )
    # Bağıntı penceresi (dk): bu süre içinde olay görmüş açık gruplar aday
    events_correlation_window_minutes: int = Field(default=30, ge=1, le=1440)
    # Çırpınma: aynı parmak izi pencere içinde bu kadar kez ateşlenirse bastırılır
    events_flap_threshold: int = Field(default=4, ge=2, le=100)
    events_flap_window_minutes: int = Field(default=60, ge=1, le=1440)
    # Bu önem ve üstündeki grup olay açar (P1/P2 olacaksa insan onayı)
    events_incident_min_severity: Literal["critical", "high", "warning", "info"] = "high"
    # Olay fırtınası sınırı: olay ajanı son bir saatte izlemeden bu kadar olay açıldıysa yenisini
    # açmaz (gruplar açık kalır; yeniden gönderimde, sınırın altına inince açılır)
    events_max_incidents_per_hour: int = Field(default=20, ge=1, le=10_000)

    # Faz 4 sürekli iyileştirme (CSI; agents/improvement.py, zamanlayıcı işi `csi_review`):
    # sinyal penceresi (gün; önceki eşit pencereyle karşılaştırılır), uygulama sonrası ölçüm
    # penceresi (gün) ve reddedilen/kapanan sinyalin yeniden önerilmeden bekleme süresi (gün)
    csi_window_days: int = Field(default=30, ge=7, le=180)
    csi_measure_days: int = Field(default=30, ge=1, le=366)
    csi_cooldown_days: int = Field(default=30, ge=0, le=365)

    # Çalışma ortamı. Güvenli varsayılan: production (geliştirme kipi kimlik doğrulaması kapalı).
    environment: Literal["development", "test", "staging", "production"] = "production"

    # Kimlik doğrulama: oidc (Keycloak / Microsoft Entra ID bearer JWT) | dev
    # dev kipi yalnızca environment=development iken kabul edilir; aksi halde uygulama açılmaz.
    auth_mode: Literal["oidc", "dev"] = "oidc"
    # Belirteç yayıncısı (iss), örn. https://sso.ornek.com/realms/serviszeka veya
    # https://login.microsoftonline.com/<tenant-id>/v2.0
    auth_issuer: str | None = None
    # Beklenen hedef kitle (aud): istemci kimliği / uygulama URI'si
    auth_audience: str | None = None
    # Boşsa <issuer>/.well-known/openid-configuration içindeki jwks_uri kullanılır
    auth_jwks_url: str | None = None
    # Rollerin okunacağı talep (noktalı yol, örn. realm_access.roles). Boşsa Keycloak
    # (realm_access.roles, resource_access.<aud>.roles) ve Entra (roles) birlikte okunur.
    auth_roles_claim: str | None = None
    # Kullanıcı adı talebi; yoksa upn, email, sub sırasıyla denenir
    auth_username_claim: str = "preferred_username"
    # exp/nbf için saat kayması toleransı (saniye)
    auth_leeway_seconds: int = 30
    # JWKS / keşif istekleri için zaman aşımı (saniye)
    auth_http_timeout: float = 3.0

    # CORS: tarayıcıdan API'ye erişebilecek kökenler (örn. web portalı). Boşsa CORS kapalı.
    # Ortam değişkeninde virgülle ayrılmış liste ya da JSON dizisi:
    #   SZ_CORS_ORIGINS=http://localhost:5173,https://portal.ornek.com
    cors_origins: Annotated[list[str], NoDecode] = []

    @field_validator("identity_standard_groups", "identity_privileged_groups", mode="before")
    @classmethod
    def _split_groups(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [g.strip() for g in text.split(",") if g.strip()]
        return value

    @field_validator("identity_provider", "notifier_provider", mode="before")
    @classmethod
    def _blank_provider_is_unset(cls, value: Any) -> Any:
        # `.env.example`'dan kopyalanan boş değer "seçilmedi" demektir
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [o.strip().rstrip("/") for o in text.split(",") if o.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()

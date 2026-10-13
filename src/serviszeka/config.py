"""Uygulama ayarları (ortam değişkenleri, SZ_ önekiyle)."""

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

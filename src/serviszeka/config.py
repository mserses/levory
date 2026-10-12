"""Uygulama ayarları (ortam değişkenleri, SZ_ önekiyle)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SZ_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./serviszeka.db"

    # LLM
    anthropic_api_key: str | None = None
    llm_reasoning_model: str = "claude-opus-5-5"
    llm_fast_model: str = "claude-haiku-5-5"
    # Yapay zekâ kararları bu güven eşiğinin altındaysa insana yönlendirilir.
    ai_confidence_threshold: float = 0.75
    # Gölge mod: ajanlar önerir ama uygulamaz.
    ai_shadow_mode: bool = True

    # ITSM entegrasyonları
    servicenow_instance_url: str | None = None
    servicenow_username: str | None = None
    servicenow_password: str | None = None
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    jira_service_desk_id: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

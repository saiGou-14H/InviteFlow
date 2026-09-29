from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the skeleton service.

    Business integrations are intentionally disabled until a concrete provider contract
    is reviewed and implemented.
    """

    model_config = SettingsConfigDict(
        env_prefix="INVITEFLOW_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "InviteFlow"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    api_prefix: str = "/api/v1"
    business_hooks_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

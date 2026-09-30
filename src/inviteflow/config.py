from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVITEFLOW_", env_file=".env", extra="ignore")

    app_name: str = "InviteFlow"
    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    api_prefix: str = "/api/v1"
    business_hooks_enabled: bool = False
    database_url: SecretStr | None = None
    session_secret: SecretStr | None = None
    public_origin: str = "http://127.0.0.1:8000"
    cookie_secure: bool = True
    user_session_ttl_seconds: int = Field(default=2592000, ge=60, le=7776000)
    user_session_max_seconds: int = Field(default=7776000, ge=60)
    admin_session_ttl_seconds: int = Field(default=28800, ge=60, le=86400)
    admin_session_max_seconds: int = Field(default=86400, ge=60)
    login_attempt_limit: int = Field(default=5, ge=1, le=100)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)
    idempotency_ttl_seconds: int = Field(default=86400, ge=60)
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    max_request_bytes: int = Field(default=262144, ge=1024, le=1048576)
    session_cleanup_retention_seconds: int = Field(default=86400, ge=60, le=31536000)
    outbox_cleanup_retention_seconds: int = Field(default=604800, ge=60, le=31536000)

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path not in {"", "/"}
        ):
            raise ValueError("public_origin must be an exact HTTP(S) origin without path")
        self.public_origin = self.public_origin.rstrip("/")
        if self.database_url:
            if not self.database_url.get_secret_value().startswith("postgresql+asyncpg://"):
                raise ValueError("database_url must use postgresql+asyncpg")
            if not self.session_secret or len(self.session_secret.get_secret_value()) < 32:
                raise ValueError(
                    "configured persistence requires a session_secret of 32+ characters"
                )
        if self.environment == "production":
            if not self.database_url or not self.cookie_secure or origin.scheme != "https":
                raise ValueError("production requires PostgreSQL, secure cookies and HTTPS origin")
        if not self.api_prefix.startswith("/") or self.api_prefix.endswith("/"):
            raise ValueError("api_prefix must start with / and have no trailing slash")
        if self.user_session_ttl_seconds > self.user_session_max_seconds:
            raise ValueError("user session idle lifetime exceeds absolute lifetime")
        if self.admin_session_ttl_seconds > self.admin_session_max_seconds:
            raise ValueError("admin session idle lifetime exceeds absolute lifetime")
        return self

    @property
    def user_cookie_name(self) -> str:
        return "__Host-if-user" if self.cookie_secure else "if-user-dev"

    @property
    def admin_cookie_name(self) -> str:
        return "__Host-if-admin" if self.cookie_secure else "if-admin-dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()

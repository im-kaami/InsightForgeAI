import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["openai", "openai-compatible", "gemini", "groq", "ollama", "fake"]


class Price(BaseModel):
    """USD per million tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_provider: Provider = "openai"
    llm_prices: dict[str, Price] = Field(default_factory=dict)
    database_url: str = "sqlite:///./insightforge.db"
    storage_dir: Path = Path("./storage")
    jwt_secret: str = "change-me"
    app_secret: str = "change-me"
    auto_create_tables: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    access_token_minutes: int = 30
    refresh_token_days: int = Field(default=30, ge=1, le=365)
    scheduler_enabled: bool = True
    environment: Literal["development", "production"] = "development"
    allow_private_urls: bool = False
    allow_sqlite_files: bool = False
    allow_private_databases: bool | None = None
    query_timeout_seconds: int = 60
    duckdb_memory_limit: str = "2GB"
    duckdb_threads: int = 4
    max_concurrent_runs_per_user: int = 2
    max_runs_per_user_per_day: int = Field(default=500, ge=1)
    daily_cost_budget_usd: float | None = Field(default=None, gt=0)
    run_workers: int = Field(default=4, ge=1, le=32)
    run_max_attempts: int = Field(default=2, ge=1, le=5)
    app_base_url: str = "http://localhost:3000"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_timeout_seconds: int = 10
    login_max_failures: int = Field(default=5, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    register_max_per_hour: int = Field(default=30, ge=1)
    max_upload_bytes: int = 200_000_000
    llm_send_sample_values: bool = True
    llm_summary_max_rows: int = 20
    local_llm_model: str | None = None
    local_llm_base_url: str = "http://localhost:11434"
    local_llm_context_tokens: int = 8192
    local_llm_max_output_tokens: int = 1024
    local_llm_think: bool = False
    local_llm_timeout_seconds: int = 300
    deep_max_rounds: int = Field(default=3, ge=1, le=5)
    deep_max_seconds: int = Field(default=300, ge=10)
    deep_max_tokens: int = Field(default=40000, ge=1000)
    sandbox_enabled: bool = False
    sandbox_image: str = "insightforge-sandbox:1"
    sandbox_timeout_seconds: int = Field(default=30, ge=5, le=300)
    sandbox_memory: str = "512m"
    sandbox_cpus: str = "1"
    # Single sign-on with OpenID Connect (Phase 5f); off unless issuer, client and redirect are set.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_provider_name: str = "single sign-on"
    oidc_scopes: str = "openid email profile"
    oidc_allowed_domains: list[str] = Field(default_factory=list)
    # Where to send the browser after sign-in (the frontend's login page).
    oidc_frontend_url: str = "http://localhost:3000"

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def use_openai_key(cls, value: str | None) -> str | None:
        return value or os.getenv("OPENAI_API_KEY")

    @field_validator("local_llm_model", mode="before")
    @classmethod
    def blank_local_model_is_disabled(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        return value


def private_databases_allowed(settings: Settings) -> bool:
    """Whether database connections may point at private or local addresses (default: development only)."""
    if settings.allow_private_databases is not None:
        return settings.allow_private_databases
    return settings.environment != "production"


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    return is_loopback_host(urlparse(url).hostname)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_settings(settings: Settings) -> list[str]:
    problems = []
    if settings.jwt_secret == "change-me" or len(settings.jwt_secret) < 32:
        problems.append("JWT_SECRET must be at least 32 characters and not use change-me")
    if settings.app_secret == "change-me" or len(settings.app_secret) < 32:
        problems.append("APP_SECRET must be at least 32 characters and not use change-me")
    if settings.environment == "production" and settings.allow_private_urls:
        problems.append("ALLOW_PRIVATE_URLS must be false in production")
    if settings.environment == "production" and settings.allow_sqlite_files:
        problems.append("ALLOW_SQLITE_FILES must be false in production")
    if (
        settings.oidc_issuer
        and settings.environment == "production"
        and not settings.oidc_issuer.startswith("https://")
    ):
        problems.append("OIDC_ISSUER must use https in production")
    if settings.local_llm_model and not is_loopback_url(settings.local_llm_base_url):
        problems.append(
            "LOCAL_LLM_BASE_URL must point to this computer (localhost); the local model is disabled"
        )
    if (
        settings.smtp_host
        and settings.smtp_from
        and settings.smtp_security == "none"
        and not is_loopback_host(settings.smtp_host)
    ):
        problems.append(
            "SMTP_SECURITY=none sends email and the password unencrypted; use starttls or ssl "
            "unless SMTP_HOST is on this computer"
        )
    return problems

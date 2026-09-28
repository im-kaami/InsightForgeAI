import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["openai", "openai-compatible", "gemini", "groq", "ollama", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_provider: Provider = "openai"
    database_url: str = "sqlite:///./insightforge.db"
    storage_dir: Path = Path("./storage")
    jwt_secret: str = "change-me"
    app_secret: str = "change-me"
    auto_create_tables: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    access_token_minutes: int = 60 * 24 * 7
    scheduler_enabled: bool = True
    environment: Literal["development", "production"] = "development"
    allow_private_urls: bool = False
    query_timeout_seconds: int = 60
    duckdb_memory_limit: str = "2GB"
    duckdb_threads: int = 4
    max_concurrent_runs_per_user: int = 2
    max_upload_bytes: int = 200_000_000
    llm_send_sample_values: bool = True
    llm_summary_max_rows: int = 20
    local_llm_model: str | None = None
    local_llm_base_url: str = "http://localhost:11434"
    local_llm_context_tokens: int = 8192
    local_llm_max_output_tokens: int = 1024
    local_llm_think: bool = False
    local_llm_timeout_seconds: int = 300

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


def is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
    if settings.local_llm_model and not is_loopback_url(settings.local_llm_base_url):
        problems.append(
            "LOCAL_LLM_BASE_URL must point to this computer (localhost); the local model is disabled"
        )
    return problems

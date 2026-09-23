import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def use_openai_key(cls, value: str | None) -> str | None:
        return value or os.getenv("OPENAI_API_KEY")


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
    return problems

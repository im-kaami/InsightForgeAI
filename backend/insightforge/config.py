import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"
    database_url: str = "sqlite:///./insightforge.db"
    storage_dir: Path = Path("./storage")
    jwt_secret: str = "change-me"
    app_secret: str = "change-me"

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def use_openai_key(cls, value: str | None) -> str | None:
        return value or os.getenv("OPENAI_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()

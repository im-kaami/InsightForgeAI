from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Credentials(BaseModel):
    email: str
    password: str


class UserOut(APIModel):
    id: str
    email: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HealthOut(BaseModel):
    status: str
    llm: str
    provider: str
    model: str


class DatasetOut(APIModel):
    id: str
    name: str
    kind: str
    tables: list[str]
    schema_: dict[str, Any] = Field(alias="schema")
    sources: list[dict[str, Any]]
    created_at: datetime


class ConnectionOut(APIModel):
    id: str
    name: str
    kind: str
    redacted_uri: str


class RunOut(APIModel):
    id: str
    session_id: str
    goal: str
    status: str
    plan: dict[str, Any] | None = None
    summary: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    token_usage: dict[str, int] = Field(default_factory=dict)
    used_fallback_plan: bool = False
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class SessionOut(APIModel):
    id: str
    dataset_id: str
    title: str
    created_at: datetime
    runs: list[RunOut] = Field(default_factory=list)
    run_count: int = 0
    last_activity_at: datetime | None = None


class ScheduleOut(APIModel):
    id: str
    dataset_id: str
    session_id: str
    goal: str
    cron: str
    timezone: str = "UTC"
    enabled: bool
    last_run_at: datetime | None
    last_run_id: str | None
    next_run_at: datetime | None
    created_at: datetime


class ConnectionCreate(BaseModel):
    name: str
    uri: str


class URLDatasetCreate(BaseModel):
    url: str
    name: str | None = None
    sheets: list[str] | None = None


class ConnectionDatasetCreate(BaseModel):
    connection_id: str | None = None
    uri: str | None = None
    name: str
    tables: list[str] | None = None


class SessionCreate(BaseModel):
    dataset_id: str
    title: str | None = None


class RunCreate(BaseModel):
    goal: str


class ScheduleCreate(BaseModel):
    dataset_id: str
    session_id: str
    goal: str
    cron: str
    timezone: str = "UTC"
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    goal: str | None = None
    cron: str | None = None
    timezone: str | None = None
    enabled: bool | None = None

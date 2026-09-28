from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from insightforge.core.profiling import DataProfile
from insightforge.core.schema import DatasetNotes, SchemaInfo
from insightforge.core.verified_report import ReportPeriod, SalesDefinition


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
    local_model: str | None = None


class ImportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_name: str | None = Field(default=None, max_length=100)
    header_row: int = Field(default=1, ge=1, le=100)
    text_columns: list[str] = Field(default_factory=list, max_length=100)
    sheets: list[str] | None = None


class VersionOut(APIModel):
    id: str
    dataset_id: str
    base_version_id: str | None
    state: str
    sources: list[dict[str, Any]]
    schema_: SchemaInfo = Field(alias="schema")
    profile: DataProfile
    created_at: datetime
    confirmed_at: datetime | None


class VersionConfirm(BaseModel):
    confirmed: Literal[True]
    expected_current_version_id: str | None = None


class PrivacyUpdate(BaseModel):
    mode: Literal["local", "schema_only", "full"]
    acknowledged: Literal[True]


class DefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: SalesDefinition
    approved: Literal[True]
    previous_id: str | None = None


class DefinitionOut(APIModel):
    id: str
    dataset_id: str
    session_id: str
    name: str
    version: int
    previous_id: str | None
    definition: SalesDefinition
    approved_at: datetime


class ReportRunCreate(ReportPeriod):
    version_id: str


class DatasetOut(APIModel):
    id: str
    name: str
    kind: str
    tables: list[str]
    schema_: SchemaInfo = Field(alias="schema")
    sources: list[dict[str, Any]]
    current_version_id: str | None = None
    llm_policy: Literal["local", "schema_only", "full"] = "local"
    notes: DatasetNotes = Field(default_factory=DatasetNotes)
    profile: DataProfile | None = None
    review_version_id: str | None = None
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
    dataset_version_id: str | None = None
    definition_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    verification_status: str = "exploratory"
    warnings: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
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
    mode: Literal["quick", "deep"] = "quick"
    clarified: bool = False


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

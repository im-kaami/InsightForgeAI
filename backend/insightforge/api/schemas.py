from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from insightforge.core.metrics import Metric, MetricQuery, SavedMetrics
from insightforge.core.profiling import DataProfile
from insightforge.core.queries import SavedQueries
from insightforge.core.recipes import AppliedRecipe, RecipeSuggestion, SavedRecipe
from insightforge.core.relationships import SavedRelationships
from insightforge.core.schema import DatasetNotes, SchemaInfo
from insightforge.core.validation import RuleSuggestion, SavedRules, ValidationReport
from insightforge.core.verified_report import ReportPeriod, SalesDefinition


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Credentials(BaseModel):
    email: str
    password: str = Field(max_length=256)


class NewCredentials(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip().lower()
        name, _, domain = value.partition("@")
        if not 3 <= len(value) <= 320 or not name or not domain or "@" in domain or " " in value:
            raise ValueError("Enter a valid email address")
        return value


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class PasswordReset(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(min_length=8, max_length=256)


class UserOut(APIModel):
    id: str
    email: str
    created_at: datetime
    email_alerts: bool = False


class Preferences(BaseModel):
    email_alerts: bool


class ForgotPassword(BaseModel):
    email: str = Field(max_length=320)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ApiTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: Literal["read", "ask"] = "read"
    expires_in_days: int = Field(default=90, ge=1, le=365)


class ApiTokenOut(APIModel):
    id: str
    name: str
    scope: str
    prefix: str
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime | None


class ApiTokenCreated(ApiTokenOut):
    """Returned once, when the token is created. The token itself is never shown again."""

    token: str


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
    recipe: AppliedRecipe | None = None
    validation: ValidationReport | None = None
    created_at: datetime
    confirmed_at: datetime | None


class Suggestions(BaseModel):
    recipe_steps: list[RecipeSuggestion] = Field(default_factory=list)
    rules: list[RuleSuggestion] = Field(default_factory=list)
    method: str = "suggested by fixed checks on the data health check; no AI model is used"


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


class FollowIn(BaseModel):
    """Follow an approved metric: re-run its checked report and alert on large changes."""

    metric: str = Field(min_length=1, max_length=60)
    group_by: str | None = Field(default=None, max_length=200)
    days: int = Field(default=30, ge=1, le=366)
    threshold_percent: float = Field(default=10, gt=0, le=1000)
    cron: str | None = Field(default=None, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)
    on_new_data: bool = True
    enabled: bool = True


class MetricCheckOut(APIModel):
    id: str
    follow_id: str
    run_id: str | None
    trigger: str
    version_id: str | None
    status: str
    current: float | None
    previous: float | None
    change_percent: float | None
    message: str
    alert: bool
    acknowledged_at: datetime | None
    created_at: datetime | None
    metric: str | None = None
    metric_label: str | None = None
    dataset_id: str | None = None
    session_id: str | None = None


class FollowOut(APIModel):
    id: str
    dataset_id: str
    metric: str
    group_by: str | None
    days: int
    threshold_percent: float
    cron: str | None
    timezone: str
    on_new_data: bool
    enabled: bool
    last_run_id: str | None
    last_checked_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime | None
    last_check: MetricCheckOut | None = None
    open_alerts: int = 0


class ShareIn(BaseModel):
    kind: Literal["dashboard", "run"]
    target_id: str = Field(min_length=1, max_length=32)
    expires_in_days: int = Field(default=7, ge=1, le=90)
    # The owner must confirm that anyone with the link may see the data it shows.
    acknowledged: Literal[True]


class ShareOut(APIModel):
    id: str
    kind: str
    target_id: str
    title: str
    prefix: str
    expires_at: datetime
    revoked_at: datetime | None
    view_count: int
    last_viewed_at: datetime | None
    created_at: datetime | None


class ShareView(BaseModel):
    secret: str = Field(min_length=10, max_length=100)


class ShareCreated(ShareOut):
    """Returned once. The secret is part of the link and is never shown again."""

    secret: str


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)


class DashboardItemIn(BaseModel):
    """``pinned``: run_id + position; ``metric``: dataset_id + metric (+ days, group_by);
    ``question``: dataset_id + query_id."""

    kind: Literal["pinned", "metric", "question"]
    title: str | None = Field(default=None, max_length=300)
    run_id: str | None = None
    position: int | None = Field(default=None, ge=0)
    dataset_id: str | None = None
    metric: str | None = Field(default=None, max_length=60)
    days: int = Field(default=30, ge=1, le=366)
    group_by: str | None = Field(default=None, max_length=200)
    query_id: str | None = Field(default=None, max_length=40)


class DashboardItemOut(APIModel):
    id: str
    kind: str
    title: str
    position: int
    dataset_id: str | None
    config: dict[str, Any] = Field(validation_alias="config_json")
    snapshot: dict[str, Any] = Field(validation_alias="snapshot_json")
    source_run_id: str | None
    version_id: str | None
    refreshed_at: datetime | None
    error: str | None
    created_at: datetime | None


class DashboardOut(APIModel):
    id: str
    name: str
    description: str
    created_at: datetime | None
    updated_at: datetime | None
    items: list[DashboardItemOut] = Field(default_factory=list)
    item_count: int = 0
    workspace_id: str | None = None
    access: str = "own"


class WorkspaceShareIn(BaseModel):
    """Share with this workspace, or stop sharing (null)."""

    workspace_id: str | None = Field(default=None, max_length=32)


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberOut(BaseModel):
    user_id: str
    email: str
    role: str
    joined_at: datetime | None


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["viewer", "editor", "owner"] = "viewer"


class InviteOut(APIModel):
    id: str
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime | None


class InviteCreated(InviteOut):
    """Returned once; the secret is part of the invite link."""

    secret: str


class InviteAccept(BaseModel):
    secret: str = Field(min_length=10, max_length=100)


class RoleIn(BaseModel):
    role: Literal["viewer", "editor", "owner"]


class WorkspaceOut(BaseModel):
    id: str
    name: str
    role: str
    created_at: datetime | None
    members: list[MemberOut] = Field(default_factory=list)
    invites: list[InviteOut] = Field(default_factory=list)
    datasets: list[dict[str, str]] = Field(default_factory=list)
    dashboards: list[dict[str, str]] = Field(default_factory=list)


class MetricReportCreate(ReportPeriod):
    """A checked report for one approved metric; the current version is used when none is given."""

    metric: str = Field(min_length=1, max_length=60)
    group_by: str | None = Field(default=None, max_length=200)
    version_id: str | None = None


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
    recipe: SavedRecipe = Field(default_factory=SavedRecipe)
    rules: SavedRules = Field(default_factory=SavedRules)
    relationships: SavedRelationships = Field(default_factory=SavedRelationships)
    metrics: SavedMetrics = Field(default_factory=SavedMetrics)
    queries: SavedQueries = Field(default_factory=SavedQueries)
    workspace_id: str | None = None
    # The caller's access: "own", "edit" or "read" (set where it is known, else None).
    access: str | None = None
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
    llm_provider: str | None = None
    llm_model: str | None = None
    cost_usd: float | None = None
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


SCHEMA_NAME = r"^[A-Za-z_][A-Za-z0-9_$]*$"


class ConnectionDatasetCreate(BaseModel):
    connection_id: str | None = None
    uri: str | None = None
    name: str
    tables: list[str] | None = None
    schema_name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=SCHEMA_NAME, alias="schema"
    )

    model_config = ConfigDict(populate_by_name=True)


class ConnectionTest(BaseModel):
    uri: str = Field(min_length=1, max_length=2000)
    schema_name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=SCHEMA_NAME, alias="schema"
    )

    model_config = ConfigDict(populate_by_name=True)


class ConnectionTables(BaseModel):
    kind: str
    tables: list[str]
    redacted_uri: str


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

class SaveModelIn(BaseModel):
    name: str | None = None


class ScoreModelIn(BaseModel):
    version_id: str | None = None


class ModelScheduleIn(BaseModel):
    cron: str = Field(min_length=1, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)
    enabled: bool = True


class ModelScheduleOut(APIModel):
    id: str
    model_id: str
    cron: str
    timezone: str
    enabled: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class SavedModelOut(BaseModel):
    id: str
    dataset_id: str
    dataset_version_id: str
    run_id: str
    name: str
    task: str
    target: str
    features: list[str]
    date_column: str | None = None
    model_type: str
    metrics: dict[str, Any]
    created_at: str | None = None
    warning: str | None = None
    schedule: ModelScheduleOut | None = None
    open_alerts: int = 0


class ScoringOut(BaseModel):
    id: str
    model_id: str
    model_name: str | None = None
    dataset_id: str | None = None
    trigger: Literal["manual", "scheduled"]
    version_id: str | None = None
    status: Literal["completed", "failed"]
    rows_scored: int | None = None
    max_psi: float | None = None
    verdict: str | None = None
    reasons: list[str] = Field(default_factory=list)
    error: str | None = None
    alert: bool = False
    acknowledged_at: datetime | None = None
    created_at: datetime | None = None


class DriftFeatureOut(BaseModel):
    feature: str
    kind: str
    psi: float
    band: str
    unseen_share: float
    missing_change: float


class DriftReportOut(BaseModel):
    features: list[DriftFeatureOut]
    rows: int
    unseen_row_share: float
    max_psi: float


class RecommendationOut(BaseModel):
    verdict: str
    detail: str
    reasons: list[str]


class ScoreOut(BaseModel):
    scoring_id: str | None = None
    model_id: str
    version_id: str
    rows_scored: int
    preview_columns: list[str]
    preview_rows: list[dict[str, Any]]
    drift: DriftReportOut
    new_data_metrics: dict[str, float] | None = None
    holdout_metrics: dict[str, Any]
    recommendation: RecommendationOut


class ExplainIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ContributionOut(BaseModel):
    feature: str
    value: Any = None
    contribution: float


class ExplanationOut(BaseModel):
    model_id: str
    explained: str
    reference: float
    output: float
    prediction: Any = None
    contributions: list[ContributionOut]
    additivity_gap: float
    algorithm: str
    background_rows: int
    interpretation: str
    cautions: list[str]


class SaveQueryIn(BaseModel):
    """Save a result table's SQL from a run as an approved question."""

    run_id: str = Field(min_length=1, max_length=40)
    position: int = Field(ge=0)
    question: str | None = Field(default=None, max_length=300)
    description: str = Field(default="", max_length=500)


class MetricSuggestions(BaseModel):
    suggestions: list[Metric] = Field(default_factory=list)
    method: str = "drafted from column names and types only; no AI model is used and nothing is approved"


class SqlQueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    version_id: str | None = None


class SqlQueryOut(BaseModel):
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    total_rows: int
    truncated: bool
    full_row_count: int | None = None
    elapsed_ms: int


class MetricPreviewIn(BaseModel):
    metric: Metric
    query: MetricQuery | None = None


class MetricPreviewOut(BaseModel):
    metric: str
    label: str
    sql: str
    description: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int


class UsageTotals(BaseModel):
    runs: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    unpriced_runs: int


class UsageByModel(BaseModel):
    provider: str
    model: str
    runs: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    unpriced_runs: int


class UsageByDay(BaseModel):
    date: str
    runs: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    unpriced_runs: int


class UsageOut(BaseModel):
    days: int
    since: datetime
    totals: UsageTotals
    by_model: list[UsageByModel]
    by_day: list[UsageByDay]

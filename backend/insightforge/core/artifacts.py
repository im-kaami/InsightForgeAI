from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from insightforge.core.checks import ResultFinding
from insightforge.core.evidence import EvidenceItem, NumberCheck
from insightforge.core.planner import Clarification, Plan
from insightforge.core.trace import TraceEvent


class TableArtifact(BaseModel):
    name: str
    type: Literal["table"] = "table"
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
    truncated: bool = False
    full_row_count: int | None = None
    csv_path: str | None = None


class PlotArtifact(BaseModel):
    name: str
    type: Literal["plot"] = "plot"
    kind: str
    title: str
    figure: dict[str, Any]
    note: str | None = None
    data_source: str | None = None
    png_path: str | None = None


class TextArtifact(BaseModel):
    name: str
    type: Literal["text"] = "text"
    text: str


class ErrorArtifact(BaseModel):
    name: str
    type: Literal["error"] = "error"
    action: str
    message: str


Artifact = Annotated[
    TableArtifact | PlotArtifact | TextArtifact | ErrorArtifact, Field(discriminator="type")
]


class RunResult(BaseModel):
    goal: str
    plan: Plan
    artifacts: list[Artifact]
    summary: str
    timings: dict[str, float]
    token_usage: dict[str, int]
    used_fallback_plan: bool = False
    fallback_reason: str | None = None
    plan_issues: list[str] = Field(default_factory=list)
    number_check: NumberCheck | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    mode: Literal["quick", "deep"] = "quick"
    rounds: int = 1
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[ResultFinding] = Field(default_factory=list)
    deep_notes: list[str] = Field(default_factory=list)
    clarification: Clarification | None = None
    key_numbers: list[str] = Field(default_factory=list)

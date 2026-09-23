from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from insightforge.core.planner import Plan


class TableArtifact(BaseModel):
    name: str
    type: Literal["table"] = "table"
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
    csv_path: str | None = None


class PlotArtifact(BaseModel):
    name: str
    type: Literal["plot"] = "plot"
    kind: str
    title: str
    figure: dict[str, Any]
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

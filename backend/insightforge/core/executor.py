import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from insightforge.core.artifacts import (
    Artifact,
    ErrorArtifact,
    PlotArtifact,
    TableArtifact,
    TextArtifact,
)
from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.core.chart_data import AUTO_CHART_NOTE, QueryRunner, prepare_chart_data, suggest_chart
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import Plan, Planner, PlotStep, SqlStep, SummaryStep
from insightforge.core.plotter import figure_to_png, make_figure
from insightforge.core.schema import SchemaInfo
from insightforge.core.sql_guard import GuardedQuery, guard_query
from insightforge.core.summarizer import Summarizer
from insightforge.core.trace import Tracer


def truncation_note(retrieved: int, full_row_count: int | None) -> str:
    if full_row_count is None:
        return (
            f"Only the first {retrieved:,} rows were kept and the full row count could not be computed. "
            "Figures below cover the retrieved rows only."
        )
    return (
        f"The query produced {full_row_count:,} rows; only the first {retrieved:,} were kept. "
        "Figures below cover the retrieved rows only."
    )


class Executor:
    def __init__(
        self,
        catalog: DataCatalog,
        planner: Planner,
        summarizer: Summarizer,
        artifact_dir: Path | None = None,
        row_limit: int = 200,
        render_png: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        query_timeout: float | None = None,
        result_limit: int = 10000,
        auto_chart: bool = True,
    ):
        self.catalog = catalog
        self.planner = planner
        self.summarizer = summarizer
        self.artifact_dir = artifact_dir
        self.row_limit = row_limit
        self.render_png = render_png
        self.on_event = on_event
        self.query_timeout = query_timeout
        self.result_limit = result_limit
        self.auto_chart = auto_chart
        self.last_results: dict[str, pd.DataFrame] = {}
        self.last_row_counts: dict[str, list[int]] = {}
        self.summary_from_model = False
        self.tracer = Tracer()

    def _run_sql(self, query: str) -> tuple[GuardedQuery, pd.DataFrame]:
        guarded = guard_query(query, self.result_limit)
        return guarded, self.catalog.query(guarded.sql, timeout_seconds=self.query_timeout)

    def _full_row_count(self, guarded: GuardedQuery, frame: pd.DataFrame) -> tuple[bool, int | None]:
        if guarded.limit is None or guarded.count_sql is None or len(frame) < guarded.limit:
            return False, None
        try:
            count = self.catalog.query(guarded.count_sql, timeout_seconds=self.query_timeout)
            full_row_count = int(count.iloc[0, 0])
        except Exception:
            return True, None
        if full_row_count <= len(frame):
            return False, None
        return True, full_row_count

    def _source_query(self, full_sql: str) -> QueryRunner:
        def run(sql: str) -> pd.DataFrame:
            return self.catalog.query(f"WITH src AS ({full_sql}) {sql}", timeout_seconds=self.query_timeout)

        return run

    def _plot(
        self, step: PlotStep, frame: pd.DataFrame, source: tuple[GuardedQuery, bool, int | None]
    ) -> Artifact:
        guarded, truncated, full_row_count = source
        started = time.perf_counter()
        try:
            full_query = self._source_query(guarded.full_sql) if truncated and guarded.full_sql else None
            chart = prepare_chart_data(step, frame, full_query, full_row_count)
            figure = make_figure(
                chart.step, chart.frame, prebinned=chart.prebinned, bin_width=chart.bin_width
            )
            png_path = None
            if self.render_png and self.artifact_dir:
                path = figure_to_png(figure, self.artifact_dir / f"{sanitize_identifier(step.name)}.png")
                png_path = str(path) if path else None
            self.tracer.record(step.name, "chart", started, chart=step.kind, points=len(chart.frame))
            return PlotArtifact(
                name=step.name,
                kind=step.kind,
                title=step.title,
                figure=figure,
                note=chart.note,
                png_path=png_path,
            )
        except Exception as error:
            self.tracer.record(step.name, "chart", started, ok=False, error=str(error)[:300])
            return ErrorArtifact(name=step.name, action=step.action, message=str(error))

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def execute(
        self,
        goal: str,
        plan: Plan,
        schema: SchemaInfo,
        memory: ConversationMemory | None = None,
    ) -> tuple[list[Artifact], dict[str, float], dict[str, int], str]:
        artifacts: list[Artifact] = []
        timings: dict[str, float] = {}
        results: dict[str, pd.DataFrame] = {}
        self.last_results = results
        self.last_row_counts = {}
        self.summary_from_model = False
        sources: dict[str, tuple[GuardedQuery, bool, int | None]] = {}
        notes: dict[str, str] = {}
        summary = ""
        summary_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        step_names = {step.name for step in plan.steps}
        auto_chart = self.auto_chart and not any(isinstance(step, PlotStep) for step in plan.steps)
        if self.artifact_dir:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)

        for step in plan.steps:
            started = time.perf_counter()
            self._emit({"type": "step_start", "name": step.name, "action": step.action})
            if isinstance(step, SqlStep):
                repaired_sql = False
                try:
                    guarded, frame = self._run_sql(step.query)
                except Exception as first_error:
                    try:
                        repaired = self.planner.repair_sql(step, str(first_error), schema)
                        guarded, frame = self._run_sql(repaired.query)
                        repaired_sql = True
                    except Exception as second_error:
                        message = f"{first_error}; repair failed: {second_error}"
                        artifacts.append(
                            ErrorArtifact(name=step.name, action=step.action, message=message)
                        )
                        self.tracer.record(step.name, "sql", started, ok=False, error=message[:300])
                        timings[step.name] = time.perf_counter() - started
                        self._emit(
                            {
                                "type": "step_done",
                                "name": step.name,
                                "artifact": artifacts[-1].model_dump(mode="json"),
                            }
                        )
                        continue
                truncated, full_row_count = self._full_row_count(guarded, frame)
                self.tracer.record(
                    step.name,
                    "sql",
                    started,
                    rows=len(frame),
                    truncated=truncated,
                    full_row_count=full_row_count,
                    repaired=repaired_sql,
                )
                results[step.name] = frame
                self.last_row_counts[step.name] = [len(frame)] + (
                    [full_row_count] if full_row_count is not None else []
                )
                sources[step.name] = (guarded, truncated, full_row_count)
                if truncated:
                    notes[step.name] = truncation_note(len(frame), full_row_count)
                csv_path = None
                if self.artifact_dir:
                    path = self.artifact_dir / f"{sanitize_identifier(step.name)}.csv"
                    frame.to_csv(path, index=False)
                    csv_path = str(path)
                rows = json.loads(frame.head(self.row_limit).to_json(orient="records", date_format="iso"))
                artifacts.append(
                    TableArtifact(
                        name=step.name,
                        sql=guarded.sql,
                        columns=[str(column) for column in frame.columns],
                        rows=rows,
                        total_rows=len(frame),
                        truncated=truncated,
                        full_row_count=full_row_count,
                        csv_path=csv_path,
                    )
                )
            elif isinstance(step, PlotStep):
                frame = results.get(step.data_source)
                if frame is None:
                    artifacts.append(
                        ErrorArtifact(
                            name=step.name,
                            action=step.action,
                            message=f"Unknown data source: {step.data_source}",
                        )
                    )
                else:
                    artifacts.append(self._plot(step, frame, sources[step.data_source]))
            elif isinstance(step, SummaryStep):
                try:
                    summary, response = self.summarizer.summarize(
                        goal, results, step.focus, memory, notes
                    )
                    self.summary_from_model = response is not None
                    if response:
                        summary_usage["prompt_tokens"] += response.prompt_tokens
                        summary_usage["completion_tokens"] += response.completion_tokens
                    artifacts.append(TextArtifact(name=step.name, text=summary))
                except Exception as error:
                    artifacts.append(
                        ErrorArtifact(name=step.name, action=step.action, message=str(error))
                    )
            timings[step.name] = time.perf_counter() - started
            self._emit(
                {
                    "type": "step_done",
                    "name": step.name,
                    "artifact": artifacts[-1].model_dump(mode="json"),
                }
            )
            if auto_chart and isinstance(step, SqlStep) and step.name in results:
                suggestion = suggest_chart(step.name, results[step.name])
                if suggestion is not None:
                    auto_chart = False
                    name = suggestion.name
                    while name in step_names:
                        name = f"{name}_auto"
                    suggestion = suggestion.model_copy(update={"name": name})
                    artifact = self._plot(suggestion, results[step.name], sources[step.name])
                    if isinstance(artifact, PlotArtifact):
                        artifact.note = f"{AUTO_CHART_NOTE} {artifact.note or ''}".strip()
                        artifacts.append(artifact)
                        self._emit(
                            {
                                "type": "step_done",
                                "name": name,
                                "artifact": artifact.model_dump(mode="json"),
                            }
                        )

        token_usage = {
            key: self.planner.last_usage[key] + summary_usage[key]
            for key in ("prompt_tokens", "completion_tokens")
        }
        return artifacts, timings, token_usage, summary

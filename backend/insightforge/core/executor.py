import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
from insightforge.core.checks import SERIOUS_FINDINGS, ResultFinding, check_result
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import Plan, Planner, PlotStep, SqlStep, StatStep, Step, SummaryStep
from insightforge.core.plotter import figure_to_png, make_figure
from insightforge.core.schema import SchemaInfo
from insightforge.core.sql_guard import GuardedQuery, guard_query
from insightforge.core.stats import StatArtifact, adjust_for_multiple_tests, run_test
from insightforge.core.summarizer import Summarizer
from insightforge.core.trace import Tracer

STAT_MAX_ROWS = 200_000


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


@dataclass
class ExecutionState:
    goal: str
    schema: SchemaInfo
    memory: ConversationMemory | None
    step_names: set[str]
    auto_chart: bool
    artifacts: list[Artifact] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    results: dict[str, pd.DataFrame] = field(default_factory=dict)
    sources: dict[str, tuple[GuardedQuery, bool, int | None]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    row_counts: dict[str, list[int]] = field(default_factory=dict)
    findings: list[ResultFinding] = field(default_factory=list)
    summary: str = ""
    summary_usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
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
                data_source=step.data_source,
                png_path=png_path,
            )
        except Exception as error:
            self.tracer.record(step.name, "chart", started, ok=False, error=str(error)[:300])
            return ErrorArtifact(name=step.name, action=step.action, message=str(error))

    def _test(self, state: ExecutionState, step: StatStep, frame: pd.DataFrame) -> Artifact:
        started = time.perf_counter()
        guarded, truncated, full_row_count = state.sources[step.data_source]
        note = None
        try:
            if truncated and guarded.full_sql:
                columns = ", ".join(
                    f'"{column.replace(chr(34), chr(34) * 2)}"' for column in (step.x, step.y)
                )
                frame = self._source_query(guarded.full_sql)(
                    f"SELECT {columns} FROM src USING SAMPLE reservoir({STAT_MAX_ROWS} ROWS) REPEATABLE (42)"
                )
                note = (
                    f"Computed on a random sample of {STAT_MAX_ROWS:,} of {full_row_count:,} rows."
                    if full_row_count and full_row_count > STAT_MAX_ROWS
                    else f"Computed on all {len(frame):,} rows, not only the {self.result_limit:,} shown."
                )
            artifact = run_test(step.name, step.method, step.data_source, step.x, step.y, frame, note)
        except Exception as error:
            self.tracer.record(
                step.name, "test", started, ok=False, method=step.method, error=str(error)[:300]
            )
            return ErrorArtifact(name=step.name, action=step.action, message=str(error))
        state.results[step.name] = artifact.key_table()
        state.notes[step.name] = f"{artifact.test} (tested method): {artifact.interpretation}"
        self.tracer.record(
            step.name,
            "test",
            started,
            method=step.method,
            test=artifact.test,
            n=artifact.n,
            rows_sampled=bool(note),
        )
        return artifact

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def start(
        self,
        goal: str,
        plan: Plan,
        schema: SchemaInfo,
        memory: ConversationMemory | None = None,
    ) -> ExecutionState:
        state = ExecutionState(
            goal=goal,
            schema=schema,
            memory=memory,
            step_names={step.name for step in plan.steps},
            auto_chart=self.auto_chart and not any(isinstance(step, PlotStep) for step in plan.steps),
        )
        self.last_results = state.results
        self.last_row_counts = state.row_counts
        self.summary_from_model = False
        if self.artifact_dir:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
        return state

    def execute(
        self,
        goal: str,
        plan: Plan,
        schema: SchemaInfo,
        memory: ConversationMemory | None = None,
    ) -> tuple[list[Artifact], dict[str, float], dict[str, int], str]:
        state = self.start(goal, plan, schema, memory)
        self.run_steps(state, plan.steps)
        return self.finish(state)

    def finish(self, state: ExecutionState) -> tuple[list[Artifact], dict[str, float], dict[str, int], str]:
        adjust_for_multiple_tests(
            [artifact for artifact in state.artifacts if isinstance(artifact, StatArtifact)]
        )
        token_usage = {
            key: self.planner.last_usage[key] + state.summary_usage[key]
            for key in ("prompt_tokens", "completion_tokens")
        }
        return state.artifacts, state.timings, token_usage, state.summary

    def _forget(self, state: ExecutionState, name: str) -> None:
        for artifact in state.artifacts:
            if isinstance(artifact, StatArtifact) and artifact.data_source == name:
                state.results.pop(artifact.name, None)
                state.notes.pop(artifact.name, None)
        state.artifacts = [
            artifact
            for artifact in state.artifacts
            if artifact.name != name and getattr(artifact, "data_source", None) != name
        ]
        for mapping in (state.results, state.sources, state.notes, state.row_counts):
            mapping.pop(name, None)
        state.findings = [finding for finding in state.findings if finding.step != name]
        if not any(isinstance(artifact, PlotArtifact) for artifact in state.artifacts):
            state.auto_chart = self.auto_chart

    def _check(self, state: ExecutionState, step: SqlStep, sql: str, frame: pd.DataFrame) -> None:
        started = time.perf_counter()
        findings = check_result(step.name, sql, frame, self.catalog, self.query_timeout)
        if findings:
            state.findings.extend(findings)
            self.tracer.record(
                f"checks:{step.name}",
                "check",
                started,
                ok=not any(finding.code in SERIOUS_FINDINGS for finding in findings),
                findings=[finding.code for finding in findings],
            )

    def run_steps(self, state: ExecutionState, steps: list[Step]) -> None:
        state.step_names |= {step.name for step in steps}
        for step in steps:
            started = time.perf_counter()
            self._emit({"type": "step_start", "name": step.name, "action": step.action})
            if isinstance(step, SqlStep):
                if step.name in state.results or any(item.name == step.name for item in state.artifacts):
                    self._forget(state, step.name)
                repaired_sql = False
                try:
                    guarded, frame = self._run_sql(step.query)
                except Exception as first_error:
                    try:
                        repaired = self.planner.repair_sql(step, str(first_error), state.schema)
                        guarded, frame = self._run_sql(repaired.query)
                        repaired_sql = True
                    except Exception as second_error:
                        message = f"{first_error}; repair failed: {second_error}"
                        state.artifacts.append(
                            ErrorArtifact(name=step.name, action=step.action, message=message)
                        )
                        self.tracer.record(step.name, "sql", started, ok=False, error=message[:300])
                        state.timings[step.name] = time.perf_counter() - started
                        self._emit(
                            {
                                "type": "step_done",
                                "name": step.name,
                                "artifact": state.artifacts[-1].model_dump(mode="json"),
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
                state.results[step.name] = frame
                state.row_counts[step.name] = [len(frame)] + (
                    [full_row_count] if full_row_count is not None else []
                )
                state.sources[step.name] = (guarded, truncated, full_row_count)
                if truncated:
                    state.notes[step.name] = truncation_note(len(frame), full_row_count)
                self._check(state, step, guarded.sql, frame)
                csv_path = None
                if self.artifact_dir:
                    path = self.artifact_dir / f"{sanitize_identifier(step.name)}.csv"
                    frame.to_csv(path, index=False)
                    csv_path = str(path)
                rows = json.loads(frame.head(self.row_limit).to_json(orient="records", date_format="iso"))
                state.artifacts.append(
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
                frame = state.results.get(step.data_source)
                state.artifacts = [item for item in state.artifacts if item.name != step.name]
                if frame is None:
                    state.artifacts.append(
                        ErrorArtifact(
                            name=step.name,
                            action=step.action,
                            message=f"Unknown data source: {step.data_source}",
                        )
                    )
                else:
                    state.artifacts.append(self._plot(step, frame, state.sources[step.data_source]))
                    state.auto_chart = False
            elif isinstance(step, StatStep):
                self._forget(state, step.name)
                frame = state.results.get(step.data_source)
                if frame is None:
                    failed = any(
                        isinstance(item, ErrorArtifact) and item.name == step.data_source
                        for item in state.artifacts
                    )
                    message = (
                        f"The data step {step.data_source!r} failed, so the test could not run"
                        if failed
                        else f"Unknown data source: {step.data_source}"
                    )
                    state.artifacts.append(ErrorArtifact(name=step.name, action=step.action, message=message))
                else:
                    state.artifacts.append(self._test(state, step, frame))
            elif isinstance(step, SummaryStep):
                try:
                    summary, response = self.summarizer.summarize(
                        state.goal, state.results, step.focus, state.memory, state.notes
                    )
                    state.summary = summary
                    self.summary_from_model = response is not None
                    if response:
                        state.summary_usage["prompt_tokens"] += response.prompt_tokens
                        state.summary_usage["completion_tokens"] += response.completion_tokens
                    state.artifacts.append(TextArtifact(name=step.name, text=summary))
                except Exception as error:
                    state.artifacts.append(
                        ErrorArtifact(name=step.name, action=step.action, message=str(error))
                    )
            state.timings[step.name] = time.perf_counter() - started
            self._emit(
                {
                    "type": "step_done",
                    "name": step.name,
                    "artifact": state.artifacts[-1].model_dump(mode="json"),
                }
            )
            if state.auto_chart and isinstance(step, SqlStep) and step.name in state.results:
                self._auto_chart(state, step.name)

    def _auto_chart(self, state: ExecutionState, source: str) -> None:
        suggestion = suggest_chart(source, state.results[source])
        if suggestion is None:
            return
        state.auto_chart = False
        name = suggestion.name
        while name in state.step_names:
            name = f"{name}_auto"
        suggestion = suggestion.model_copy(update={"name": name})
        artifact = self._plot(suggestion, state.results[source], state.sources[source])
        if isinstance(artifact, PlotArtifact):
            artifact.note = f"{AUTO_CHART_NOTE} {artifact.note or ''}".strip()
            state.artifacts.append(artifact)
            self._emit({"type": "step_done", "name": name, "artifact": artifact.model_dump(mode="json")})

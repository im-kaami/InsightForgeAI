import json
import time
from pathlib import Path

import pandas as pd

from insightforge.core.artifacts import (
    Artifact,
    ErrorArtifact,
    PlotArtifact,
    TableArtifact,
    TextArtifact,
)
from insightforge.core.catalog import DataCatalog, sanitize_identifier
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import Plan, Planner, PlotStep, SqlStep, SummaryStep
from insightforge.core.plotter import figure_to_png, make_figure
from insightforge.core.schema import SchemaInfo
from insightforge.core.sql_guard import guard_sql
from insightforge.core.summarizer import Summarizer


class Executor:
    def __init__(
        self,
        catalog: DataCatalog,
        planner: Planner,
        summarizer: Summarizer,
        artifact_dir: Path | None = None,
        row_limit: int = 200,
        render_png: bool = False,
    ):
        self.catalog = catalog
        self.planner = planner
        self.summarizer = summarizer
        self.artifact_dir = artifact_dir
        self.row_limit = row_limit
        self.render_png = render_png

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
        summary = ""
        summary_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        if self.artifact_dir:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)

        for step in plan.steps:
            started = time.perf_counter()
            if isinstance(step, SqlStep):
                executed_sql = step.query
                try:
                    executed_sql = guard_sql(executed_sql)
                    frame = self.catalog.query(executed_sql)
                except Exception as first_error:
                    try:
                        repaired = self.planner.repair_sql(step, str(first_error), schema)
                        executed_sql = guard_sql(repaired.query)
                        frame = self.catalog.query(executed_sql)
                    except Exception as second_error:
                        artifacts.append(
                            ErrorArtifact(
                                name=step.name,
                                action=step.action,
                                message=f"{first_error}; repair failed: {second_error}",
                            )
                        )
                        timings[step.name] = time.perf_counter() - started
                        continue
                results[step.name] = frame
                csv_path = None
                if self.artifact_dir:
                    path = self.artifact_dir / f"{sanitize_identifier(step.name)}.csv"
                    frame.to_csv(path, index=False)
                    csv_path = str(path)
                rows = json.loads(frame.head(self.row_limit).to_json(orient="records", date_format="iso"))
                artifacts.append(
                    TableArtifact(
                        name=step.name,
                        sql=executed_sql,
                        columns=[str(column) for column in frame.columns],
                        rows=rows,
                        total_rows=len(frame),
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
                    try:
                        figure = make_figure(step, frame)
                        png_path = None
                        if self.render_png and self.artifact_dir:
                            path = figure_to_png(
                                figure, self.artifact_dir / f"{sanitize_identifier(step.name)}.png"
                            )
                            png_path = str(path) if path else None
                        artifacts.append(
                            PlotArtifact(
                                name=step.name,
                                kind=step.kind,
                                title=step.title,
                                figure=figure,
                                png_path=png_path,
                            )
                        )
                    except Exception as error:
                        artifacts.append(
                            ErrorArtifact(name=step.name, action=step.action, message=str(error))
                        )
            elif isinstance(step, SummaryStep):
                try:
                    summary, response = self.summarizer.summarize(
                        goal, results, step.focus, memory
                    )
                    if response:
                        summary_usage["prompt_tokens"] += response.prompt_tokens
                        summary_usage["completion_tokens"] += response.completion_tokens
                    artifacts.append(TextArtifact(name=step.name, text=summary))
                except Exception as error:
                    artifacts.append(
                        ErrorArtifact(name=step.name, action=step.action, message=str(error))
                    )
            timings[step.name] = time.perf_counter() - started

        token_usage = {
            key: self.planner.last_usage[key] + summary_usage[key]
            for key in ("prompt_tokens", "completion_tokens")
        }
        return artifacts, timings, token_usage, summary

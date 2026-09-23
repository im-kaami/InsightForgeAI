import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from insightforge.core.artifacts import RunResult, TextArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.executor import Executor
from insightforge.core.llm import LLMClient
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import Planner
from insightforge.core.summarizer import Summarizer


class InsightForgeAgent:
    def __init__(
        self,
        llm: LLMClient,
        artifact_dir: Path | None = None,
        render_png: bool = False,
        query_timeout: float | None = None,
        summary_max_rows: int = 20,
        schema_sample_rows: int = 3,
    ):
        self.planner = Planner(llm)
        self.summarizer = Summarizer(llm, max_rows=summary_max_rows)
        self.artifact_dir = artifact_dir
        self.render_png = render_png
        self.query_timeout = query_timeout
        self.schema_sample_rows = schema_sample_rows

    def run(
        self,
        goal: str,
        catalog: DataCatalog,
        memory: ConversationMemory | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> RunResult:
        def emit(event: dict[str, Any]) -> None:
            if on_event:
                try:
                    on_event(event)
                except Exception:
                    pass

        started = time.perf_counter()
        schema = catalog.introspect(sample_rows=self.schema_sample_rows)
        emit({"type": "planning"})
        plan = self.planner.plan(goal, schema, memory)
        emit(
            {
                "type": "plan",
                "plan": plan.model_dump(mode="json"),
                "used_fallback": self.planner.last_used_fallback,
                "fallback_reason": self.planner.last_fallback_reason,
            }
        )
        executor = Executor(
            catalog,
            self.planner,
            self.summarizer,
            self.artifact_dir,
            render_png=self.render_png,
            on_event=on_event,
            query_timeout=self.query_timeout,
        )
        artifacts, timings, token_usage, summary = executor.execute(
            goal, plan, schema, memory
        )
        text_artifacts = [artifact for artifact in artifacts if isinstance(artifact, TextArtifact)]
        if text_artifacts:
            summary = text_artifacts[-1].text
        else:
            summary, response = self.summarizer.summarize(goal, {}, memory=memory)
            if response:
                token_usage["prompt_tokens"] += response.prompt_tokens
                token_usage["completion_tokens"] += response.completion_tokens
            artifacts.append(TextArtifact(name="summary", text=summary))
        if memory is not None:
            memory.add(goal, summary, [table.name for table in schema.tables])
        timings["total"] = time.perf_counter() - started
        emit({"type": "done", "summary": summary})
        return RunResult(
            goal=goal,
            plan=plan,
            artifacts=artifacts,
            summary=summary,
            timings=timings,
            token_usage=token_usage,
            used_fallback_plan=self.planner.last_used_fallback,
            fallback_reason=self.planner.last_fallback_reason,
        )

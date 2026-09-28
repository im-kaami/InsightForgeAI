import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from insightforge.core.artifacts import RunResult, TableArtifact, TextArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.evidence import check_numbers, describe_query, extract_numbers, sql_literals
from insightforge.core.executor import ExecutionState, Executor
from insightforge.core.llm import LLMClient, describe_error
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import Plan, Planner, SummaryStep
from insightforge.core.privacy import PrivacyMode, PromptPolicy
from insightforge.core.schema import SchemaInfo
from insightforge.core.summarizer import Summarizer
from insightforge.core.trace import Tracer


def without_samples(schema: SchemaInfo) -> SchemaInfo:
    return SchemaInfo(
        tables=[
            table.model_copy(
                update={
                    "columns": [
                        column.model_copy(update={"sample_values": []}) for column in table.columns
                    ]
                }
            )
            for table in schema.tables
        ]
    )


class InsightForgeAgent:
    def __init__(
        self,
        llm: LLMClient,
        artifact_dir: Path | None = None,
        render_png: bool = False,
        query_timeout: float | None = None,
        summary_max_rows: int = 20,
        schema_sample_rows: int = 3,
        privacy_mode: PrivacyMode = "schema_only",
        local_llm: LLMClient | None = None,
        deep_max_rounds: int = 3,
        deep_max_seconds: float = 300,
        deep_max_tokens: int = 40000,
    ):
        self.deep_max_rounds = deep_max_rounds
        self.deep_max_seconds = deep_max_seconds
        self.deep_max_tokens = deep_max_tokens
        self.policy = PromptPolicy(privacy_mode, local_model=local_llm is not None)
        self.planner = Planner(llm, privacy_mode=privacy_mode, local_llm=local_llm)
        self.summarizer = Summarizer(
            llm, max_rows=summary_max_rows, privacy_mode=privacy_mode, local_llm=local_llm
        )
        self.artifact_dir = artifact_dir
        self.render_png = render_png
        self.query_timeout = query_timeout
        self.schema_sample_rows = 0 if privacy_mode == "schema_only" else schema_sample_rows

    def run(
        self,
        goal: str,
        catalog: DataCatalog,
        memory: ConversationMemory | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        schema: SchemaInfo | None = None,
        mode: Literal["quick", "deep"] = "quick",
        allow_clarification: bool = False,
    ) -> RunResult:
        def emit(event: dict[str, Any]) -> None:
            if on_event:
                try:
                    on_event(event)
                except Exception:
                    pass

        started = time.perf_counter()
        tracer = Tracer()
        self.planner.tracer = tracer
        self.summarizer.tracer = tracer
        if schema is None:
            schema = catalog.introspect(sample_rows=self.schema_sample_rows)
        elif self.schema_sample_rows == 0:
            schema = without_samples(schema)
        emit({"type": "planning"})
        plan = self.planner.plan(goal, schema, memory, allow_clarification)
        clarification = self.planner.last_clarification
        if clarification is not None:
            emit({"type": "clarify", "question": clarification.question})
            emit({"type": "done", "summary": clarification.question})
            return RunResult(
                goal=goal,
                plan=plan,
                artifacts=[],
                summary=clarification.question,
                timings={"total": time.perf_counter() - started},
                token_usage=dict(self.planner.last_usage),
                trace=tracer.events,
                mode=mode,
                clarification=clarification,
            )
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
        executor.tracer = tracer
        rounds, reviews, deep_notes = 1, [], []
        if mode == "deep":
            state, rounds, reviews, deep_notes = self._deep(
                goal, plan, schema, memory, executor, tracer, started, emit
            )
        else:
            state = executor.start(goal, plan, schema, memory)
            executor.run_steps(state, plan.steps)
        artifacts, timings, token_usage, summary = executor.finish(state)
        text_artifacts = [artifact for artifact in artifacts if isinstance(artifact, TextArtifact)]
        tables = [artifact for artifact in artifacts if isinstance(artifact, TableArtifact)]
        assumptions = [
            describe_query(table.name, table.sql, schema, executor.result_limit) for table in tables
        ]
        for table in tables:
            if table.truncated:
                full = f"{table.full_row_count:,}" if table.full_row_count else "an unknown number of"
                assumptions.append(
                    f"{table.name}: only the first {table.total_rows:,} of {full} result rows were kept."
                )
        number_check, evidence = None, []
        if text_artifacts:
            summary = text_artifacts[-1].text
            if executor.summary_from_model:
                check_started = time.perf_counter()
                ignore = {claim.value for claim in extract_numbers(goal)}
                for table in tables:
                    ignore |= sql_literals(table.sql)
                number_check, evidence = check_numbers(
                    summary, executor.last_results, executor.last_row_counts, ignore
                )
                tracer.record(
                    "summary_numbers",
                    "check",
                    check_started,
                    ok=not number_check.unmatched,
                    checked=number_check.checked,
                    matched=number_check.matched,
                    unmatched=number_check.unmatched,
                )
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
            plan_issues=self.planner.last_plan_issues,
            number_check=number_check,
            evidence=evidence,
            assumptions=assumptions,
            trace=tracer.events,
            mode=mode,
            rounds=rounds,
            reviews=reviews,
            findings=state.findings,
            deep_notes=deep_notes,
        )

    def _deep(
        self,
        goal: str,
        plan: Plan,
        schema: SchemaInfo,
        memory: ConversationMemory | None,
        executor: Executor,
        tracer: Tracer,
        started: float,
        emit: Callable[[dict[str, Any]], None],
    ) -> tuple[ExecutionState, int, list[dict[str, Any]], list[str]]:
        state = executor.start(goal, plan, schema, memory)
        summaries = [step for step in plan.steps if isinstance(step, SummaryStep)]
        executor.run_steps(state, [step for step in plan.steps if not isinstance(step, SummaryStep)])
        rounds, reviews, notes = 1, [], []
        if getattr(self.planner.llm, "offline", False):
            notes.append("Deep mode needs an AI model, so the analysis ran once without a review.")
        while not notes and rounds < self.deep_max_rounds:
            if time.perf_counter() - started > self.deep_max_seconds:
                limit = f"{self.deep_max_seconds:g}-second"
                notes.append(f"Deep mode stopped after {rounds} round(s) at its {limit} limit.")
                break
            if sum(self.planner.last_usage.values()) > self.deep_max_tokens:
                notes.append(
                    f"Deep mode stopped after {rounds} round(s) at its {self.deep_max_tokens:,}-token limit."
                )
                break
            show_values = self.planner.policy.values_visible_to_model
            tables = {name: (state.sources[name][0].sql, frame) for name, frame in state.results.items()}
            findings = [item.message if show_values else item.model_message for item in state.findings]
            decided = time.perf_counter()
            try:
                decision = self.planner.review(goal, schema, tables, findings, rounds)
            except Exception as error:
                notes.append(
                    f"Review {rounds} failed ({describe_error(error)}), "
                    "so the results were summarized as they were."
                )
                break
            outcome = {"verdict": decision.verdict, "reason": decision.reason}
            names = [step.name for step in decision.steps]
            tracer.record(f"review:{rounds}", "decision", decided, **outcome, steps=names)
            reviews.append({"round": rounds, **outcome, "steps": names})
            self.planner.last_plan_issues.extend(decision.issues)
            emit({"type": "review", "name": f"review {rounds}", "verdict": decision.verdict})
            if decision.verdict == "answer" or not decision.steps:
                break
            executor.run_steps(state, decision.steps)
            rounds += 1
        executor.run_steps(state, summaries[:1] or [SummaryStep(name="summary")])
        return state, rounds, reviews, notes

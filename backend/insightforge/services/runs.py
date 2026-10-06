import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from insightforge.config import get_settings
from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import PlotArtifact, RunResult, TableArtifact, TextArtifact
from insightforge.core.checks import SERIOUS_FINDINGS
from insightforge.core.costs import estimate_cost, price_for
from insightforge.core.executor import truncation_note
from insightforge.core.llm import LLMClient, build_llm, build_local_llm, llm_mode, resolved_model
from insightforge.core.memory import ConversationMemory
from insightforge.core.metric_report import ARTIFACT as METRIC_ARTIFACT
from insightforge.core.metric_report import calculate_metric_report
from insightforge.core.metrics import Metric, MetricError, SavedMetrics
from insightforge.core.planner import Plan, PlotStep, SqlStep, SummaryStep
from insightforge.core.plotter import figure_to_png, make_figure
from insightforge.core.queries import SavedQueries
from insightforge.core.relationships import Relationship, SavedRelationships
from insightforge.core.sandbox import build_sandbox
from insightforge.core.schema import DatasetNotes, SchemaInfo
from insightforge.core.validation import SavedRules, check_rules
from insightforge.core.verified_report import (
    CalculationCheck,
    ReportPeriod,
    ReportValidationError,
    SalesDefinition,
    calculate_sales_report,
)
from insightforge.db.models import Artifact, ChatSession, Connection, Dataset, DatasetVersion, Run, User
from insightforge.db.session import SessionLocal, configure
from insightforge.services.access import dataset_access
from insightforge.services.crypto import decrypt
from insightforge.services.datasets import ensure_current_version, open_catalog
from insightforge.services.events import RunEventBus
from insightforge.services.storage import Storage

REPORT_KINDS = ("sales_margin_v1", "metric_report_v1")


def _utc_iso(value: datetime) -> str:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def _persist_artifacts(db: Session, run: Run, artifacts: list[Any]) -> None:
    for position, artifact in enumerate(artifacts):
        payload = artifact.model_dump(mode="json")
        file_path = payload.get("png_path") or payload.get("csv_path")
        db.add(
            Artifact(
                run_id=run.id,
                position=position,
                type=artifact.type,
                name=artifact.name,
                payload_json=payload,
                file_path=file_path,
            )
        )


def _verified_result(
    run: Run,
    catalog: Any,
    version: DatasetVersion,
    run_dir: Any,
    timeout_seconds: int,
) -> tuple[RunResult, str, list[str]]:
    started = time.perf_counter()
    definition = SalesDefinition.model_validate(run.request_json["definition"])
    period = ReportPeriod.model_validate(run.request_json["period"])
    checked = calculate_sales_report(
        catalog,
        definition,
        period,
        timeout_seconds=timeout_seconds,
    )
    table_frame = pd.DataFrame(checked.rows, columns=checked.columns)
    csv_path = run_dir / "sales_metrics.csv"
    table_frame.to_csv(csv_path, index=False)
    table = TableArtifact(
        name="sales_metrics",
        sql=checked.sql,
        columns=checked.columns,
        rows=checked.rows,
        total_rows=len(checked.rows),
        csv_path=str(csv_path),
    )
    display_rows = [row for row in checked.rows if int(row["row_count"]) > 0]
    display_frame = pd.DataFrame(
        [
            {"period": row["period"], "net_sales": float(Decimal(row["net_sales"]))}
            for row in display_rows
        ]
    )
    plot_step = PlotStep(
        name="sales_comparison",
        kind="bar",
        data_source="sales_metrics",
        x="period",
        y="net_sales",
        title="Net sales by period (rounded display)",
    )
    figure = make_figure(plot_step, display_frame)
    png = figure_to_png(figure, run_dir / "sales_comparison.png")
    plot = PlotArtifact(
        name=plot_step.name,
        kind=plot_step.kind,
        title=plot_step.title,
        figure=figure,
        png_path=str(png) if png else None,
    )
    text = TextArtifact(name="summary", text=checked.summary)
    plan = Plan(
        steps=[
            SqlStep(name="sales_metrics", query=checked.sql),
            plot_step,
            SummaryStep(name="summary"),
        ]
    )
    run.provenance_json = {
        "kind": "sales_margin_v1",
        "source_version_id": version.id,
        "definition_id": run.definition_id,
        "definition_version": run.request_json["definition_version"],
        "definition": run.request_json["definition"],
        "period": run.request_json["period"],
        "imported_at": _utc_iso(version.created_at),
        "source_freshness": "unknown",
        "sources": version.sources_json,
        "privacy_mode": "local",
        "engine_version": checked.engine_version,
        "checks": [check.model_dump(mode="json") for check in checked.checks],
        "evidence": [item.model_dump(mode="json") for item in checked.evidence],
        "comparison": checked.comparison,
        "metric_definitions": checked.metric_definitions,
    }
    result = RunResult(
        goal=run.goal,
        plan=plan,
        artifacts=[table, plot, text],
        summary=checked.summary,
        timings={"total": time.perf_counter() - started},
        token_usage={},
        used_fallback_plan=False,
        fallback_reason=None,
    )
    return result, checked.verification, checked.warnings


def _metric_report_result(
    run: Run,
    catalog: Any,
    version: DatasetVersion,
    run_dir: Any,
    timeout_seconds: int,
) -> tuple[RunResult, str, list[str]]:
    started = time.perf_counter()
    request = run.request_json
    metric = Metric.model_validate(request["metric"])
    relationships = [Relationship.model_validate(item) for item in request.get("relationships") or []]
    try:
        checked = calculate_metric_report(
            catalog,
            metric,
            ReportPeriod.model_validate(request["period"]),
            SchemaInfo.model_validate(version.schema_json),
            relationships,
            group_by=request.get("group_by"),
            revision=request.get("metrics_revision"),
            timeout_seconds=timeout_seconds,
        )
    except MetricError as error:
        raise ReportValidationError(
            [CalculationCheck(code="metric_definition", passed=False, message=str(error))]
        ) from error
    csv_path = run_dir / f"{METRIC_ARTIFACT}.csv"
    pd.DataFrame(checked.rows, columns=checked.columns).to_csv(csv_path, index=False)
    info = {
        "name": metric.name,
        "label": metric.display,
        "unit": metric.unit,
        "description": checked.definition,
        "revision": request.get("metrics_revision"),
    }
    table = TableArtifact(
        name=METRIC_ARTIFACT,
        sql=checked.sql,
        columns=checked.columns,
        rows=checked.rows,
        total_rows=len(checked.rows),
        csv_path=str(csv_path),
        metric=info,
    )
    group = checked.columns[0]
    chart_rows = checked.rows[1:] if len(checked.rows) > 1 else checked.rows
    plot_step = PlotStep(
        name="metric_report_chart",
        kind="bar",
        data_source=METRIC_ARTIFACT,
        x=group,
        y="current",
        title=f"{metric.display}: current period" + (f" by {group}" if len(checked.rows) > 1 else ""),
    )
    frame = pd.DataFrame(
        [
            {group: str(row[group]), "current": row["current"]}
            for row in chart_rows
            if row["current"] is not None
        ]
    )
    artifacts: list[Any] = [table]
    if not frame.empty:
        figure = make_figure(plot_step, frame)
        png = figure_to_png(figure, run_dir / "metric_report_chart.png")
        artifacts.append(
            PlotArtifact(
                name=plot_step.name,
                kind=plot_step.kind,
                title=plot_step.title,
                figure=figure,
                png_path=str(png) if png else None,
            )
        )
    artifacts.append(TextArtifact(name="summary", text=checked.summary))
    run.provenance_json = {
        **run.provenance_json,
        "engine_version": checked.engine_version,
        "checks": [check.model_dump(mode="json") for check in checked.checks],
        "evidence": [item.model_dump(mode="json") for item in checked.evidence],
        "comparison": checked.comparison,
        "metric_definitions": {metric.name: checked.definition},
        "assumptions": [f"{METRIC_ARTIFACT}: approved metric {checked.definition}"],
    }
    result = RunResult(
        goal=run.goal,
        plan=Plan(
            steps=[
                SqlStep(name=METRIC_ARTIFACT, query=checked.sql, metric=info),
                plot_step,
                SummaryStep(name="summary"),
            ]
        ),
        artifacts=artifacts,
        summary=checked.summary,
        timings={"total": time.perf_counter() - started},
        token_usage={},
    )
    return result, checked.verification, checked.warnings


def execute_run(
    run_id: str,
    db_factory: Callable[[], Session] | None = None,
    bus: RunEventBus | None = None,
    llm: LLMClient | None = None,
    local_llm: LLMClient | None = None,
) -> None:
    configure()
    factory = db_factory or SessionLocal
    db = factory()
    catalog = None

    def publish(event: dict[str, Any]) -> None:
        if bus and event.get("type") != "done":
            bus.publish(run_id, event)

    try:
        run = db.get(Run, run_id)
        if not run:
            return
        run.status = "running"
        db.commit()
        chat = db.get(ChatSession, run.session_id)
        if chat is None:
            raise ValueError("The run session is unavailable")
        dataset = db.get(Dataset, chat.dataset_id)
        asker = db.get(User, run.owner_id)
        # The person asking needs access to the dataset (their own, or shared with their workspace).
        if dataset is None or asker is None or dataset_access(db, asker, dataset) is None:
            raise ValueError("The run dataset is unavailable")
        connection = db.get(Connection, dataset.connection_id) if dataset.connection_id else None
        uri = decrypt(connection.encrypted_uri) if connection else None
        version = db.get(DatasetVersion, run.dataset_version_id) if run.dataset_version_id else None
        if run.dataset_version_id and (
            version is None
            or version.dataset_id != dataset.id
            or version.owner_id != dataset.owner_id
            or version.state != "ready"
        ):
            raise ValueError("The pinned dataset version is unavailable or not confirmed")
        if not run.dataset_version_id and not connection:
            version = ensure_current_version(db, dataset)
            if version:
                run.dataset_version_id = version.id
        policy = (run.request_json or {}).get("privacy_mode", dataset.llm_policy)
        if not run.request_json:
            run.request_json = {"kind": "exploratory", "privacy_mode": policy}
        catalog = open_catalog(
            dataset,
            uri,
            for_run=True,
            version_id=version.id if version else None,
        )
        settings = get_settings()
        run_dir = Storage(settings.storage_dir).run_dir(run.owner_id, run.id)
        kind = run.request_json.get("kind")
        if kind in REPORT_KINDS:
            if version is None or version.state != "ready":
                raise ValueError("Verified reports require a ready immutable dataset version")
            specific = (
                {
                    "definition_id": run.definition_id,
                    "definition_version": run.request_json.get("definition_version"),
                    "definition": run.request_json.get("definition"),
                }
                if kind == "sales_margin_v1"
                else {
                    "metric": run.request_json.get("metric"),
                    "metrics_revision": run.request_json.get("metrics_revision"),
                    "group_by": run.request_json.get("group_by"),
                }
            )
            run.provenance_json = {
                "kind": kind,
                "source_version_id": version.id,
                **specific,
                "period": run.request_json.get("period"),
                "imported_at": _utc_iso(version.created_at),
                "source_freshness": "unknown",
                "sources": version.sources_json,
                "privacy_mode": "local",
            }
            rules = SavedRules.model_validate(dataset.rules_json or {})
            validation = (
                check_rules(
                    catalog,
                    rules.rules,
                    revision=rules.revision,
                    timeout=settings.query_timeout_seconds,
                )
                if rules.rules
                else None
            )
            if validation:
                run.provenance_json = {
                    **run.provenance_json,
                    "validation": validation.model_dump(mode="json"),
                }
            db.commit()
            if validation and validation.blocked:
                raise ReportValidationError(
                    [
                        CalculationCheck(
                            code="validation_rule",
                            passed=False,
                            message=f"Blocking rule not met: {item.description}. {item.message}",
                            affected_rows=item.failing_rows,
                        )
                        for item in validation.results
                        if item.severity == "blocking" and item.status != "passed"
                    ]
                )
            builder = _verified_result if kind == "sales_margin_v1" else _metric_report_result
            result, verification_status, warnings = builder(
                run,
                catalog,
                version,
                run_dir,
                settings.query_timeout_seconds,
            )
            if validation:
                run.provenance_json = {
                    **run.provenance_json,
                    "validation": validation.model_dump(mode="json"),
                }
                unmet = [item for item in validation.results if item.status != "passed"]
                warnings = [
                    *warnings,
                    *(f"Rule not met: {item.description}. {item.message}" for item in unmet),
                ]
                if unmet:
                    verification_status = "needs_review"
        else:
            previous = db.scalars(
                select(Run)
                .where(Run.session_id == run.session_id, Run.status == "completed", Run.id != run.id)
                .order_by(Run.created_at.desc())
                .limit(5)
            ).all()
            memory = ConversationMemory()
            for item in reversed(previous):
                earlier = item.provenance_json or {}
                if earlier.get("clarification") or earlier.get("refusal"):
                    continue
                memory.add(
                    item.goal,
                    item.summary or "",
                    dataset.tables_json,
                    key_numbers=earlier.get("key_numbers") or [],
                    assumptions=earlier.get("assumptions") or [],
                    queries=[
                        f"{step['name']}: {step['query']}"
                        for step in (item.plan_json or {}).get("steps", [])
                        if step.get("action") == "sql" and step.get("query")
                    ],
                )
            configured = llm or build_llm()
            local = local_llm if local_llm is not None else build_local_llm(settings)
            billed: tuple[str, str] | None = None
            if policy == "local":
                name = getattr(local, "model", settings.local_llm_model)
                model = f"local: {name}" if local else "offline (no AI model)"
                if local:
                    billed = ("local", str(name))
            elif llm_mode(configured) == "fake":
                model = "offline (no AI model)"
            else:
                model = f"{settings.llm_provider}: {resolved_model(settings)}"
                billed = (settings.llm_provider, resolved_model(settings))
            agent = InsightForgeAgent(
                configured,
                artifact_dir=run_dir,
                render_png=True,
                query_timeout=settings.query_timeout_seconds,
                summary_max_rows=settings.llm_summary_max_rows,
                schema_sample_rows=3 if settings.llm_send_sample_values else 0,
                privacy_mode=policy,
                local_llm=local,
                deep_max_rounds=settings.deep_max_rounds,
                deep_max_seconds=settings.deep_max_seconds,
                deep_max_tokens=settings.deep_max_tokens,
                sandbox=build_sandbox(settings),
            )
            saved_schema = (
                SchemaInfo.model_validate(version.schema_json)
                if version is not None and (version.schema_json or {}).get("tables")
                else None
            )
            request = run.request_json or {}
            mode = "deep" if request.get("mode") == "deep" else "quick"
            saved_metrics = SavedMetrics.model_validate(dataset.metrics_json or {})
            saved_queries = SavedQueries.model_validate(dataset.queries_json or {})
            result = agent.run(
                run.goal,
                catalog,
                memory,
                on_event=publish,
                schema=saved_schema,
                mode=mode,
                allow_clarification=bool(request.get("allow_clarification")),
                notes=DatasetNotes.model_validate(dataset.notes_json or {}),
                relationships=SavedRelationships.model_validate(
                    dataset.relationships_json or {}
                ).relationships,
                metrics=saved_metrics.metrics,
                metrics_revision=saved_metrics.revision,
                queries=saved_queries.queries,
                queries_revision=saved_queries.revision,
                approved_only=saved_queries.approved_only,
                value_index=connection is None,  # live databases are not scanned for values
            )
            check = result.number_check
            needs_review = (
                result.used_fallback_plan
                or any(artifact.type == "error" for artifact in result.artifacts)
                or bool(check and check.unmatched)
                or any(finding.code in SERIOUS_FINDINGS for finding in result.findings)
            )
            verification_status = "needs_review" if needs_review else "exploratory"
            warnings = [result.fallback_reason] if result.fallback_reason else []
            warnings += [f"Plan step skipped: {issue}" for issue in result.plan_issues]
            warnings += [finding.message for finding in result.findings]
            warnings += result.deep_notes
            warnings += [
                f"{artifact.name}: {truncation_note(artifact.total_rows, artifact.full_row_count)}"
                for artifact in result.artifacts
                if isinstance(artifact, TableArtifact) and artifact.truncated
            ]
            if check and check.unmatched:
                warnings.append(
                    f"{len(check.unmatched)} number(s) in the summary were not found in the results: "
                    f"{', '.join(check.unmatched[:8])}. Check them before relying on the summary."
                )
            run.provenance_json = {
                "kind": "exploratory",
                "source_version_id": version.id if version else None,
                "imported_at": _utc_iso(version.created_at) if version else None,
                "source_freshness": "unknown" if version else "live query (not a reproducible snapshot)",
                "sources": version.sources_json if version else dataset.sources_json,
                "privacy_mode": policy,
                "model": model,
                "engine_kind": "exploratory",
                "assumptions": result.assumptions,
                "evidence": [item.model_dump(mode="json") for item in result.evidence],
                "number_check": check.model_dump(mode="json") if check else None,
                "trace": [event.model_dump(mode="json") for event in result.trace],
                "mode": result.mode,
                "rounds": result.rounds,
                "reviews": result.reviews,
                "key_numbers": result.key_numbers,
                "clarification": (
                    result.clarification.model_dump(mode="json") if result.clarification else None
                ),
                "refusal": result.refusal,
                "approved_only": saved_queries.approved_only,
                "checks": (
                    [
                        {
                            "code": "summary_numbers",
                            "passed": not check.unmatched,
                            "message": check.message,
                        }
                    ]
                    if check and check.checked
                    else []
                )
                + [
                    {"code": finding.code, "passed": False, "message": finding.message}
                    for finding in result.findings
                ],
            }
            if billed:
                provider, model_name = billed
                prompt_tokens = int(result.token_usage.get("prompt_tokens", 0))
                completion_tokens = int(result.token_usage.get("completion_tokens", 0))
                price = None if provider == "local" else price_for(model_name, settings.llm_prices)
                cost = (
                    0.0
                    if provider == "local"
                    else estimate_cost(prompt_tokens, completion_tokens, price)
                )
                run.llm_provider, run.llm_model, run.cost_usd = provider, model_name, cost
                run.provenance_json = {
                    **run.provenance_json,
                    "cost": {
                        "provider": provider,
                        "model": model_name,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost_usd": cost,
                        "price": price.model_dump() if price else None,
                    },
                }
        run.status = "completed"
        run.plan_json = result.plan.model_dump(mode="json")
        run.summary = result.summary
        run.timings_json = result.timings
        run.token_usage_json = result.token_usage
        run.used_fallback_plan = result.used_fallback_plan
        run.fallback_reason = result.fallback_reason
        run.verification_status = verification_status
        run.warnings_json = warnings
        run.finished_at = datetime.now(UTC)
        _persist_artifacts(db, run, result.artifacts)
        db.commit()
        if bus:
            bus.publish(run_id, {"type": "done", "run_id": run.id})
    except ReportValidationError as error:
        db.rollback()
        run = db.get(Run, run_id)
        if run:
            checks = [check.model_dump(mode="json") for check in error.checks]
            provenance = dict(run.provenance_json or {})
            provenance["checks"] = checks
            run.provenance_json = provenance
            run.status = "failed"
            run.verification_status = "blocked"
            run.error = str(error)
            run.warnings_json = [check["message"] for check in checks if not check["passed"]]
            run.finished_at = datetime.now(UTC)
            db.commit()
        if bus:
            bus.publish(run_id, {"type": "error", "message": str(error)})
    except Exception as error:
        db.rollback()
        run = db.get(Run, run_id)
        if run:
            run.status = "failed"
            run.verification_status = (
                "blocked" if (run.request_json or {}).get("kind") in REPORT_KINDS else "needs_review"
            )
            run.error = str(error)
            run.warnings_json = [str(error)]
            run.finished_at = datetime.now(UTC)
            db.commit()
        if bus:
            bus.publish(run_id, {"type": "error", "message": str(error)})
    finally:
        if catalog:
            catalog.close()
        if bus:
            bus.finish(run_id)
        db.close()

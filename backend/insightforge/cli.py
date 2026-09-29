import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from insightforge.config import get_settings
from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import (
    CodeArtifact,
    ErrorArtifact,
    PlotArtifact,
    StatArtifact,
    TableArtifact,
    TextArtifact,
)
from insightforge.core.catalog import DataCatalog
from insightforge.core.executor import truncation_note
from insightforge.core.llm import build_llm, build_local_llm, llm_mode, offline_fake_llm, resolved_model
from insightforge.core.sandbox import build_sandbox
from insightforge.evals import DEFAULT_SUITE, CaseResult, compare, load_report, load_suite, run_suite
from insightforge.ingest import IngestError, load_any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="insightforge")
    commands = parser.add_subparsers(dest="command", required=True)

    ask = commands.add_parser("ask", help="Analyze a data source")
    ask.add_argument("source")
    ask.add_argument("goal")
    ask.add_argument("--name")
    ask.add_argument("--out", type=Path, default=Path("./out"))
    ask.add_argument("--png", action="store_true")
    ask.add_argument("--fake", action="store_true")
    ask.add_argument("--sheet", dest="sheets", nargs="+")
    ask.add_argument("--tables", nargs="+")
    ask.add_argument("--model")
    ask.add_argument("--base-url")

    schema = commands.add_parser("schema", help="Print a data source schema")
    schema.add_argument("source")
    schema.add_argument("--name")

    evaluate = commands.add_parser("eval", help="Score the analyst on reference questions")
    evaluate.add_argument("--model", choices=["offline", "local", "cloud"], default="offline")
    evaluate.add_argument("--mode", choices=["quick", "deep"], default="quick")
    evaluate.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    evaluate.add_argument("--case", dest="cases", action="append")
    evaluate.add_argument("--out", type=Path)
    evaluate.add_argument("--baseline", type=Path)
    evaluate.add_argument("--max-drop", type=float, default=0.05)
    return parser


def _options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if getattr(args, "sheets", None):
        options["sheets"] = args.sheets
    if getattr(args, "tables", None):
        options["tables"] = args.tables
    return options


def _pipe_rows(columns: list[str], rows: list[dict[str, Any]]) -> str:
    if not columns:
        return "(no columns)"

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(map(cell, columns)) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(cell(row.get(column, "")) for column in columns) + " |"
        for row in rows[:20]
    )
    return "\n".join(lines)


def _print_result(result: Any) -> None:
    print("\nPlan")
    print(json.dumps(result.plan.model_dump(mode="json"), indent=2))
    print("\nArtifacts")
    for artifact in result.artifacts:
        if isinstance(artifact, TableArtifact):
            print(f"\nTable: {artifact.name} ({artifact.total_rows} rows)")
            if artifact.truncated:
                print(truncation_note(artifact.total_rows, artifact.full_row_count))
            print(_pipe_rows(artifact.columns, artifact.rows))
            if artifact.csv_path:
                print(f"CSV: {artifact.csv_path}")
        elif isinstance(artifact, PlotArtifact):
            location = artifact.png_path or "PNG not rendered"
            print(f"\nPlot: {artifact.title or artifact.name} ({location})")
        elif isinstance(artifact, StatArtifact):
            print(f"\nTest: {artifact.name} ({artifact.test}, tested method)")
            print(artifact.interpretation)
            for line in [*artifact.checks, *artifact.cautions]:
                print(f"- {line}")
        elif isinstance(artifact, CodeArtifact):
            outcome = "ran" if artifact.ok else "failed"
            print(f"\nCode: {artifact.name} (free-form code, sandboxed; {outcome})")
            print(artifact.code)
            if artifact.error:
                print(artifact.error)
            if artifact.columns:
                print(_pipe_rows(artifact.columns, artifact.rows))
            elif artifact.value is not None:
                print(f"Result: {artifact.value}")
        elif isinstance(artifact, ErrorArtifact):
            print(f"\nError: {artifact.name}: {artifact.message}")
        elif isinstance(artifact, TextArtifact):
            continue
    print(f"\nSummary\n{result.summary}")
    if result.number_check and result.number_check.checked:
        print(f"\nNumber check: {result.number_check.message}")
        if result.number_check.unmatched:
            print(f"Not found in the results: {', '.join(result.number_check.unmatched)}")
    if result.assumptions:
        print("\nAssumptions")
        for item in result.assumptions:
            print(f"- {item}")
    print(f"\nTimings: {json.dumps(result.timings, sort_keys=True)}")
    print(f"Tokens: {json.dumps(result.token_usage, sort_keys=True)}")
    print(f"Used fallback plan: {result.used_fallback_plan}")
    if result.fallback_reason is not None:
        print(f"Fallback reason: {result.fallback_reason}")


def _ask(args: argparse.Namespace) -> int:
    catalog = DataCatalog()
    try:
        loaded = load_any(args.source, catalog, name=args.name, options=_options(args))
        print(f"Loaded tables: {', '.join(loaded.tables)}")
        for note in loaded.notes:
            print(f"Note: {note}")
        catalog.lock()
        if args.fake:
            llm = offline_fake_llm()
        else:
            settings = get_settings()
            updates = {}
            if args.model:
                updates["llm_model"] = args.model
            if args.base_url:
                updates["llm_base_url"] = args.base_url
            llm = build_llm(settings.model_copy(update=updates))
        result = InsightForgeAgent(llm, artifact_dir=args.out, render_png=args.png).run(
            args.goal, catalog
        )
        _print_result(result)
        return 0
    finally:
        catalog.close()


def _schema(args: argparse.Namespace) -> int:
    catalog = DataCatalog()
    try:
        loaded = load_any(args.source, catalog, name=args.name)
        print(f"Loaded tables: {', '.join(loaded.tables)}")
        print(catalog.introspect().to_prompt())
        return 0
    finally:
        catalog.close()


def _eval_agent_factory(model: str) -> Callable[[], InsightForgeAgent]:
    settings = get_settings()
    sandbox = build_sandbox(settings)
    if model == "offline":
        return lambda: InsightForgeAgent(offline_fake_llm(), privacy_mode="full")
    if model == "local":
        local = build_local_llm(settings)
        if local is None:
            raise SystemExit(
                "Set LOCAL_LLM_MODEL (and a localhost LOCAL_LLM_BASE_URL) to evaluate a local model"
            )
        return lambda: InsightForgeAgent(
            offline_fake_llm(), privacy_mode="local", local_llm=local, sandbox=sandbox
        )
    cloud = build_llm(settings)
    if llm_mode(cloud) == "fake":
        raise SystemExit("No cloud model is configured; set LLM_API_KEY or LLM_BASE_URL")
    print(
        f"Note: the evaluation datasets are synthetic; questions and results are sent to "
        f"{settings.llm_provider} ({resolved_model(settings)})."
    )
    return lambda: InsightForgeAgent(cloud, privacy_mode="full", sandbox=sandbox)


def _eval(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    make_agent = _eval_agent_factory(args.model)
    label = {
        "offline": "offline",
        "local": f"local: {get_settings().local_llm_model}",
        "cloud": f"cloud: {resolved_model(get_settings())}",
    }[args.model] + f" ({args.mode})"

    def show(result: CaseResult) -> None:
        status = "PASS" if result.passed else "FAIL"
        revised = f", {result.rounds} rounds" if result.rounds > 1 else ""
        print(f"{status} {result.id} ({result.seconds:.1f}s{revised}): {result.reason}", flush=True)

    report = run_suite(suite, make_agent, label, args.cases, show, args.mode)
    stamp = report.started_at.strftime("%Y%m%d-%H%M%S")
    out = args.out or suite.base_dir / "results" / f"{args.model}-{args.mode}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.to_json(), encoding="utf-8")
    print(f"\n{json.dumps(report.summary, indent=2)}\nReport: {out}")
    if not args.baseline:
        return 0
    comparison = compare(report, load_report(args.baseline))
    print(f"Accuracy change against the baseline: {comparison.accuracy_delta:+.1%}")
    if comparison.regressions:
        print(f"Now failing: {', '.join(comparison.regressions)}")
    if comparison.fixes:
        print(f"Now passing: {', '.join(comparison.fixes)}")
    return 1 if comparison.accuracy_delta < -args.max_drop else 0


def _run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "eval":
            return _eval(args)
        return _ask(args) if args.command == "ask" else _schema(args)
    except IngestError as error:
        print(f"Ingestion error: {error}", file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(_run())


if __name__ == "__main__":
    main()

import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import ErrorArtifact, RunResult, TableArtifact
from insightforge.core.catalog import DataCatalog, _quote

DEFAULT_SUITE = Path(__file__).resolve().parents[1] / "evals" / "suite.json"


class Expectation(BaseModel):
    type: Literal["value", "rows", "top", "clarify"]
    sql: str = "SELECT 1"
    keys: int = 1
    either_percent: bool = False


class EvalCase(BaseModel):
    id: str
    dataset: str
    question: str
    expect: Expectation


class EvalSuite(BaseModel):
    name: str
    version: int
    datasets: dict[str, dict[str, str]]
    cases: list[EvalCase]
    base_dir: Path = Field(default=Path("."), exclude=True)


class CaseResult(BaseModel):
    id: str
    dataset: str
    question: str
    passed: bool
    reason: str
    used_fallback_plan: bool = False
    plan_issues: int = 0
    errors: int = 0
    numbers_checked: int = 0
    numbers_matched: int = 0
    seconds: float = 0.0
    tokens: int = 0
    rounds: int = 1
    sql: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    suite: str
    suite_version: int
    model: str
    started_at: datetime
    cases: list[CaseResult]

    @property
    def summary(self) -> dict[str, Any]:
        total = len(self.cases) or 1
        checked = sum(case.numbers_checked for case in self.cases)
        return {
            "cases": len(self.cases),
            "passed": sum(case.passed for case in self.cases),
            "accuracy": round(sum(case.passed for case in self.cases) / total, 4),
            "fallback_rate": round(sum(case.used_fallback_plan for case in self.cases) / total, 4),
            "error_rate": round(sum(case.errors > 0 for case in self.cases) / total, 4),
            "summary_numbers_matched": (
                round(sum(case.numbers_matched for case in self.cases) / checked, 4) if checked else None
            ),
            "average_seconds": round(sum(case.seconds for case in self.cases) / total, 2),
            "revised_cases": sum(case.rounds > 1 for case in self.cases),
            "clarifying_questions": sum(case.reason.startswith("asked") for case in self.cases),
            "total_tokens": sum(case.tokens for case in self.cases),
        }

    def to_json(self) -> str:
        return json.dumps({**self.model_dump(mode="json"), "summary": self.summary}, indent=2)


class Comparison(BaseModel):
    accuracy_delta: float
    regressions: list[str]
    fixes: list[str]


def load_suite(path: Path = DEFAULT_SUITE) -> EvalSuite:
    suite = EvalSuite.model_validate_json(path.read_text(encoding="utf-8"))
    suite.base_dir = path.resolve().parent
    missing = {case.dataset for case in suite.cases} - set(suite.datasets)
    if missing:
        raise ValueError(f"Cases use unknown datasets: {', '.join(sorted(missing))}")
    return suite


def open_dataset(suite: EvalSuite, dataset: str) -> DataCatalog:
    catalog = DataCatalog()
    for table, relative in suite.datasets[dataset].items():
        path = (suite.base_dir / relative).resolve().as_posix().replace("'", "''")
        catalog.connection.execute(f"CREATE TABLE {_quote(table)} AS SELECT * FROM read_csv_auto('{path}')")
    catalog.lock()
    return catalog


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _close(actual: float, expected: float) -> bool:
    difference = abs(actual - expected)
    if difference <= max(0.011, 1e-6 * abs(expected)):
        return True
    return abs(expected) >= 100 and float(actual).is_integer() and difference <= 0.5


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip().casefold()


def _key_matches(cell: Any, key: Any) -> bool:
    cell_text, key_text = _text(cell), _text(key)
    return cell_text == key_text or cell_text.startswith(key_text + "-")


def _row_matches(row: dict[str, Any], keys: list[Any], values: list[float]) -> bool:
    cells = list(row.values())
    numbers = [float(cell) for cell in cells if _is_number(cell)]
    return all(any(_key_matches(cell, key) for cell in cells) for key in keys) and all(
        any(_close(number, value) for number in numbers) for value in values
    )


def _native(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def score(case: EvalCase, expected: pd.DataFrame, result: RunResult) -> tuple[bool, str]:
    if case.expect.type == "clarify":
        if result.clarification:
            return True, f"asked: {result.clarification.question}"
        return False, "answered without asking a clarifying question"
    if result.clarification:
        return False, f"asked a needless clarifying question: {result.clarification.question}"
    tables = [artifact for artifact in result.artifacts if isinstance(artifact, TableArtifact)]
    if not tables:
        return False, "no result tables"
    if expected.empty:
        return False, "the reference query returned no rows"
    expect = case.expect
    if expect.type == "value":
        target = float(_native(expected.iloc[0, 0]))
        targets = [target, target * 100] if expect.either_percent else [target]
        for table in tables:
            if any(
                _is_number(cell) and any(_close(float(cell), item) for item in targets)
                for row in table.rows
                for cell in row.values()
            ):
                return True, "value found in a result cell"
            if target.is_integer() and target in {table.total_rows, table.full_row_count}:
                return True, "value matches the number of result rows"
        return False, f"expected {target:g}, which is not in any result table"
    if expect.type == "top":
        key = _native(expected.iloc[0, 0])
        for table in tables:
            if table.rows and any(_key_matches(cell, key) for cell in table.rows[0].values()):
                return True, f"{key} is listed first"
        return False, f"no result table lists {key} first"
    rows = [
        (
            [_native(value) for value in record[: expect.keys]],
            [float(_native(value)) for value in record[expect.keys :]],
        )
        for record in expected.itertuples(index=False, name=None)
    ]
    best = 0
    for table in tables:
        found = sum(any(_row_matches(row, keys, values) for row in table.rows) for keys, values in rows)
        if found == len(rows):
            return True, f"all {len(rows)} expected rows found"
        best = max(best, found)
    return False, f"no result table contains all {len(rows)} expected rows (best: {best})"


def run_case(
    suite: EvalSuite, case: EvalCase, agent: InsightForgeAgent, mode: Literal["quick", "deep"] = "quick"
) -> CaseResult:
    catalog = open_dataset(suite, case.dataset)
    started = time.perf_counter()
    try:
        expected = catalog.query(case.expect.sql)
        try:
            result = agent.run(case.question, catalog, mode=mode, allow_clarification=True)
        except Exception as error:
            return CaseResult(
                id=case.id,
                dataset=case.dataset,
                question=case.question,
                passed=False,
                reason=f"run failed: {type(error).__name__}: {str(error)[:200]}",
                seconds=round(time.perf_counter() - started, 2),
            )
    finally:
        catalog.close()
    passed, reason = score(case, expected, result)
    check = result.number_check
    return CaseResult(
        id=case.id,
        dataset=case.dataset,
        question=case.question,
        passed=passed,
        reason=reason,
        used_fallback_plan=result.used_fallback_plan,
        plan_issues=len(result.plan_issues),
        errors=sum(isinstance(artifact, ErrorArtifact) for artifact in result.artifacts),
        numbers_checked=check.checked if check else 0,
        numbers_matched=check.matched if check else 0,
        seconds=round(time.perf_counter() - started, 2),
        tokens=sum(result.token_usage.values()),
        rounds=result.rounds,
        sql=[artifact.sql for artifact in result.artifacts if isinstance(artifact, TableArtifact)],
    )


def run_suite(
    suite: EvalSuite,
    make_agent: Callable[[], InsightForgeAgent],
    model: str,
    case_ids: list[str] | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
    mode: Literal["quick", "deep"] = "quick",
) -> EvalReport:
    cases = [case for case in suite.cases if not case_ids or case.id in case_ids]
    if case_ids and len(cases) != len(set(case_ids)):
        unknown = sorted(set(case_ids) - {case.id for case in cases})
        raise ValueError(f"Unknown case ids: {', '.join(unknown)}")
    report = EvalReport(
        suite=suite.name, suite_version=suite.version, model=model, started_at=datetime.now(UTC), cases=[]
    )
    for case in cases:
        result = run_case(suite, case, make_agent(), mode)
        report.cases.append(result)
        if on_result:
            on_result(result)
    return report


def compare(report: EvalReport, baseline: EvalReport) -> Comparison:
    before = {case.id: case.passed for case in baseline.cases}
    now = {case.id: case.passed for case in report.cases}
    shared = before.keys() & now.keys()
    count = len(shared) or 1
    return Comparison(
        accuracy_delta=round(
            (sum(now[case] for case in shared) - sum(before[case] for case in shared)) / count, 4
        ),
        regressions=sorted(case for case in shared if before[case] and not now[case]),
        fixes=sorted(case for case in shared if now[case] and not before[case]),
    )


def load_report(path: Path) -> EvalReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("summary", None)
    return EvalReport.model_validate(data)

import json

import pandas as pd

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import ErrorArtifact, PlotArtifact, TableArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.executor import Executor
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import Plan, Planner, PlotStep, SqlStep, SummaryStep
from insightforge.core.privacy import PromptPolicy
from insightforge.core.summarizer import Summarizer


def _events(rows: int = 120) -> DataCatalog:
    catalog = DataCatalog()
    catalog.register_df(
        "events", pd.DataFrame({"id": range(rows), "value": [float(index) for index in range(rows)]})
    )
    return catalog


def _run(catalog: DataCatalog, query: str, llm: FakeLLMClient):
    plan = Plan(
        steps=[
            SqlStep(name="raw", query=query),
            PlotStep(name="trend", kind="line", data_source="raw", x="id", y="value"),
            SummaryStep(name="summary"),
        ]
    )
    executor = Executor(catalog, Planner(llm), Summarizer(llm), result_limit=50)
    artifacts, _, _, _ = executor.execute("Trend", plan, catalog.introspect())
    table = next(artifact for artifact in artifacts if isinstance(artifact, TableArtifact))
    plot = next(artifact for artifact in artifacts if isinstance(artifact, PlotArtifact))
    return table, plot


def test_executor_reports_cut_off_results_with_their_full_size():
    catalog = _events()
    llm = FakeLLMClient(["Summary"])
    try:
        table, plot = _run(catalog, "SELECT * FROM events ORDER BY id", llm)
    finally:
        catalog.close()
    assert (table.total_rows, table.truncated, table.full_row_count) == (50, True, 120)
    assert plot.note == "Plotted all 120 rows."
    summary_prompt = llm.calls[-1][-1]["content"]
    assert "Rows retrieved: 50" in summary_prompt
    assert "The query produced 120 rows; only the first 50 were kept." in summary_prompt


def test_executor_trusts_an_explicit_limit():
    catalog = _events()
    try:
        table, plot = _run(catalog, "SELECT * FROM events ORDER BY id LIMIT 50", FakeLLMClient(["Summary"]))
    finally:
        catalog.close()
    assert (table.total_rows, table.truncated, table.full_row_count) == (50, False, None)
    assert plot.note is None


def test_executor_marks_unknown_size_when_the_count_fails(monkeypatch):
    catalog = _events()
    original = catalog.query

    def query(sql, timeout_seconds=None):
        if sql.startswith("SELECT COUNT(*) FROM ("):
            raise RuntimeError("count failed")
        return original(sql, timeout_seconds)

    monkeypatch.setattr(catalog, "query", query)
    try:
        table, _ = _run(catalog, "SELECT * FROM events ORDER BY id", FakeLLMClient(["Summary"]))
    finally:
        catalog.close()
    assert (table.truncated, table.full_row_count) == (True, None)


def test_executor_adds_a_chart_when_the_plan_has_none(catalog, schema):
    llm = FakeLLMClient(["Summary"])
    plan = Plan(
        steps=[
            SqlStep(
                name="by_department",
                query="SELECT department, AVG(salary) AS avg_salary FROM employees GROUP BY 1",
            ),
            SummaryStep(name="summary"),
        ]
    )
    artifacts, _, _, _ = Executor(catalog, Planner(llm), Summarizer(llm)).execute("Compare", plan, schema)
    assert [artifact.type for artifact in artifacts] == ["table", "plot", "text"]
    plot = artifacts[1]
    assert (plot.name, plot.kind, plot.title) == ("by_department_chart", "bar", "avg_salary by department")
    assert plot.note == "Chart added automatically because the plan had none."


def test_agent_traces_model_calls_queries_charts_and_checks(catalog):
    plan = {
        "steps": [
            {"name": "bad", "action": "sql", "query": "SELECT nope FROM employees"},
            {
                "name": "by_dept",
                "action": "sql",
                "query": "SELECT department, COUNT(*) AS n FROM employees GROUP BY 1",
            },
            {"name": "summary", "action": "summary"},
        ]
    }

    def reply(messages):
        content = messages[0]["content"]
        if "data-analysis planner" in content:
            return json.dumps(plan)
        if "Correct the DuckDB SQL" in content:
            return json.dumps({"query": "SELECT COUNT(*) AS n FROM employees"})
        return "There are 60 employees, 12 in each department, and 777 ghosts."

    result = InsightForgeAgent(FakeLLMClient(reply), privacy_mode="full").run("Headcount", catalog)
    assert [(event.kind, event.step, event.ok) for event in result.trace] == [
        ("model", "plan", True),
        ("model", "sql_repair:bad", True),
        ("sql", "bad", True),
        ("sql", "by_dept", True),
        ("chart", "by_dept_chart", True),
        ("model", "summary", True),
        ("check", "summary_numbers", False),
    ]
    assert result.trace[0].details["shared"] == "nothing (offline)"
    assert result.trace[2].details == {
        "rows": 1,
        "truncated": False,
        "full_row_count": None,
        "repaired": True,
    }
    assert result.trace[-1].details["unmatched"] == ["777"]


def test_trace_describes_what_each_privacy_mode_shares():
    assert PromptPolicy("schema_only").shared_with_model.startswith("table and column names")
    assert PromptPolicy("local", local_model=True).shared_with_model == (
        "everything, to a model on this computer"
    )


def test_executor_does_not_add_charts_to_plans_that_have_one(catalog, schema):
    llm = FakeLLMClient(["Summary"])
    plan = Plan(
        steps=[
            SqlStep(name="one", query="SELECT department, COUNT(*) AS n FROM employees GROUP BY 1"),
            SqlStep(name="two", query="SELECT department, AVG(salary) AS s FROM employees GROUP BY 1"),
            PlotStep(name="plot", kind="bar", data_source="one", x="department", y="n"),
            SummaryStep(name="summary"),
        ]
    )
    artifacts, _, _, _ = Executor(catalog, Planner(llm), Summarizer(llm)).execute("Compare", plan, schema)
    assert [artifact.name for artifact in artifacts if artifact.type == "plot"] == ["plot"]


def test_executor_does_not_flag_results_that_exactly_fill_the_limit():
    catalog = _events(50)
    try:
        table, _ = _run(catalog, "SELECT * FROM events ORDER BY id", FakeLLMClient(["Summary"]))
    finally:
        catalog.close()
    assert (table.total_rows, table.truncated) == (50, False)


def test_executor_repairs_bad_sql(catalog, schema):
    def respond(messages):
        if "Error:" in messages[-1]["content"]:
            return '{"query":"SELECT department, COUNT(*) AS count FROM employees GROUP BY department"}'
        return "Summary complete"

    llm = FakeLLMClient(respond)
    planner = Planner(llm)
    plan = Plan(
        steps=[
            SqlStep(name="counts", query="SELECT missing FROM employees"),
            SummaryStep(name="summary"),
        ]
    )
    artifacts, _, _, _ = Executor(catalog, planner, Summarizer(llm)).execute("Count", plan, schema)
    tables = [artifact for artifact in artifacts if isinstance(artifact, TableArtifact)]
    assert len(tables) == 1
    assert tables[0].total_rows == 5


def test_executor_reports_missing_plot_source(catalog, schema):
    llm = FakeLLMClient(["summary"])
    plan = Plan(
        steps=[
            SqlStep(name="one", query="SELECT 1 AS value"),
            PlotStep(name="plot", kind="bar", data_source="missing", x="value", y="value"),
            SummaryStep(name="summary"),
        ]
    )
    artifacts, _, _, _ = Executor(catalog, Planner(llm), Summarizer(llm)).execute("Plot", plan, schema)
    assert any(isinstance(artifact, ErrorArtifact) and artifact.name == "plot" for artifact in artifacts)


def test_guard_failure_repair_failure_does_not_stop_run(catalog, schema):
    def respond(messages):
        if "Error:" in messages[-1]["content"]:
            return '{"query":"SELECT * FROM read_csv_auto(\\"x.csv\\")"}'
        return "Summary complete"

    llm = FakeLLMClient(respond)
    plan = Plan(
        steps=[
            SqlStep(name="unsafe", query="SELECT * FROM read_csv_auto('x.csv')"),
            SqlStep(name="safe", query="SELECT COUNT(*) AS count FROM employees"),
            SummaryStep(name="summary"),
        ]
    )
    artifacts, _, _, _ = Executor(catalog, Planner(llm), Summarizer(llm)).execute("Run", plan, schema)
    assert any(isinstance(artifact, ErrorArtifact) and artifact.name == "unsafe" for artifact in artifacts)
    assert any(isinstance(artifact, TableArtifact) and artifact.name == "safe" for artifact in artifacts)

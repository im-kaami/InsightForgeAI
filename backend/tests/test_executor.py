import pandas as pd

from insightforge.core.artifacts import ErrorArtifact, PlotArtifact, TableArtifact
from insightforge.core.catalog import DataCatalog
from insightforge.core.executor import Executor
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import Plan, Planner, PlotStep, SqlStep, SummaryStep
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

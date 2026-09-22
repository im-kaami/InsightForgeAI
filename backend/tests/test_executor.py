from insightforge.core.artifacts import ErrorArtifact, TableArtifact
from insightforge.core.executor import Executor
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import Plan, Planner, PlotStep, SqlStep, SummaryStep
from insightforge.core.summarizer import Summarizer


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

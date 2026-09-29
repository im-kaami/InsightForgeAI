import json
import shutil

import pandas as pd
import pytest

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import CodeArtifact
from insightforge.core.llm import FakeLLMClient
from insightforge.core.planner import PYTHON_CUES, Planner
from insightforge.core.sandbox import DockerSandbox, SandboxResult, build_sandbox

CODE = "result = df.groupby('department')['salary'].median().reset_index(name='median_salary')"
CHOICE = {"python": True, "sql": "SELECT department, salary FROM employees", "code": CODE}


class FakeSandbox:
    limits = {"network": "none", "memory": "512m", "timeout_seconds": "30"}

    def __init__(self, result: SandboxResult):
        self.result = result
        self.calls: list[tuple[str, pd.DataFrame]] = []

    def run(self, code: str, frame: pd.DataFrame) -> SandboxResult:
        self.calls.append((code, frame))
        return self.result


def _reply(messages):
    if "explicitly asks for Python code" in messages[0]["content"]:
        return json.dumps(CHOICE)
    return "Engineering has the highest median."


def test_python_questions_run_code_in_the_sandbox_and_feed_results_forward(catalog, hr_df):
    table = hr_df.groupby("department")["salary"].median().reset_index(name="median_salary")
    sandbox = FakeSandbox(SandboxResult(True, frame=table, stdout="done\n", limits=FakeSandbox.limits))
    agent = InsightForgeAgent(FakeLLMClient(_reply), privacy_mode="full", sandbox=sandbox)
    result = agent.run("Using Python, compute the median salary per department", catalog)
    assert [step.action for step in result.plan.steps] == ["sql", "python", "summary"]
    code = next(item for item in result.artifacts if isinstance(item, CodeArtifact))
    assert code.ok and code.code == CODE and code.trust == "free-form code"
    assert code.columns == ["department", "median_salary"] and code.total_rows == 5
    assert code.limits["network"] == "none" and code.stdout == "done\n"
    assert len(sandbox.calls[0][1]) == len(hr_df)
    assert [event.kind for event in result.trace if event.step == "python_analysis"] == ["code"]


def test_python_is_not_offered_without_a_sandbox_and_failures_are_shown(catalog, schema):
    llm = FakeLLMClient(_reply)
    Planner(llm).plan("Using Python, compute the median salary", schema)
    assert all("explicitly asks for Python code" not in call[0]["content"] for call in llm.calls)

    failing = FakeSandbox(SandboxResult(False, error="NameError: name 'dff' is not defined"))
    result = InsightForgeAgent(FakeLLMClient(_reply), privacy_mode="full", sandbox=failing).run(
        "Using Python, compute the median salary per department", catalog
    )
    code = next(item for item in result.artifacts if isinstance(item, CodeArtifact))
    assert not code.ok and code.error.startswith("NameError")
    assert result.number_check is None or result.number_check.checked >= 0


def test_scalar_results_become_a_one_row_table(catalog):
    sandbox = FakeSandbox(SandboxResult(True, value=88016.67))
    result = InsightForgeAgent(FakeLLMClient(_reply), privacy_mode="full", sandbox=sandbox).run(
        "Using Python, compute the average salary", catalog
    )
    code = next(item for item in result.artifacts if isinstance(item, CodeArtifact))
    assert code.value == 88016.67 and code.columns == []


def test_python_cues_and_disabled_settings():
    assert PYTHON_CUES.search("Using Python, bootstrap the mean")
    assert PYTHON_CUES.search("Run a Monte Carlo simulation of revenue")
    assert not PYTHON_CUES.search("What is the average salary?")

    class Off:
        sandbox_enabled = False

    assert build_sandbox(Off()) is None
    assert DockerSandbox("x").run("x" * 9000, pd.DataFrame()).error.startswith("Code is longer than")


REAL = DockerSandbox("insightforge-sandbox:1", timeout=20, memory="256m")
needs_docker = pytest.mark.skipif(
    shutil.which("docker") is None or not REAL.available(), reason="sandbox image not built"
)


@needs_docker
def test_real_sandbox_computes_and_enforces_its_limits(hr_df):
    table = REAL.run(CODE, hr_df)
    assert table.ok and table.frame is not None and len(table.frame) == 5
    network = REAL.run("import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=3)", hr_df)
    assert not network.ok and "Network is unreachable" in network.error
    readonly = REAL.run("open('/work/code.py', 'w').write('x')", hr_df)
    assert not readonly.ok and "Read-only file system" in readonly.error
    user = REAL.run("import os\nresult = os.getuid()", hr_df)
    assert user.value == 65534
    memory = REAL.run("x = bytearray(600 * 1024 * 1024)", hr_df)
    assert not memory.ok and "too much memory" in memory.error

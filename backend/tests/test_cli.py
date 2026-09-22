import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).parents[1]
FIXTURES = BACKEND / "tests" / "fixtures"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "insightforge.cli", *map(str, args)],
        cwd=BACKEND,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_ask_csv_with_fake_llm(tmp_path):
    result = _run(
        "ask",
        FIXTURES / "hr.csv",
        "average salary by department",
        "--name",
        "employees",
        "--fake",
        "--out",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "employees" in result.stdout
    assert "Table:" in result.stdout
    assert "Summary" in result.stdout


def test_cli_schema_prints_table():
    result = _run("schema", FIXTURES / "hr.csv", "--name", "employees")
    assert result.returncode == 0, result.stderr
    assert "TABLE employees" in result.stdout


def test_cli_ask_workbook_lists_sheet_tables(tmp_path):
    result = _run(
        "ask",
        FIXTURES / "workbook.xlsx",
        "summarize workbook",
        "--fake",
        "--out",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "workbook__orders" in result.stdout
    assert "workbook__customers" in result.stdout

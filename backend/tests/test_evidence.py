import json

import pandas as pd
import pytest

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.evidence import check_numbers, describe_query, extract_numbers, sql_literals
from insightforge.core.llm import FakeLLMClient
from insightforge.core.schema import ColumnInfo, SchemaInfo, TableInfo

TABLE = pd.DataFrame(
    {
        "department": ["Engineering", "Finance", "Human Resources"],
        "avg_salary": [106333.333, 91416.667, 77166.667],
        "share": [0.2, 0.2, 0.2],
    }
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Average is 106,333.33 dollars", [("106,333.33", 106333.33)]),
        ("Revenue reached $1.2M", [("1.2M", 1_200_000.0)]),
        ("It fell -4.5% since", [("-4.5%", -4.5)]),
        ("About 3 million orders", [("3 million", 3_000_000.0)]),
        ("1. Engineering leads with 12 staff", [("12", 12.0)]),
        ("In 2024 on 2024-01-05, the top 5 grew 3rd", []),
    ],
)
def test_extract_numbers_reads_numbers_as_written(text, expected):
    assert [(claim.text, claim.value) for claim in extract_numbers(text)] == expected


def test_check_numbers_links_matches_to_cells_and_flags_the_rest():
    summary = (
        "Engineering pays 106,333 on average, Human Resources 77,166.67, and each is 20% of staff. "
        "There are 3 departments and 60 employees; the maximum salary is 12700:0."
    )
    check, evidence = check_numbers(summary, {"by_department": TABLE}, {"by_department": [3, 60]})
    assert check.checked == 5
    assert check.unmatched == ["12700"]
    assert [(item.text, item.row, item.column) for item in evidence] == [
        ("106,333", 1, "avg_salary"),
        ("77,166.67", 3, "avg_salary"),
        ("20%", 1, "share"),
        ("60", None, None),
    ]
    assert check.message == "4 of 5 numbers in the summary were found in the results"


def test_check_numbers_accepts_column_averages():
    check, evidence = check_numbers("The average salary is 91,638.89.", {"by_department": TABLE})
    assert check.unmatched == []
    assert (evidence[0].kind, evidence[0].column) == ("column_average", "avg_salary")


def test_check_numbers_ignores_numbers_from_the_question_and_sql():
    ignore = sql_literals("SELECT * FROM t WHERE amount > 500 LIMIT 15")
    check, _ = check_numbers("Orders above 500, top 15 shown", {"t": TABLE}, ignore=ignore)
    assert (check.checked, check.unmatched) == (0, [])


def test_describe_query_lists_tables_filters_groups_and_missing_values():
    schema = SchemaInfo(
        tables=[
            TableInfo(
                name="employees",
                row_count=60,
                columns=[
                    ColumnInfo(name="salary", dtype="DOUBLE", null_fraction=0.1),
                    ColumnInfo(name="department", dtype="VARCHAR"),
                ],
            )
        ]
    )
    text = describe_query(
        "top_departments",
        "SELECT department, AVG(salary) AS avg_salary FROM employees WHERE salary > 0 "
        "GROUP BY department ORDER BY avg_salary DESC LIMIT 3",
        schema,
    )
    assert text == (
        "top_departments: reads employees; keeps only rows where salary > 0; groups by department; "
        "calculates AVG(salary); keeps only the first 3 rows by avg_salary DESC; salary is missing "
        "in 10% of employees rows, and those rows are skipped by the calculation."
    )
    assert "first 10,000" not in describe_query("all", "SELECT * FROM employees LIMIT 10000")


def test_agent_checks_model_summaries_and_records_assumptions(catalog):
    plan = {
        "steps": [
            {
                "name": "by_department",
                "action": "sql",
                "query": "SELECT department, COUNT(*) AS people FROM employees GROUP BY 1 ORDER BY 1",
            },
            {"name": "summary", "action": "summary"},
        ]
    }
    llm = FakeLLMClient(
        [json.dumps(plan), "Each of the 5 departments has 12 people, 60 in total, and 99,999 unicorns."]
    )
    result = InsightForgeAgent(llm, privacy_mode="full").run("Headcount", catalog)
    assert result.number_check.unmatched == ["99,999"]
    assert [(item.text, item.kind, item.column) for item in result.evidence] == [
        ("12", "cell", "people"),
        ("60", "column_total", "people"),
    ]
    assert result.assumptions[0].startswith("by_department: reads employees; uses every row")


def test_agent_skips_the_number_check_for_summaries_written_without_a_model(catalog):
    result = InsightForgeAgent(FakeLLMClient(["unused"]), privacy_mode="local").run("Profile", catalog)
    assert result.number_check is None
    assert result.assumptions

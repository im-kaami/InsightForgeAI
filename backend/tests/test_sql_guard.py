import pytest

from insightforge.core.sql_guard import SQLGuardError, guard_sql


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM employees",
        "WITH people AS (SELECT * FROM employees) SELECT * FROM people",
        "SELECT 1 UNION SELECT 2",
        "SELECT * FROM employees LIMIT 5",
        "SELECT department, COUNT(*) FROM employees GROUP BY department ORDER BY department",
    ],
)
def test_guard_accepts_read_queries(query):
    assert guard_sql(query)


def test_guard_preserves_existing_limit():
    assert "LIMIT 5" in guard_sql("SELECT * FROM employees LIMIT 5")


def test_guard_injects_limit():
    assert "LIMIT 10000" in guard_sql("SELECT * FROM employees")


@pytest.mark.parametrize(
    "query",
    [
        "COPY (SELECT 1) TO 'x.csv'",
        "INSTALL httpfs",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT 1; SELECT 2",
        "DROP TABLE employees",
        "SELECT * FROM 'data.csv'",
        "PRAGMA database_list",
        "ATTACH 'x.db' AS y",
        "SELECT getenv('HOME')",
    ],
)
def test_guard_rejects_unsafe_queries(query):
    with pytest.raises(SQLGuardError):
        guard_sql(query)

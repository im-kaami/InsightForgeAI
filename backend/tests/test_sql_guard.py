import pytest

from insightforge.core.sql_guard import SQLGuardError, add_missing_group_by, guard_query, guard_sql


def test_a_forgotten_group_by_is_added_by_code():
    fixed = add_missing_group_by(
        "SELECT region, COUNT(*) AS n FROM orders WHERE status = 'cancelled' ORDER BY n DESC"
    )
    assert fixed == (
        "SELECT region, COUNT(*) AS n FROM orders WHERE status = 'cancelled' GROUP BY region ORDER BY n DESC"
    )
    assert add_missing_group_by("SELECT UPPER(region) AS r, SUM(amount) FROM orders").endswith(
        "GROUP BY UPPER(region)"
    )
    for unchanged in (
        "SELECT COUNT(*) FROM orders",
        "SELECT region FROM orders",
        "SELECT region, COUNT(*) FROM orders GROUP BY region",
        "SELECT *, COUNT(*) FROM orders",
        "DELETE FROM orders",
        "SELECT a, COUNT(*) FROM t UNION SELECT b, COUNT(*) FROM u",
    ):
        assert add_missing_group_by(unchanged) is None


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


def test_guard_query_keeps_the_unlimited_query_for_counting():
    guarded = guard_query("SELECT * FROM employees ORDER BY salary", default_limit=100)
    assert guarded.limit == 100
    assert guarded.sql.endswith("LIMIT 100")
    assert "LIMIT" not in guarded.full_sql
    assert guarded.count_sql == f"SELECT COUNT(*) FROM ({guarded.full_sql}) AS q"


def test_guard_query_does_not_count_explicitly_limited_queries():
    guarded = guard_query("SELECT * FROM employees LIMIT 5")
    assert (guarded.limit, guarded.full_sql, guarded.count_sql) == (None, None, None)


def test_guard_query_wraps_unions():
    guarded = guard_query("SELECT 1 AS v UNION ALL SELECT 2", default_limit=10)
    assert guarded.sql.endswith("LIMIT 10")
    assert "UNION ALL" in guarded.full_sql


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

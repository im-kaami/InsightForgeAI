import re
from dataclasses import dataclass

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class SQLGuardError(ValueError):
    pass


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    full_sql: str | None = None
    count_sql: str | None = None
    limit: int | None = None


_FILE_SUFFIX = re.compile(r"\.(csv|parquet|json|jsonl|xlsx|db|duckdb)$", re.IGNORECASE)
_FORBIDDEN_FUNCTIONS = {"glob", "getenv", "current_setting", "load", "install", "copy"}


def _unsafe_path(value: str) -> bool:
    return "/" in value or "\\" in value or bool(_FILE_SUFFIX.search(value))


def _validated(sql: str) -> exp.Expression:
    try:
        statements = [statement for statement in parse(sql, read="duckdb") if statement is not None]
    except ParseError as error:
        raise SQLGuardError(f"SQL parse failed: {error}") from error
    if len(statements) != 1:
        raise SQLGuardError("Exactly one SQL statement is allowed")
    expression = statements[0]
    if not isinstance(expression, (exp.Select, exp.Union)):
        raise SQLGuardError("Only SELECT and UNION queries are allowed")

    forbidden_names = (
        "Command",
        "Copy",
        "Insert",
        "Update",
        "Delete",
        "Create",
        "Drop",
        "Alter",
        "Pragma",
        "Set",
        "Attach",
        "Detach",
        "Transaction",
    )
    forbidden_types = tuple(
        node_type for name in forbidden_names if (node_type := getattr(exp, name, None)) is not None
    )
    for node in expression.walk():
        if forbidden_types and isinstance(node, forbidden_types):
            raise SQLGuardError(f"Forbidden SQL operation: {type(node).__name__}")
        if isinstance(node, exp.Func):
            name = node.name.lower() if isinstance(node, exp.Anonymous) else node.sql_name().lower()
            if (
                name.startswith("read_")
                or name.endswith("_scan")
                or name.endswith("_query")
                or name in _FORBIDDEN_FUNCTIONS
            ):
                raise SQLGuardError(f"Forbidden SQL function: {name}")
        if isinstance(node, exp.Table) and _unsafe_path(node.name):
            raise SQLGuardError(f"File-backed table references are forbidden: {node.name}")
        if isinstance(node, exp.Literal) and node.is_string and _unsafe_path(node.this):
            parent = node.parent
            if isinstance(parent, (exp.Table, exp.From, exp.Join)):
                raise SQLGuardError(f"File-backed table references are forbidden: {node.this}")
    return expression


def guard_query(sql: str, default_limit: int = 10000) -> GuardedQuery:
    expression = _validated(sql)
    if expression.args.get("limit") is not None:
        return GuardedQuery(sql=expression.sql(dialect="duckdb"))
    limit = int(default_limit)
    full_sql = expression.sql(dialect="duckdb")
    if isinstance(expression, exp.Union):
        limited = f"SELECT * FROM ({full_sql}) AS q LIMIT {limit}"
    else:
        limited = expression.limit(limit).sql(dialect="duckdb")
    return GuardedQuery(
        sql=limited,
        full_sql=full_sql,
        count_sql=f"SELECT COUNT(*) FROM ({full_sql}) AS q",
        limit=limit,
    )


def add_missing_group_by(sql: str) -> str | None:
    """Group an aggregate query by its plain columns when the GROUP BY was forgotten.

    ``SELECT region, COUNT(*) FROM orders`` becomes ``... GROUP BY region``. Returns None when the
    query is not a single SELECT that mixes aggregates with plain columns and has no GROUP BY.
    """
    try:
        expression = _validated(sql)
    except SQLGuardError:
        return None
    if not isinstance(expression, exp.Select) or expression.args.get("group"):
        return None
    plain, aggregated = [], False
    for projection in expression.expressions:
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        if inner.find(exp.AggFunc) is not None:
            aggregated = True
        elif isinstance(inner, exp.Star) or inner.find(exp.Window) is not None:
            return None
        elif not isinstance(inner, exp.Literal):
            plain.append(inner.copy())
    if not aggregated or not plain:
        return None
    return expression.group_by(*plain, copy=True).sql(dialect="duckdb")


def guard_sql(sql: str, default_limit: int = 10000) -> str:
    return guard_query(sql, default_limit).sql

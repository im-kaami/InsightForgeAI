import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class SQLGuardError(ValueError):
    pass


_FILE_SUFFIX = re.compile(r"\.(csv|parquet|json|jsonl|xlsx|db|duckdb)$", re.IGNORECASE)
_FORBIDDEN_FUNCTIONS = {"glob", "getenv", "current_setting", "load", "install", "copy"}


def _unsafe_path(value: str) -> bool:
    return "/" in value or "\\" in value or bool(_FILE_SUFFIX.search(value))


def guard_sql(sql: str, default_limit: int = 10000) -> str:
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

    if expression.args.get("limit") is None:
        if isinstance(expression, exp.Union):
            rendered = expression.sql(dialect="duckdb")
            return f"SELECT * FROM ({rendered}) AS q LIMIT {int(default_limit)}"
        expression = expression.limit(default_limit)
    return expression.sql(dialect="duckdb")

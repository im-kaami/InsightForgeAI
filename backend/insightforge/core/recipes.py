"""Deterministic cleaning recipes (Phase 3b).

A recipe is an ordered list of steps that tested code applies to a copy of a dataset version. The AI
never edits data: suggestions come from the health check, and the user saves the steps they accept.
"""

import math
import re
import threading
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlglot import exp, parse

from insightforge.core.catalog import DataCatalog, QueryTimeoutError, _qualified, _quote
from insightforge.core.profiling import DataProfile, column_kind
from insightforge.core.sensitivity import classify_column
from insightforge.core.sql_guard import SQLGuardError, guard_query

NEW_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
DATE_FORMAT = re.compile(r"^[%A-Za-z0-9 /:.,_\-]{1,40}$")
_DTYPE = re.compile(r"^[A-Z0-9_(), ]{1,60}$")
_TEMP_TABLE = "__insightforge_recipe_tmp"
TYPE_SQL = {
    "integer": "BIGINT",
    "number": "DOUBLE",
    "text": "VARCHAR",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "boolean": "BOOLEAN",
}
Scalar = str | int | float | bool


class RecipeError(ValueError):
    def __init__(self, message: str, index: int | None = None):
        self.index = index
        super().__init__(f"Step {index + 1}: {message}" if index is not None else message)


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(min_length=1, max_length=200)


def _new_name(value: str) -> str:
    if not NEW_NAME.match(value):
        raise ValueError("New names must start with a letter or _ and use only letters, digits and _")
    return value


class RenameColumn(_Step):
    kind: Literal["rename_column"] = "rename_column"
    column: str = Field(min_length=1, max_length=200)
    new_name: str

    _check_name = field_validator("new_name")(_new_name)


class ChangeType(_Step):
    kind: Literal["change_type"] = "change_type"
    column: str = Field(min_length=1, max_length=200)
    to: Literal["integer", "number", "text", "date", "timestamp", "boolean"]
    date_format: str | None = None
    on_error: Literal["fail", "empty"] = "fail"

    @field_validator("date_format")
    @classmethod
    def _format(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not DATE_FORMAT.match(value) or "%" not in value:
            raise ValueError("Date formats use strftime codes such as %d/%m/%Y")
        return value

    @model_validator(mode="after")
    def _format_needs_date(self) -> "ChangeType":
        if self.date_format and self.to not in {"date", "timestamp"}:
            raise ValueError("A date format only applies when changing to a date or timestamp")
        return self


class CleanText(_Step):
    kind: Literal["clean_text"] = "clean_text"
    column: str = Field(min_length=1, max_length=200)
    trim: bool = True
    collapse_spaces: bool = False
    case: Literal["keep", "lower", "upper"] = "keep"


class ValueMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_value: str = Field(max_length=500)
    to_value: str | None = Field(default=None, max_length=500)


class MapValues(_Step):
    kind: Literal["map_values"] = "map_values"
    column: str = Field(min_length=1, max_length=200)
    mapping: list[ValueMapping] = Field(min_length=1, max_length=200)
    ignore_case: bool = False

    @model_validator(mode="after")
    def _unique(self) -> "MapValues":
        keys = [item.from_value.casefold() if self.ignore_case else item.from_value for item in self.mapping]
        if len(set(keys)) != len(keys):
            raise ValueError("Each value can be mapped only once")
        return self


class FillMissing(_Step):
    kind: Literal["fill_missing"] = "fill_missing"
    column: str = Field(min_length=1, max_length=200)
    method: Literal["value", "mean", "median", "most_common"] = "value"
    value: Scalar | None = None

    @model_validator(mode="after")
    def _value(self) -> "FillMissing":
        if self.method == "value" and (self.value is None or self.value == ""):
            raise ValueError("Give the value that should replace missing values")
        if isinstance(self.value, str) and len(self.value) > 500:
            raise ValueError("Fill values are limited to 500 characters")
        return self


class DropMissing(_Step):
    kind: Literal["drop_missing"] = "drop_missing"
    columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=50
    )


class DropDuplicates(_Step):
    kind: Literal["drop_duplicates"] = "drop_duplicates"
    columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=50
    )


Operator = Literal[
    "equals",
    "not_equals",
    "greater_than",
    "less_than",
    "at_least",
    "at_most",
    "is_missing",
    "is_not_missing",
]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=200)
    op: Operator
    value: Scalar | None = None

    @model_validator(mode="after")
    def _value(self) -> "Condition":
        if self.op not in {"is_missing", "is_not_missing"} and (self.value is None or self.value == ""):
            raise ValueError(f"The '{self.op}' condition needs a value")
        if isinstance(self.value, str) and len(self.value) > 500:
            raise ValueError("Condition values are limited to 500 characters")
        return self


class FilterRows(_Step):
    kind: Literal["filter_rows"] = "filter_rows"
    action: Literal["keep", "remove"]
    conditions: list[Condition] = Field(min_length=1, max_length=10)


class DeriveColumn(_Step):
    kind: Literal["derive_column"] = "derive_column"
    new_name: str
    expression: str = Field(min_length=1, max_length=1000)

    _check_name = field_validator("new_name")(_new_name)


RecipeStep = Annotated[
    RenameColumn
    | ChangeType
    | CleanText
    | MapValues
    | FillMissing
    | DropMissing
    | DropDuplicates
    | FilterRows
    | DeriveColumn,
    Field(discriminator="kind"),
]


class CleaningRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[RecipeStep] = Field(default_factory=list, max_length=50)
    auto_apply: bool = True


class SavedRecipe(CleaningRecipe):
    model_config = ConfigDict(extra="ignore")
    revision: int = 0
    updated_at: datetime | None = None


class StepResult(BaseModel):
    index: int
    kind: str
    table: str
    description: str
    rows_before: int
    rows_after: int
    changed_values: int | None = None
    failed_values: int | None = None


class AppliedRecipe(BaseModel):
    revision: int = 0
    steps: list[RecipeStep] = Field(default_factory=list)
    results: list[StepResult] = Field(default_factory=list)
    error: str | None = None
    failed_step: int | None = None
    applied_at: datetime | None = None
    method: str = "deterministic DuckDB SQL; the original import is kept unchanged"


class RecipeSuggestion(BaseModel):
    step: RecipeStep
    reason: str


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float):
        text = repr(value)
    else:
        text = str(value)
    return "'" + text.replace("'", "''") + "'"


def _show(value: Any) -> str:
    text = str(value)
    return f"'{text[:40]}...'" if len(text) > 40 else f"'{text}'"


def describe_step(step: RecipeStep) -> str:
    where = f"{step.table}"
    match step:
        case RenameColumn():
            return f"Rename {where}.{step.column} to {step.new_name}"
        case ChangeType():
            fmt = f" using the format {step.date_format}" if step.date_format else ""
            fallback = "leave values that do not convert empty" if step.on_error == "empty" else (
                "stop if any value does not convert"
            )
            return f"Change {where}.{step.column} to {step.to}{fmt}; {fallback}"
        case CleanText():
            parts = []
            if step.trim:
                parts.append("trim spaces")
            if step.collapse_spaces:
                parts.append("collapse repeated spaces")
            if step.case != "keep":
                parts.append(f"make {step.case} case")
            return f"Clean text in {where}.{step.column}: {', '.join(parts) or 'no change'}"
        case MapValues():
            shown = ", ".join(
                f"{_show(item.from_value)} -> "
                + (_show(item.to_value) if item.to_value is not None else "missing")
                for item in step.mapping[:3]
            )
            more = f" and {len(step.mapping) - 3} more" if len(step.mapping) > 3 else ""
            case_note = " (ignoring case)" if step.ignore_case else ""
            return f"Replace values in {where}.{step.column}{case_note}: {shown}{more}"
        case FillMissing():
            method = step.method.replace("_", " ")
            how = _show(step.value) if step.method == "value" else f"the {method} value"
            return f"Fill missing {where}.{step.column} with {how}"
        case DropMissing():
            return f"Remove rows of {where} where {' or '.join(step.columns)} is missing"
        case DropDuplicates():
            key = f" with the same {', '.join(step.columns)}" if step.columns else " that repeat exactly"
            return f"Remove rows of {where}{key}, keeping the first"
        case FilterRows():
            text = " and ".join(
                f"{item.column} {item.op.replace('_', ' ')}"
                + (f" {_show(item.value)}" if item.value is not None else "")
                for item in step.conditions
            )
            return f"{step.action.capitalize()} rows of {where} where {text}"
        case DeriveColumn():
            return f"Add column {where}.{step.new_name} = {step.expression}"
    return f"{step.kind} on {where}"


def _execute(catalog: DataCatalog, sql: str, timeout: float) -> list[tuple[Any, ...]]:
    timer = threading.Timer(timeout, catalog.connection.interrupt)
    timer.start()
    try:
        return catalog.connection.execute(sql).fetchall()
    except duckdb.InterruptException as error:
        raise QueryTimeoutError(f"Cleaning step exceeded {timeout} seconds") from error
    finally:
        timer.cancel()


class _Table:
    def __init__(self, catalog: DataCatalog, name: str, index: int):
        tables = {table.casefold(): table for table in catalog.table_names()}
        if name.casefold() not in tables:
            raise RecipeError(f"Table {name} is not in this version", index)
        self.name = tables[name.casefold()]
        self.sql = _qualified(self.name)
        rows = catalog.connection.execute(f"DESCRIBE SELECT * FROM {self.sql}").fetchall()
        self.columns = [(str(row[0]), str(row[1])) for row in rows]
        self.index = index

    def column(self, name: str) -> tuple[str, str]:
        for column, dtype in self.columns:
            if column.casefold() == name.casefold():
                return column, dtype
        raise RecipeError(f"Column {name} is not in {self.name}", self.index)

    def require_new(self, name: str) -> None:
        if any(column.casefold() == name.casefold() for column, _ in self.columns):
            raise RecipeError(f"{self.name} already has a column named {name}", self.index)


def _dtype(dtype: str, index: int) -> str:
    if not _DTYPE.match(dtype.upper()):
        raise RecipeError(f"Unsupported column type {dtype}", index)
    return dtype.upper()


def _rebuild(
    catalog: DataCatalog,
    table: _Table,
    select: str,
    where: str = "",
    qualify: str = "",
    timeout: float = 60,
) -> None:
    temp = _quote(_TEMP_TABLE)
    _execute(catalog, f"DROP TABLE IF EXISTS {temp}", timeout)
    _execute(
        catalog,
        f"CREATE TABLE {temp} AS SELECT {select} FROM {table.sql}"
        f"{' WHERE ' + where if where else ''}{' QUALIFY ' + qualify if qualify else ''} ORDER BY rowid",
        timeout,
    )
    _execute(catalog, f"DROP TABLE {table.sql}", timeout)
    _execute(catalog, f"ALTER TABLE {temp} RENAME TO {_quote(table.name)}", timeout)


def _replace_column(table: _Table, column: str, expression: str) -> str:
    return ", ".join(
        f"{expression} AS {_quote(name)}" if name == column else _quote(name) for name, _ in table.columns
    )


def _count(catalog: DataCatalog, sql: str, timeout: float) -> int:
    return int(_execute(catalog, sql, timeout)[0][0])


def _derive_expression(expression: str, table: _Table, index: int) -> str:
    text = f"SELECT ({expression}) AS v FROM {table.sql}"
    try:
        guard_query(text)
    except SQLGuardError as error:
        raise RecipeError(f"The expression is not allowed: {error}", index) from error
    statements = [item for item in parse(text, read="duckdb") if item is not None]
    select = statements[0]
    if not isinstance(select, exp.Select) or len(select.expressions) != 1:
        raise RecipeError("Write a single expression, for example amount * quantity", index)
    if any(select.args.get(key) for key in ("where", "group", "having", "order", "limit", "joins")):
        raise RecipeError("Write a single expression, for example amount * quantity", index)
    node = select.expressions[0]
    value = node.this if isinstance(node, exp.Alias) else node
    for part in value.walk():
        if isinstance(part, (exp.Select, exp.Subquery, exp.Table)):
            raise RecipeError("Expressions cannot read other tables or use subqueries", index)
        if isinstance(part, (exp.AggFunc, exp.Window)):
            raise RecipeError(
                "Expressions work row by row; totals and window functions are not allowed", index
            )
    return value.sql(dialect="duckdb")


def _condition(table: _Table, condition: Condition, index: int) -> str:
    column, dtype = table.column(condition.column)
    quoted = _quote(column)
    if condition.op == "is_missing":
        return f"{quoted} IS NULL"
    if condition.op == "is_not_missing":
        return f"{quoted} IS NOT NULL"
    kind = column_kind(dtype)
    ordering = {"greater_than", "less_than", "at_least", "at_most"}
    if condition.op in ordering and kind not in {"number", "date"}:
        raise RecipeError(f"'{condition.op}' needs a number or date column; {column} is {dtype}", index)
    operator = {
        "equals": "=",
        "not_equals": "<>",
        "greater_than": ">",
        "less_than": "<",
        "at_least": ">=",
        "at_most": "<=",
    }[condition.op]
    return f"{quoted} {operator} CAST({_literal(condition.value)} AS {_dtype(dtype, index)})"


def _apply_step(catalog: DataCatalog, step: RecipeStep, index: int, timeout: float) -> StepResult:
    table = _Table(catalog, step.table, index)
    before = _count(catalog, f"SELECT COUNT(*) FROM {table.sql}", timeout)
    changed: int | None = None
    failed: int | None = None
    description = describe_step(step)
    match step:
        case RenameColumn():
            column, _ = table.column(step.column)
            table.require_new(step.new_name)
            _execute(
                catalog,
                f"ALTER TABLE {table.sql} RENAME COLUMN {_quote(column)} TO {_quote(step.new_name)}",
                timeout,
            )
        case ChangeType():
            column, dtype = table.column(step.column)
            quoted = _quote(column)
            text = column_kind(dtype) == "text"
            source = f"TRIM({quoted})" if text else quoted
            target = TYPE_SQL[step.to]
            if step.date_format:
                if not text:
                    raise RecipeError(f"A date format needs a text column; {column} is {dtype}", index)
                expression = f"CAST(try_strptime({source}, {_literal(step.date_format)}) AS {target})"
            else:
                expression = f"TRY_CAST({source} AS {target})"
            failed = _count(
                catalog,
                f"SELECT COUNT(*) FROM {table.sql} WHERE {quoted} IS NOT NULL "
                + (f"AND TRIM({quoted}) <> '' " if text else "")
                + f"AND {expression} IS NULL",
                timeout,
            )
            if failed and step.on_error == "fail":
                raise RecipeError(
                    f"{failed:,} values in {table.name}.{column} could not be converted to {step.to}; "
                    "fix them, choose another format, or allow empty values",
                    index,
                )
            _rebuild(catalog, table, _replace_column(table, column, expression), timeout=timeout)
        case CleanText():
            column, dtype = table.column(step.column)
            if column_kind(dtype) != "text":
                raise RecipeError(f"Text cleaning needs a text column; {column} is {dtype}", index)
            expression = _quote(column)
            if step.collapse_spaces:
                expression = f"regexp_replace({expression}, '\\s+', ' ', 'g')"
            if step.trim:
                expression = f"TRIM({expression})"
            if step.case != "keep":
                expression = f"{step.case.upper()}({expression})"
            changed = _count(
                catalog,
                f"SELECT COUNT(*) FROM {table.sql} WHERE {_quote(column)} IS DISTINCT FROM {expression}",
                timeout,
            )
            _rebuild(catalog, table, _replace_column(table, column, expression), timeout=timeout)
        case MapValues():
            column, dtype = table.column(step.column)
            if column_kind(dtype) != "text":
                raise RecipeError(f"Replacing values needs a text column; {column} is {dtype}", index)
            quoted = _quote(column)
            subject = f"LOWER({quoted})" if step.ignore_case else quoted
            branches = " ".join(
                f"WHEN {subject} = "
                + (f"LOWER({_literal(item.from_value)})" if step.ignore_case else _literal(item.from_value))
                + f" THEN {_literal(item.to_value) if item.to_value is not None else 'NULL'}"
                for item in step.mapping
            )
            expression = f"CAST(CASE {branches} ELSE {quoted} END AS VARCHAR)"
            changed = _count(
                catalog,
                f"SELECT COUNT(*) FROM {table.sql} WHERE {quoted} IS DISTINCT FROM {expression}",
                timeout,
            )
            _rebuild(catalog, table, _replace_column(table, column, expression), timeout=timeout)
        case FillMissing():
            column, dtype = table.column(step.column)
            quoted = _quote(column)
            kind = column_kind(dtype)
            if step.method in {"mean", "median"} and kind != "number":
                raise RecipeError(f"The {step.method} needs a number column; {column} is {dtype}", index)
            if step.method == "value":
                fill: Any = step.value
            elif step.method == "mean":
                fill = _execute(catalog, f"SELECT AVG({quoted}) FROM {table.sql}", timeout)[0][0]
            elif step.method == "median":
                median = f"SELECT quantile_cont({quoted}, 0.5) FROM {table.sql}"
                fill = _execute(catalog, median, timeout)[0][0]
            else:
                rows = _execute(
                    catalog,
                    f"SELECT {quoted} FROM {table.sql} WHERE {quoted} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1",
                    timeout,
                )
                fill = rows[0][0] if rows else None
            if fill is None:
                raise RecipeError(f"{table.name}.{column} has no values to compute a fill value from", index)
            if step.method in {"mean", "median"} and "INT" in dtype.upper():
                fill = math.floor(float(fill) + 0.5)
            if step.method != "value":
                hidden = "a value (hidden for a sensitive column)"
                shown = hidden if classify_column(column) else _show(fill)
                rounded = kind == "number" and "INT" in dtype.upper()
                note = " (rounded to fit the column type)" if rounded else ""
                description = f"{description}: {shown}{note}"
            expression = f"COALESCE({quoted}, CAST({_literal(fill)} AS {_dtype(dtype, index)}))"
            changed = _count(catalog, f"SELECT COUNT(*) FROM {table.sql} WHERE {quoted} IS NULL", timeout)
            try:
                _rebuild(catalog, table, _replace_column(table, column, expression), timeout=timeout)
            except duckdb.ConversionException as error:
                raise RecipeError(f"The fill value does not fit {column} ({dtype})", index) from error
        case DropMissing():
            columns = [table.column(name)[0] for name in step.columns]
            missing = " OR ".join(f"{_quote(name)} IS NULL" for name in columns)
            _rebuild(catalog, table, "*", where=f"NOT ({missing})", timeout=timeout)
        case DropDuplicates():
            columns = [table.column(name)[0] for name in step.columns] or [name for name, _ in table.columns]
            partition = ", ".join(_quote(name) for name in columns)
            _rebuild(
                catalog,
                table,
                "*",
                qualify=f"row_number() OVER (PARTITION BY {partition} ORDER BY rowid) = 1",
                timeout=timeout,
            )
        case FilterRows():
            joined = " AND ".join(f"({_condition(table, item, index)})" for item in step.conditions)
            where = f"COALESCE({joined}, FALSE)"
            try:
                _rebuild(
                    catalog,
                    table,
                    "*",
                    where=where if step.action == "keep" else f"NOT {where}",
                    timeout=timeout,
                )
            except duckdb.ConversionException as error:
                raise RecipeError("A condition value does not fit its column type", index) from error
        case DeriveColumn():
            table.require_new(step.new_name)
            expression = _derive_expression(step.expression, table, index)
            try:
                _rebuild(catalog, table, f"*, ({expression}) AS {_quote(step.new_name)}", timeout=timeout)
            except duckdb.Error as error:
                message = str(error).splitlines()[0][:200]
                raise RecipeError(f"The expression could not be calculated: {message}", index) from error
    after = _count(catalog, f"SELECT COUNT(*) FROM {_qualified(table.name)}", timeout)
    return StepResult(
        index=index,
        kind=step.kind,
        table=table.name,
        description=description,
        rows_before=before,
        rows_after=after,
        changed_values=changed,
        failed_values=failed,
    )


def apply_recipe(catalog: DataCatalog, steps: list[RecipeStep], *, timeout: float = 60) -> list[StepResult]:
    """Apply steps in order to a writable catalog. Raises RecipeError naming the failing step."""
    results: list[StepResult] = []
    for index, step in enumerate(steps):
        try:
            results.append(_apply_step(catalog, step, index, timeout))
        except RecipeError:
            raise
        except QueryTimeoutError as error:
            raise RecipeError(str(error), index) from error
        except duckdb.Error as error:
            message = str(error).splitlines()[0][:200]
            raise RecipeError(f"The step could not be applied: {message}", index) from error
    return results


def applied(revision: int, steps: list[RecipeStep], results: list[StepResult]) -> AppliedRecipe:
    return AppliedRecipe(revision=revision, steps=steps, results=results, applied_at=datetime.now(UTC))


def suggest_steps(profile: DataProfile, limit: int = 20) -> list[RecipeSuggestion]:
    """Suggest cleaning steps from the health check. Code decides; nothing is sent to a model."""
    suggestions: list[RecipeSuggestion] = []
    for table in profile.tables:
        if table.duplicate_rows:
            suggestions.append(
                RecipeSuggestion(
                    step=DropDuplicates(table=table.name),
                    reason=f"{table.duplicate_rows:,} rows repeat exactly",
                )
            )
        for column in table.columns:
            if column.kind != "text":
                continue
            if "Values look like numbers but are stored as text" in column.alerts:
                suggestions.append(
                    RecipeSuggestion(
                        step=ChangeType(table=table.name, column=column.name, to="number"),
                        reason="Values look like numbers but are stored as text",
                    )
                )
            elif "Values look like dates but are stored as text" in column.alerts:
                suggestions.append(
                    RecipeSuggestion(
                        step=ChangeType(table=table.name, column=column.name, to="date"),
                        reason="Values look like dates but are stored as text",
                    )
                )
            groups: dict[str, list[tuple[str, int]]] = {}
            for item in column.top_values:
                key = " ".join(item.value.split()).casefold()
                groups.setdefault(key, []).append((item.value, item.count))
            mapping = []
            for variants in groups.values():
                if len(variants) < 2:
                    continue
                keep = max(variants, key=lambda pair: (pair[1], pair[0]))[0]
                mapping += [
                    ValueMapping(from_value=value, to_value=keep) for value, _ in variants if value != keep
                ]
            if mapping:
                suggestions.append(
                    RecipeSuggestion(
                        step=MapValues(table=table.name, column=column.name, mapping=mapping),
                        reason="Some common values differ only in capital letters or spaces",
                    )
                )
    return suggestions[:limit]

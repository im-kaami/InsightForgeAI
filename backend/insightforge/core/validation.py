"""Validation rules checked by tested code on every dataset version (Phase 3b)."""

import re
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from insightforge.core.catalog import DataCatalog, QueryTimeoutError, _qualified, _quote
from insightforge.core.profiling import DataProfile, column_kind
from insightforge.core.schema import is_identifier
from insightforge.core.sensitivity import classify_column

MAX_EXAMPLES = 5


def _rule_id() -> str:
    return uuid.uuid4().hex[:12]


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=_rule_id, min_length=1, max_length=40)
    table: str = Field(min_length=1, max_length=200)
    severity: Literal["blocking", "warning"] = "warning"


class _ColumnRule(_Rule):
    column: str = Field(min_length=1, max_length=200)


class NotNullRule(_ColumnRule):
    kind: Literal["not_null"] = "not_null"


class UniqueRule(_ColumnRule):
    kind: Literal["unique"] = "unique"


class RangeRule(_ColumnRule):
    kind: Literal["range"] = "range"
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounds(self) -> "RangeRule":
        if self.min is None and self.max is None:
            raise ValueError("Give a lowest value, a highest value or both")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("The lowest value must not be above the highest value")
        return self


class AllowedValuesRule(_ColumnRule):
    kind: Literal["allowed_values"] = "allowed_values"
    values: list[Annotated[str, Field(max_length=200)]] = Field(min_length=1, max_length=200)
    ignore_case: bool = False


class PatternRule(_ColumnRule):
    kind: Literal["pattern"] = "pattern"
    pattern: str = Field(min_length=1, max_length=200)

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"The pattern is not a valid regular expression: {error}") from error
        return value


class FreshnessRule(_ColumnRule):
    kind: Literal["freshness"] = "freshness"
    max_age_days: int = Field(ge=0, le=3650)


class RowCountRule(_Rule):
    kind: Literal["row_count"] = "row_count"
    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _bounds(self) -> "RowCountRule":
        if self.min is None and self.max is None:
            raise ValueError("Give a lowest row count, a highest row count or both")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("The lowest row count must not be above the highest")
        return self


ValidationRule = Annotated[
    NotNullRule | UniqueRule | RangeRule | AllowedValuesRule | PatternRule | FreshnessRule | RowCountRule,
    Field(discriminator="kind"),
]


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[ValidationRule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _unique_ids(self) -> "RuleSet":
        ids = [rule.id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("Rule ids must be unique")
        return self


class SavedRules(RuleSet):
    model_config = ConfigDict(extra="ignore")
    revision: int = 0
    updated_at: datetime | None = None


class RuleResult(BaseModel):
    rule_id: str
    kind: str
    table: str
    column: str | None = None
    severity: Literal["blocking", "warning"]
    status: Literal["passed", "failed", "error"]
    description: str
    message: str
    checked_rows: int = 0
    failing_rows: int = 0
    examples: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    rules_revision: int = 0
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    results: list[RuleResult] = Field(default_factory=list)
    passed: int = 0
    failed_blocking: int = 0
    failed_warning: int = 0
    method: str = "exact counts over every row; no values are sent to an AI model"

    @property
    def blocked(self) -> bool:
        return self.failed_blocking > 0


class RuleSuggestion(BaseModel):
    rule: ValidationRule
    reason: str


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def describe_rule(rule: ValidationRule) -> str:
    where = f"{rule.table}.{rule.column}" if isinstance(rule, _ColumnRule) else rule.table
    match rule:
        case NotNullRule():
            return f"{where} is never missing"
        case UniqueRule():
            return f"{where} has no repeated values"
        case RangeRule():
            if rule.min is not None and rule.max is not None:
                return f"{where} is between {_number(rule.min)} and {_number(rule.max)}"
            if rule.min is not None:
                return f"{where} is at least {_number(rule.min)}"
            return f"{where} is at most {_number(rule.max)}"
        case AllowedValuesRule():
            more = f" and {len(rule.values) - 6} more" if len(rule.values) > 6 else ""
            shown = ", ".join(rule.values[:6]) + more
            return f"{where} is one of: {shown}" + (" (ignoring case)" if rule.ignore_case else "")
        case PatternRule():
            return f"{where} matches the pattern {rule.pattern}"
        case FreshnessRule():
            return f"The latest date in {where} is at most {rule.max_age_days} days old"
        case RowCountRule():
            if rule.min is not None and rule.max is not None:
                return f"{where} has between {rule.min:,} and {rule.max:,} rows"
            if rule.min is not None:
                return f"{where} has at least {rule.min:,} rows"
            return f"{where} has at most {rule.max:,} rows"
    return f"{rule.kind} on {where}"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class _RuleCheckError(ValueError):
    pass


def _resolve(catalog: DataCatalog, rule: ValidationRule) -> tuple[str, str | None, str | None]:
    tables = {name.casefold(): name for name in catalog.table_names()}
    table = tables.get(rule.table.casefold())
    if table is None:
        raise _RuleCheckError(f"Table {rule.table} is not in this version")
    if not isinstance(rule, _ColumnRule):
        return table, None, None
    rows = catalog.connection.execute(f"DESCRIBE SELECT * FROM {_qualified(table)}").fetchall()
    for name, dtype, *_ in rows:
        if str(name).casefold() == rule.column.casefold():
            return table, str(name), str(dtype)
    raise _RuleCheckError(f"Column {rule.column} is not in {table}")


def _check(
    catalog: DataCatalog, rule: ValidationRule, today: date, timeout: float
) -> tuple[int, int, list[str], str]:
    table, column, dtype = _resolve(catalog, rule)
    source = _qualified(table)
    total = int(catalog.query(f"SELECT COUNT(*) AS n FROM {source}", timeout_seconds=timeout).iloc[0, 0])
    if isinstance(rule, RowCountRule):
        low = rule.min is not None and total < rule.min
        high = rule.max is not None and total > rule.max
        failing = int(low or high)
        return total, failing, [], f"{table} has {total:,} rows"
    quoted = _quote(column)
    kind = column_kind(dtype)
    text = f"CAST({quoted} AS VARCHAR)"
    condition: str
    match rule:
        case NotNullRule():
            condition = f"{quoted} IS NULL"
        case UniqueRule():
            condition = (
                f"{quoted} IS NOT NULL AND {quoted} IN (SELECT {quoted} FROM {source} "
                f"WHERE {quoted} IS NOT NULL GROUP BY 1 HAVING COUNT(*) > 1)"
            )
        case RangeRule():
            if kind != "number":
                raise _RuleCheckError(f"A range rule needs a number column; {column} is {dtype}")
            value = f"CAST({quoted} AS DOUBLE)"
            parts = []
            if rule.min is not None:
                parts.append(f"{value} < {float(rule.min)!r}")
            if rule.max is not None:
                parts.append(f"{value} > {float(rule.max)!r}")
            condition = f"{quoted} IS NOT NULL AND ({' OR '.join(parts)})"
        case AllowedValuesRule():
            if rule.ignore_case:
                allowed = ", ".join(_literal(item.casefold()) for item in rule.values)
                condition = f"{quoted} IS NOT NULL AND LOWER({text}) NOT IN ({allowed})"
            else:
                allowed = ", ".join(_literal(item) for item in rule.values)
                condition = f"{quoted} IS NOT NULL AND {text} NOT IN ({allowed})"
        case PatternRule():
            condition = f"{quoted} IS NOT NULL AND NOT regexp_full_match({text}, {_literal(rule.pattern)})"
        case FreshnessRule():
            if kind != "date":
                raise _RuleCheckError(f"A freshness rule needs a date column; {column} is {dtype}")
            latest = catalog.query(
                f"SELECT CAST(MAX(CAST({quoted} AS DATE)) AS VARCHAR) AS latest FROM {source}",
                timeout_seconds=timeout,
            ).iloc[0, 0]
            if latest is None or str(latest) in {"None", "NaT", "nan"}:
                return total, total, [], f"{table}.{column} has no dates"
            latest_day = date.fromisoformat(str(latest)[:10])
            age = (today - latest_day).days
            failing = int(age > rule.max_age_days)
            return (
                total,
                failing,
                [],
                f"Latest date is {latest_day.isoformat()}, {age:,} days before this check. This looks at the "
                "dates in the data, not at when the source was last updated",
            )
        case _:
            raise _RuleCheckError(f"Unsupported rule {rule.kind}")
    counted = catalog.query(
        f"SELECT COUNT(*) AS n FROM {source} WHERE {condition}", timeout_seconds=timeout
    )
    failing = int(counted.iloc[0, 0])
    examples: list[str] = []
    if failing and not isinstance(rule, NotNullRule) and not classify_column(column):
        frame = catalog.query(
            f"SELECT DISTINCT {text} AS v FROM {source} WHERE {condition} ORDER BY 1 LIMIT {MAX_EXAMPLES}",
            timeout_seconds=timeout,
        )
        examples = [str(value)[:80] for value in frame["v"]]
    verb = "breaks" if failing == 1 else "break"
    return total, failing, examples, f"{failing:,} of {total:,} rows {verb} this rule"


def check_rules(
    catalog: DataCatalog,
    rules: list[ValidationRule],
    *,
    revision: int = 0,
    today: date | None = None,
    timeout: float = 30,
) -> ValidationReport:
    """Check every rule on every row of the catalog. Failures never raise; they become results."""
    today = today or datetime.now(UTC).date()
    report = ValidationReport(rules_revision=revision)
    for rule in rules:
        column = rule.column if isinstance(rule, _ColumnRule) else None
        try:
            checked, failing, examples, message = _check(catalog, rule, today, timeout)
            status: Literal["passed", "failed", "error"] = "failed" if failing else "passed"
            if not failing and not isinstance(rule, (RowCountRule, FreshnessRule)):
                message = f"All {checked:,} rows pass"
        except _RuleCheckError as error:
            checked, failing, examples, message, status = 0, 0, [], str(error), "error"
        except (duckdb.Error, QueryTimeoutError) as error:
            detail = str(error).splitlines()[0][:200]
            message = f"The rule could not be checked: {detail}"
            checked, failing, examples, status = 0, 0, [], "error"
        report.results.append(
            RuleResult(
                rule_id=rule.id,
                kind=rule.kind,
                table=rule.table,
                column=column,
                severity=rule.severity,
                status=status,
                description=describe_rule(rule),
                message=message,
                checked_rows=checked,
                failing_rows=failing,
                examples=examples,
            )
        )
    for result in report.results:
        if result.status == "passed":
            report.passed += 1
        elif result.severity == "blocking":
            report.failed_blocking += 1
        else:
            report.failed_warning += 1
    return report


def suggest_rules(profile: DataProfile, limit: int = 20) -> list[RuleSuggestion]:
    """Suggest rules that the current data already meets, from the health check only."""
    suggestions: list[RuleSuggestion] = []
    for table in profile.tables:
        if table.row_count:
            suggestions.append(
                RuleSuggestion(
                    rule=RowCountRule(table=table.name, min=1),
                    reason="Catches an empty file or export",
                )
            )
        for column in table.columns:
            present = (table.row_count or 0) - (column.null_count or 0)
            if not present:
                continue
            if is_identifier(column.name) and column.null_count == 0:
                suggestions.append(
                    RuleSuggestion(
                        rule=NotNullRule(table=table.name, column=column.name),
                        reason="This identifier has a value in every row",
                    )
                )
                if column.distinct_count == present:
                    suggestions.append(
                        RuleSuggestion(
                            rule=UniqueRule(table=table.name, column=column.name),
                            reason="Every value of this identifier is different",
                        )
                    )
                continue
            if column.kind == "number" and column.min_value is not None:
                try:
                    lowest = float(column.min_value)
                except ValueError:
                    continue
                if lowest >= 0:
                    suggestions.append(
                        RuleSuggestion(
                            rule=RangeRule(table=table.name, column=column.name, min=0),
                            reason="No value is negative today",
                        )
                    )
            if (
                column.kind == "text"
                and not column.sensitivity
                and column.distinct_count
                and column.distinct_count <= 10
                and len(column.top_values) == column.distinct_count
                and sum(item.count for item in column.top_values) == present
                and present >= 10
            ):
                suggestions.append(
                    RuleSuggestion(
                        rule=AllowedValuesRule(
                            table=table.name,
                            column=column.name,
                            values=sorted(item.value for item in column.top_values),
                        ),
                        reason=f"Only {column.distinct_count} different values appear today",
                    )
                )
    return suggestions[:limit]

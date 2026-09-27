from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from insightforge.core.catalog import DataCatalog, _qualified, _quote
from insightforge.core.schema import ColumnInfo, SchemaInfo, TableInfo


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColumnReference(StrictModel):
    table: str = Field(min_length=1, max_length=200)
    column: str = Field(min_length=1, max_length=200)


class ApprovedJoin(StrictModel):
    table: str = Field(min_length=1, max_length=200)
    fact_key: str = Field(min_length=1, max_length=200)
    lookup_key: str = Field(min_length=1, max_length=200)
    cardinality: Literal["one_to_one"] = "one_to_one"


class ReportFilter(StrictModel):
    column: str = Field(min_length=1, max_length=200)
    operator: Literal["equals", "not_equals"] = "equals"
    value: str = Field(max_length=200)


class SalesDefinition(StrictModel):
    kind: Literal["sales_margin_v1"] = "sales_margin_v1"
    fact_table: str = Field(min_length=1, max_length=200)
    row_key: str = Field(min_length=1, max_length=200)
    order_id_column: str | None = None
    date_column: str = Field(min_length=1, max_length=200)
    date_format: Literal["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"] = "%Y-%m-%d"
    revenue_column: str = Field(min_length=1, max_length=200)
    refunds: ColumnReference | None = None
    refunds_confirmed_absent: bool = False
    cost: ColumnReference | None = None
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    currency_column: str | None = None
    single_currency_confirmed: bool = False
    display_decimals: int = Field(default=2, ge=0, le=6)
    joins: list[ApprovedJoin] = Field(default_factory=list, max_length=2)
    filters: list[ReportFilter] = Field(default_factory=list, max_length=5)
    business_notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def explicit_assumptions(self) -> "SalesDefinition":
        if self.refunds is None and not self.refunds_confirmed_absent:
            raise ValueError("Map a refund amount or explicitly confirm that refunds are absent")
        if self.currency_column is None and not self.single_currency_confirmed:
            raise ValueError("Map a currency column or explicitly confirm a single currency")
        joined = [join.table for join in self.joins]
        if len(set(joined)) != len(joined) or self.fact_table in joined:
            raise ValueError("Join tables must be distinct; self joins are not supported")
        return self


class ReportPeriod(StrictModel):
    start_date: date = Field(ge=date(1900, 1, 1), le=date(2100, 12, 31))
    end_date: date = Field(ge=date(1900, 1, 1), le=date(2100, 12, 31))

    @model_validator(mode="after")
    def valid_window(self) -> "ReportPeriod":
        if not 0 <= (self.end_date - self.start_date).days <= 365:
            raise ValueError("Choose an ordered reporting period of at most 366 days")
        return self

    @property
    def previous_start(self) -> date:
        return self.start_date - timedelta(days=(self.end_date - self.start_date).days + 1)

    @property
    def previous_end(self) -> date:
        return self.start_date - timedelta(days=1)


class CalculationCheck(BaseModel):
    code: str
    passed: bool
    message: str
    affected_rows: int = 0


class EvidenceValue(BaseModel):
    id: str
    artifact: str = "sales_metrics"
    row: int
    column: str
    value: str | int | None


class VerifiedSalesResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    sql: str
    summary: str
    checks: list[CalculationCheck]
    warnings: list[str]
    evidence: list[EvidenceValue]
    comparison: dict[str, str | None]
    metric_definitions: dict[str, str]
    verification: Literal["checks_passed", "needs_review"]
    engine_version: str = "sales_margin_v1"


class ReportValidationError(ValueError):
    def __init__(self, checks: list[CalculationCheck]):
        self.checks = checks
        super().__init__("; ".join(check.message for check in checks if not check.passed))


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def validate_definition(definition: SalesDefinition, schema: SchemaInfo) -> None:
    columns = {table.name: {column.name for column in table.columns} for table in schema.tables}
    references = [
        ColumnReference(table=definition.fact_table, column=column)
        for column in (
            definition.row_key,
            definition.order_id_column,
            definition.date_column,
            definition.revenue_column,
            definition.currency_column,
        )
        if column is not None
    ]
    references += [reference for reference in (definition.refunds, definition.cost) if reference is not None]
    references += [
        ColumnReference(table=definition.fact_table, column=item.column) for item in definition.filters
    ]
    allowed = {definition.fact_table, *(join.table for join in definition.joins)}
    for join in definition.joins:
        references.extend(
            [
                ColumnReference(table=definition.fact_table, column=join.fact_key),
                ColumnReference(table=join.table, column=join.lookup_key),
            ]
        )
    for reference in references:
        if reference.table not in allowed or reference.column not in columns.get(reference.table, set()):
            raise ReportValidationError(
                [
                    CalculationCheck(
                        code="unknown_column",
                        passed=False,
                        message=f"Unknown or unjoined column: {reference.table}.{reference.column}",
                    )
                ]
            )


def _metadata(catalog: DataCatalog) -> SchemaInfo:
    tables = []
    for name in catalog.table_names():
        description = catalog.connection.execute(f"DESCRIBE SELECT * FROM {_qualified(name)}").fetchall()
        tables.append(
            TableInfo(
                name=name,
                row_count=0,
                columns=[ColumnInfo(name=str(row[0]), dtype=str(row[1])) for row in description],
            )
        )
    return SchemaInfo(tables=tables)


def _amount(expression: str) -> str:
    return f"TRY_CAST(TRIM(CAST({expression} AS VARCHAR)) AS DECIMAL(28,6))"


def _invalid_amount(expression: str) -> str:
    pattern = _literal(r"[-+]?[0-9]+(\.[0-9]{1,6})?")
    return (
        f"({expression} IS NULL OR {_amount(expression)} IS NULL OR NOT "
        f"regexp_full_match(TRIM(CAST({expression} AS VARCHAR)), {pattern}))"
    )


def _percent(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return format((numerator / denominator * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def calculate_sales_report(
    catalog: DataCatalog,
    definition: SalesDefinition,
    period: ReportPeriod,
    *,
    timeout_seconds: float = 30,
) -> VerifiedSalesResult:
    if timeout_seconds <= 0:
        raise ValueError("The query timeout must be positive")
    schema = _metadata(catalog)
    validate_definition(definition, schema)
    checks: list[CalculationCheck] = []
    warnings: list[str] = []
    aliases = {definition.fact_table: "f", **{join.table: f"j{i}" for i, join in enumerate(definition.joins)}}

    def reference(value: ColumnReference) -> str:
        return f"{aliases[value.table]}.{_quote(value.column)}"

    def fact(column: str) -> str:
        return f"f.{_quote(column)}"

    def check(code: str, sql: str, message: str) -> None:
        affected = int(catalog.query(sql, timeout_seconds=timeout_seconds).iloc[0, 0])
        checks.append(
            CalculationCheck(code=code, passed=affected == 0, affected_rows=affected, message=message)
        )
        if affected:
            raise ReportValidationError(checks)

    source = f"{_qualified(definition.fact_table)} AS f"
    row_key = fact(definition.row_key)
    check(
        "duplicate_grain",
        f"SELECT COUNT(*) - COUNT(DISTINCT {row_key}) FROM {source}",
        "The approved row key must be unique and non-null; duplicate or missing keys would distort totals",
    )
    for index, join in enumerate(definition.joins):
        table_sql = _qualified(join.table)
        key_sql = _quote(join.lookup_key)
        check(
            f"join_{index}_lookup_unique",
            f"SELECT COUNT(*) - COUNT(DISTINCT {key_sql}) FROM {table_sql}",
            f"Join {join.table}: lookup keys must be unique and non-null",
        )
        check(
            f"join_{index}_fact_unique",
            f"SELECT COUNT(*) - COUNT(DISTINCT {_quote(join.fact_key)}) "
            f"FROM {_qualified(definition.fact_table)}",
            f"Join {join.table}: fact keys must be unique and non-null; only one-to-one "
            "monetary joins are supported",
        )
        source += f" LEFT JOIN {table_sql} AS j{index} ON {fact(join.fact_key)} = j{index}.{key_sql}"
        check(
            f"join_{index}_matches",
            f"SELECT COUNT(*) FROM {source} WHERE j{index}.{key_sql} IS NULL",
            f"Join {join.table}: unmatched source keys must be resolved before reporting",
        )
    filters = (
        " AND ".join(
            f"CAST({fact(item.column)} AS VARCHAR) "
            f"{'=' if item.operator == 'equals' else '<>'} {_literal(item.value)}"
            for item in definition.filters
        )
        or "TRUE"
    )
    date_expression = (
        f"TRY_STRPTIME(TRIM(CAST({fact(definition.date_column)} AS VARCHAR)), "
        f"{_literal(definition.date_format)})::DATE"
    )
    check(
        "invalid_dates",
        f"SELECT COUNT(*) FROM {source} WHERE ({filters}) AND {date_expression} IS NULL",
        "Missing or invalid dates: confirm the selected date format before reporting",
    )
    window = (
        f"({filters}) AND {date_expression} BETWEEN DATE '{period.previous_start.isoformat()}' "
        f"AND DATE '{period.end_date.isoformat()}'"
    )
    amounts = {"revenue": fact(definition.revenue_column)}
    if definition.refunds:
        amounts["refunds"] = reference(definition.refunds)
    if definition.cost:
        amounts["cost"] = reference(definition.cost)
    for name, expression in amounts.items():
        check(
            f"invalid_{name}",
            f"SELECT COUNT(*) FROM {source} WHERE ({window}) AND {_invalid_amount(expression)}",
            f"Invalid {name}: amounts must be present, finite decimals with at most six decimal places; "
            "currency symbols, grouping separators and scientific notation require an explicit "
            "import correction",
        )
    if definition.refunds:
        check(
            "negative_refunds",
            f"SELECT COUNT(*) FROM {source} WHERE ({window}) AND {_amount(amounts['refunds'])} < 0",
            "Refund amounts must be non-negative amounts to subtract; confirm the source sign convention",
        )
    if definition.currency_column:
        currency_column = fact(definition.currency_column)
        check(
            "currency_mismatch",
            f"SELECT COUNT(*) FROM {source} WHERE ({window}) AND ({currency_column} IS NULL OR "
            f"UPPER(TRIM(CAST({currency_column} AS VARCHAR))) <> {_literal(definition.currency)})",
            "Reporting period includes missing or different currencies; currency conversion is not inferred",
        )
    else:
        warnings.append(
            f"Currency {definition.currency} is owner-declared; no source currency column was checked"
        )
    order_id = fact(definition.order_id_column or definition.row_key)
    check(
        "missing_order_id",
        f"SELECT COUNT(*) FROM {source} WHERE ({window}) AND {order_id} IS NULL",
        "Order identifiers must be present for the approved distinct-order calculation",
    )
    for table in schema.tables:
        for column in table.columns:
            if column.dtype.upper() in {"DOUBLE", "FLOAT", "REAL"}:
                mapped = {(definition.fact_table, definition.revenue_column)}
                mapped |= {
                    (item.table, item.column) for item in (definition.cost, definition.refunds) if item
                }
                if (table.name, column.name) in mapped:
                    warnings.append(
                        f"{table.name}.{column.name} was imported as floating-point; "
                        "reimport as text for source-decimal precision"
                    )
    revenue = _amount(amounts["revenue"])
    refund = _amount(amounts["refunds"]) if "refunds" in amounts else "CAST(0 AS DECIMAL(28,6))"
    cost = _amount(amounts["cost"]) if "cost" in amounts else "CAST(NULL AS DECIMAL(28,6))"
    selects = []
    for sort, (label, start, end) in enumerate(
        (
            ("current", period.start_date, period.end_date),
            ("previous", period.previous_start, period.previous_end),
        )
    ):
        selects.append(
            f"SELECT {sort} AS sort_order, '{label}' AS period, '{start.isoformat()}' AS start_date, "
            f"'{end.isoformat()}' AS end_date, COUNT(*) AS row_count, "
            "COUNT(DISTINCT order_id) AS order_count, "
            "SUM(revenue) AS gross_sales, SUM(refunds) AS refunds, SUM(revenue - refunds) AS net_sales, "
            "SUM(cost) AS cost, SUM(revenue - refunds - cost) AS gross_profit FROM report_base "
            f"WHERE business_date BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'"
        )
    sql = (
        f"WITH report_base AS (SELECT {date_expression} AS business_date, {order_id} AS order_id, "
        f"{revenue} AS revenue, {refund} AS refunds, {cost} AS cost FROM {source} WHERE {window}), "
        f"totals AS ({' UNION ALL '.join(selects)}) "
        "SELECT period, start_date, end_date, row_count, order_count, "
        "CAST(gross_sales AS VARCHAR) AS gross_sales, CAST(refunds AS VARCHAR) AS refunds, "
        "CAST(net_sales AS VARCHAR) AS net_sales, CAST(cost AS VARCHAR) AS cost, "
        "CAST(gross_profit AS VARCHAR) AS gross_profit FROM totals ORDER BY sort_order"
    )
    frame = catalog.query(sql, timeout_seconds=timeout_seconds)
    rows = frame.to_dict(orient="records")
    if int(rows[0]["row_count"]) == 0:
        raise ReportValidationError(
            [
                *checks,
                CalculationCheck(
                    code="no_current_rows",
                    passed=False,
                    message="No source rows match this reporting period and filters",
                ),
            ]
        )
    if int(rows[1]["row_count"]) == 0:
        warnings.append("No previous-period rows; growth comparison is unavailable, not zero")
    if not definition.cost:
        warnings.append("No cost source is mapped; gross profit and margin are unavailable")
    if not definition.refunds:
        warnings.append("The owner confirmed no refunds; net sales assumes zero refunds")
    for row in rows:
        for key in ("gross_sales", "refunds", "net_sales", "cost", "gross_profit"):
            row[key] = str(row[key]) if row[key] is not None else None
        row["row_count"], row["order_count"] = int(row["row_count"]), int(row["order_count"])
        row["margin_percent"] = (
            _percent(Decimal(row["gross_profit"]), Decimal(row["net_sales"]))
            if row["gross_profit"] is not None and row["net_sales"] is not None
            else None
        )
        if row["row_count"] and row["net_sales"] is not None and Decimal(row["net_sales"]) == 0:
            warnings.append(f"{row['period']}: net sales is zero; margin percentage is undefined")
    current, previous = rows
    change = None
    growth = None
    if previous["row_count"]:
        with localcontext() as context:
            context.prec = 50
            change_decimal = Decimal(current["net_sales"]) - Decimal(previous["net_sales"])
            change = format(change_decimal, "f")
            growth = _percent(change_decimal, Decimal(previous["net_sales"]))
        if Decimal(previous["net_sales"]) <= 0:
            growth = None
            warnings.append(
                "Previous net sales is non-positive; conventional growth percentage is unavailable"
            )
    comparison = {"net_sales_change": change, "net_sales_change_percent": growth}
    metric_definitions = {
        "gross_sales": f"SUM({definition.fact_table}.{definition.revenue_column})",
        "refunds": "SUM(approved per-row refund amount)"
        if definition.refunds
        else "0 (owner-confirmed assumption)",
        "net_sales": "gross_sales - refunds",
        "order_count": f"COUNT(DISTINCT {definition.order_id_column or definition.row_key})",
        "cost": "SUM(approved per-row total cost); not unit cost" if definition.cost else "not configured",
        "gross_profit": "net_sales - cost",
        "margin_percent": "100 * gross_profit / net_sales; undefined when net_sales = 0",
        "net_sales_change_percent": "100 * (current net_sales - previous net_sales) / previous net_sales; "
        "only when previous rows exist and previous net_sales > 0",
    }
    evidence = [
        EvidenceValue(id=f"{row['period']}.{column}", row=index, column=column, value=row[column])
        for index, row in enumerate(rows)
        for column in (
            "row_count",
            "order_count",
            "gross_sales",
            "refunds",
            "net_sales",
            "cost",
            "gross_profit",
            "margin_percent",
        )
    ]
    quantum = Decimal(1).scaleb(-definition.display_decimals)
    with localcontext() as context:
        context.prec = 50
        display_net = format(Decimal(current["net_sales"]).quantize(quantum, rounding=ROUND_HALF_UP), "f")
    lines = [
        "## Sales and margin summary",
        f"Period: {period.start_date.isoformat()} to {period.end_date.isoformat()} (inclusive).",
        f"- Net sales: {definition.currency} {display_net}. Evidence: `current.net_sales`.",
        f"- Orders: {current['order_count']}. Evidence: `current.order_count`.",
    ]
    if current["margin_percent"] is not None:
        lines.append(f"- Gross margin: {current['margin_percent']}%. Evidence: `current.margin_percent`.")
    if growth is not None:
        lines.append(
            f"- Net sales changed {growth}% versus the preceding equal-length period. "
            "Evidence: `current.net_sales`, `previous.net_sales`; see the approved growth formula."
        )
    if warnings:
        lines.extend(["### Review notes", *[f"- {warning}" for warning in warnings]])
    lines.append(
        "Calculations use the approved definition. They do not establish causes or certify the source data."
    )
    return VerifiedSalesResult(
        columns=[*list(frame.columns), "margin_percent"],
        rows=rows,
        sql=sql,
        summary="\n\n".join(lines),
        checks=checks,
        warnings=warnings,
        evidence=evidence,
        comparison=comparison,
        metric_definitions=metric_definitions,
        verification="needs_review" if warnings else "checks_passed",
    )

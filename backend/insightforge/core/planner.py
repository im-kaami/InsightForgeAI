import logging
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from insightforge.core.llm import LLMClient, LLMResponse, describe_error
from insightforge.core.memory import ConversationMemory
from insightforge.core.schema import SchemaInfo, TableInfo


class SqlStep(BaseModel):
    name: str
    action: Literal["sql"] = "sql"
    query: str
    description: str = ""


class PlotStep(BaseModel):
    name: str
    action: Literal["plot"] = "plot"
    kind: Literal["line", "bar", "scatter", "pie", "histogram"]
    data_source: str
    x: str
    y: str | None = None
    color: str | None = None
    title: str = ""


class SummaryStep(BaseModel):
    name: str
    action: Literal["summary"] = "summary"
    focus: str = ""


Step = Annotated[SqlStep | PlotStep | SummaryStep, Field(discriminator="action")]


class Plan(BaseModel):
    steps: list[Step]


class PlanValidationError(ValueError):
    pass


_STEP_ADAPTER = TypeAdapter(Step)


def _quote(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def _safe_step_name(prefix: str, column: str) -> str:
    suffix = re.sub(r"\W+", "_", column.lower()).strip("_") or "column"
    return f"{prefix}_{suffix}"


def validate_plan(raw: Any, schema: SchemaInfo) -> Plan:
    del schema
    values = raw.get("steps") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise PlanValidationError("Plan must be a list or an object containing a steps list")

    steps: list[Step] = []
    names: set[str] = set()
    sql_names: set[str] = set()
    has_summary = False
    for value in values:
        if len(steps) >= 6:
            break
        try:
            step = _STEP_ADAPTER.validate_python(value)
        except (ValidationError, TypeError):
            continue
        if step.name in names:
            continue
        if isinstance(step, PlotStep) and step.data_source not in sql_names:
            continue
        if isinstance(step, SummaryStep):
            if has_summary:
                continue
            has_summary = True
        if isinstance(step, SqlStep):
            sql_names.add(step.name)
        names.add(step.name)
        steps.append(step)

    if not sql_names:
        raise PlanValidationError("Plan must contain at least one SQL step")
    if not has_summary:
        if len(steps) == 6:
            removable = next(
                (
                    index
                    for index in range(len(steps) - 1, -1, -1)
                    if not isinstance(steps[index], SqlStep)
                ),
                5,
            )
            steps.pop(removable)
        name = "summary"
        counter = 2
        while name in names:
            name = f"summary_{counter}"
            counter += 1
        steps.append(SummaryStep(name=name))
    return Plan(steps=steps)


def _is_text(dtype: str) -> bool:
    return any(token in dtype.upper() for token in ("VARCHAR", "TEXT", "STRING", "CHAR"))


def _is_numeric(dtype: str) -> bool:
    return any(
        token in dtype.upper()
        for token in (
            "TINYINT",
            "SMALLINT",
            "INTEGER",
            "BIGINT",
            "HUGEINT",
            "FLOAT",
            "DOUBLE",
            "DECIMAL",
            "NUMERIC",
            "REAL",
        )
    )


def fallback_plan(goal: str, schema: SchemaInfo) -> Plan:
    del goal
    if not schema.tables:
        raise PlanValidationError("Cannot create a fallback plan without tables")
    table: TableInfo = max(schema.tables, key=lambda item: item.row_count)
    table_sql = (
        _quote(table.name)
        if "." not in table.name
        else ".".join(_quote(part) for part in table.name.split("."))
    )
    steps: list[Step] = [
        SqlStep(name="row_count", query=f"SELECT COUNT(*) AS row_count FROM {table_sql}")
    ]
    text_column = next((column for column in table.columns if _is_text(column.dtype)), None)
    if text_column:
        column_sql = _quote(text_column.name)
        sql_name = _safe_step_name("top", text_column.name)
        steps.extend(
            [
                SqlStep(
                    name=sql_name,
                    query=(
                        f"SELECT {column_sql}, COUNT(*) AS count FROM {table_sql} "
                        f"GROUP BY {column_sql} ORDER BY count DESC LIMIT 10"
                    ),
                ),
                PlotStep(
                    name=f"plot_{sql_name}",
                    kind="bar",
                    data_source=sql_name,
                    x=text_column.name,
                    y="count",
                    title=f"Top {text_column.name}",
                ),
            ]
        )
    numeric_column = next((column for column in table.columns if _is_numeric(column.dtype)), None)
    if numeric_column:
        column_sql = _quote(numeric_column.name)
        steps.append(
            SqlStep(
                name=_safe_step_name("stats", numeric_column.name),
                query=(
                    f"SELECT MIN({column_sql}) AS min, AVG({column_sql}) AS avg, "
                    f"MAX({column_sql}) AS max FROM {table_sql}"
                ),
            )
        )
    steps.append(SummaryStep(name="summary"))
    return Plan(steps=steps)


class Planner:
    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.last_used_fallback = False
        self.last_fallback_reason: str | None = None
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def _record_usage(self, response: LLMResponse) -> None:
        self.last_usage["prompt_tokens"] += response.prompt_tokens
        self.last_usage["completion_tokens"] += response.completion_tokens

    def plan(
        self, goal: str, schema: SchemaInfo, memory: ConversationMemory | None = None
    ) -> Plan:
        self.last_used_fallback = False
        self.last_fallback_reason = None
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        schema_text = schema.to_prompt()
        system = f"""You are a data-analysis planner. The available schema is:
{schema_text}

Use DuckDB SQL. Rules:
- Use only listed tables and columns exactly as named; quote identifiers with double quotes if they
  contain spaces or uppercase.
- Every plot must reference a prior SQL step by data_source and use columns from that step's SELECT.
- Aggregate before plotting.
- End with exactly one summary step and keep the plan to at most 6 steps.
- Respond ONLY with JSON {{"steps": [...]}}.
Example:
{{"steps":[
  {{"name":"count_rows","action":"sql","query":"SELECT COUNT(*) AS count FROM table_name"}},
  {{"name":"summary","action":"summary","focus":"row count"}}
]}}"""
        if memory and memory.turns:
            system += (
                "\n\nConversation so far:\n"
                f"{memory.to_prompt()}\nTreat the current goal as a follow-up to this conversation."
            )
        try:
            raw, response = self.llm.chat_json(
                [{"role": "system", "content": system}, {"role": "user", "content": goal}]
            )
            self._record_usage(response)
            return validate_plan(raw, schema)
        except Exception as exc:
            self.last_used_fallback = True
            self.last_fallback_reason = describe_error(exc)
            logging.getLogger("insightforge").warning(
                "Planner falling back to profiling plan: %s", self.last_fallback_reason
            )
            return fallback_plan(goal, schema)

    def repair_sql(self, step: SqlStep, error: str, schema: SchemaInfo) -> SqlStep:
        messages = [
            {
                "role": "system",
                "content": (
                    "Correct the DuckDB SQL using only this schema. Respond only with JSON "
                    f'{{"query":"..."}}.\n{schema.to_prompt()}'
                ),
            },
            {
                "role": "user",
                "content": f"Query:\n{step.query}\n\nError:\n{error}",
            },
        ]
        raw, response = self.llm.chat_json(messages)
        self._record_usage(response)
        if not isinstance(raw, dict) or not isinstance(raw.get("query"), str):
            raise PlanValidationError("SQL repair response must contain a query string")
        return step.model_copy(update={"query": raw["query"]})

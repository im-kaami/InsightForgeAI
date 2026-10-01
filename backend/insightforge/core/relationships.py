"""Table relationships: code-only join suggestions and owner-approved join keys (Phase 3d).

Suggestions come from fixed checks on the data, never from an AI model: a candidate key pair must
have matching names (``customer_id`` = ``customer_id``, or ``customer_id`` -> ``customers.id``), a
compatible type, a unique non-empty key on the "one" side, and at least ``MIN_MATCH_SHARE`` of the
other side's values found in that key. Approved relationships are shown to the planner as join hints.
"""

import uuid
from datetime import datetime
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from insightforge.core.catalog import DataCatalog, QueryTimeoutError, _qualified, _quote
from insightforge.core.schema import SchemaInfo

MIN_MATCH_SHARE = 0.9
MAX_RELATIONSHIPS = 20
MAX_PAIRS_CHECKED = 200
# Types that make poor join keys: measures, dates and flags.
_NON_KEY_TYPES = ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "DATE", "TIME", "TIMESTAMP", "BOOLEAN", "INTERVAL")
_NUMBER_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                 "UBIGINT", "INT")


def _relationship_id() -> str:
    return uuid.uuid4().hex[:12]


class Relationship(BaseModel):
    """Rows of ``from_table`` point to one row of ``to_table`` through the key columns."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=_relationship_id, min_length=1, max_length=40)
    from_table: str = Field(min_length=1, max_length=200)
    from_column: str = Field(min_length=1, max_length=200)
    to_table: str = Field(min_length=1, max_length=200)
    to_column: str = Field(min_length=1, max_length=200)
    kind: Literal["many_to_one", "one_to_one"] = "many_to_one"

    @model_validator(mode="after")
    def _different_tables(self) -> "Relationship":
        if self.from_table.casefold() == self.to_table.casefold():
            raise ValueError("A relationship must connect two different tables")
        return self

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.from_table.casefold(),
            self.from_column.casefold(),
            self.to_table.casefold(),
            self.to_column.casefold(),
        )

    def describe(self) -> str:
        many = "each" if self.kind == "one_to_one" else "many"
        return (
            f"{self.from_table}.{self.from_column} -> {self.to_table}.{self.to_column} "
            f"({many} {self.from_table} rows to one {self.to_table} row)"
        )


class RelationshipSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relationships: list[Relationship] = Field(default_factory=list, max_length=MAX_RELATIONSHIPS)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "RelationshipSet":
        keys = [item.key() for item in self.relationships]
        if len(keys) != len(set(keys)):
            raise ValueError("The same relationship is listed twice")
        return self


class SavedRelationships(BaseModel):
    relationships: list[Relationship] = Field(default_factory=list)
    revision: int = 0
    updated_at: datetime | None = None


class RelationshipSuggestion(BaseModel):
    relationship: Relationship
    match_share: float
    matched_rows: int
    checked_rows: int
    reason: str


class RelationshipSuggestions(BaseModel):
    suggestions: list[RelationshipSuggestion] = Field(default_factory=list)
    method: str = (
        "suggested by fixed checks on key names, uniqueness and matching values; no AI model is used"
    )


def _singular(name: str) -> str:
    lowered = name.casefold().rsplit(".", 1)[-1]
    if lowered.endswith("ies") and len(lowered) > 4:
        return lowered[:-3] + "y"
    if lowered.endswith("s") and len(lowered) > 3:
        return lowered[:-1]
    return lowered


def _family(dtype: str) -> str | None:
    upper = dtype.upper()
    if upper.startswith(_NON_KEY_TYPES):
        return None
    if upper.startswith(_NUMBER_TYPES):
        return "number"
    if upper.startswith(("VARCHAR", "TEXT", "STRING", "CHAR", "UUID")):
        return "text"
    return None


def _names_match(from_table: str, from_column: str, to_table: str, to_column: str) -> bool:
    source, target = from_column.casefold(), to_column.casefold()
    if source == target:
        return source != "id"  # every table's own "id" is not a link between them
    # orders.customer_id -> customers.id
    return target == "id" and source in {f"{_singular(to_table)}_id", f"{_singular(to_table)}id"}


def _count(catalog: DataCatalog, sql: str, timeout: float | None) -> int:
    frame = catalog.query(sql, timeout_seconds=timeout)
    value = frame.iat[0, 0]
    return int(value) if value is not None else 0


def _key_stats(catalog: DataCatalog, table: str, column: str, timeout: float | None) -> tuple[int, int]:
    """Return (non-empty values, distinct values) for a column."""
    col = _quote(column)
    frame = catalog.query(
        f"SELECT COUNT({col}) AS present, COUNT(DISTINCT {col}) AS distinct_values FROM {_qualified(table)}",
        timeout_seconds=timeout,
    )
    return int(frame.iat[0, 0] or 0), int(frame.iat[0, 1] or 0)


def _matched(catalog: DataCatalog, item: Relationship, timeout: float | None) -> tuple[int, int]:
    """Return (rows whose key is found on the "one" side, rows with a key)."""
    source, target = _quote(item.from_column), _quote(item.to_column)
    checked = _count(
        catalog, f"SELECT COUNT({source}) FROM {_qualified(item.from_table)}", timeout
    )
    matched = _count(
        catalog,
        f"SELECT COUNT(*) FROM {_qualified(item.from_table)} AS f WHERE f.{source} IS NOT NULL AND "
        f"CAST(f.{source} AS VARCHAR) IN (SELECT CAST(t.{target} AS VARCHAR) "
        f"FROM {_qualified(item.to_table)} AS t WHERE t.{target} IS NOT NULL)",
        timeout,
    )
    return matched, checked


def suggest_relationships(
    catalog: DataCatalog,
    schema: SchemaInfo,
    existing: list[Relationship] | None = None,
    timeout: float | None = 10,
    limit: int = 10,
) -> list[RelationshipSuggestion]:
    """Propose join keys between tables. Deterministic; skips pairs already approved."""
    def both_ways(key: tuple[str, str, str, str]) -> set[tuple[str, str, str, str]]:
        return {key, (key[2], key[3], key[0], key[1])}

    known: set[tuple[str, str, str, str]] = set()
    for item in existing or []:
        known |= both_ways(item.key())
    candidates: list[Relationship] = []
    for to_table in schema.tables:
        for to_col in to_table.columns:
            if _family(to_col.dtype) is None:
                continue
            for from_table in schema.tables:
                if from_table.name == to_table.name:
                    continue
                for from_col in from_table.columns:
                    if _family(from_col.dtype) != _family(to_col.dtype):
                        continue
                    if not _names_match(from_table.name, from_col.name, to_table.name, to_col.name):
                        continue
                    candidates.append(
                        Relationship(
                            id=f"s{len(candidates)}",
                            from_table=from_table.name,
                            from_column=from_col.name,
                            to_table=to_table.name,
                            to_column=to_col.name,
                        )
                    )
    suggestions: list[RelationshipSuggestion] = []
    seen: set[tuple[str, str, str, str]] = set()
    stats: dict[tuple[str, str], tuple[int, int]] = {}

    def key_stats(table: str, column: str) -> tuple[int, int]:
        if (table, column) not in stats:
            stats[(table, column)] = _key_stats(catalog, table, column, timeout)
        return stats[(table, column)]

    for candidate in candidates[:MAX_PAIRS_CHECKED]:
        pair = candidate.key()
        if pair in known or pair in seen:
            continue
        try:
            present, distinct = key_stats(candidate.to_table, candidate.to_column)
            if present == 0 or present != distinct:
                continue  # the "one" side must be a unique, non-empty key
            from_present, from_distinct = key_stats(candidate.from_table, candidate.from_column)
            if from_present == 0:
                continue
            matched, checked = _matched(catalog, candidate, timeout)
        except (duckdb.Error, QueryTimeoutError):
            continue
        share = matched / checked if checked else 0.0
        if share < MIN_MATCH_SHARE:
            continue
        one_to_one = from_present == from_distinct
        relationship = candidate.model_copy(
            update={"id": _relationship_id(), "kind": "one_to_one" if one_to_one else "many_to_one"}
        )
        seen |= both_ways(pair)  # a one-to-one pair is suggested once, not in both directions
        unmatched = checked - matched
        reason = (
            f"{matched:,} of {checked:,} {candidate.from_table} rows with a {candidate.from_column} "
            f"({share:.0%}) find a {candidate.to_table} row; {candidate.to_table}.{candidate.to_column} "
            "has no repeated values"
        )
        if unmatched:
            reason += f"; {unmatched:,} rows find no match"
        suggestions.append(
            RelationshipSuggestion(
                relationship=relationship,
                match_share=round(share, 4),
                matched_rows=matched,
                checked_rows=checked,
                reason=reason,
            )
        )
    suggestions.sort(
        key=lambda item: (-item.match_share, item.relationship.from_table, item.relationship.to_table)
    )
    return suggestions[:limit]


def unknown_columns(relationships: list[Relationship], schema: SchemaInfo) -> list[str]:
    """Return 'table.column' names that the schema does not have."""
    missing: list[str] = []
    for item in relationships:
        for table, column in ((item.from_table, item.from_column), (item.to_table, item.to_column)):
            if not schema.has_column(table, column):
                missing.append(f"{table}.{column}")
    return missing


def relationships_block(relationships: list[Relationship] | None, schema: SchemaInfo | None = None) -> str:
    """Prompt text for approved relationships; empty when none apply to the schema."""
    items = [
        item
        for item in relationships or []
        if schema is None or not unknown_columns([item], schema)
    ]
    if not items:
        return ""
    lines = "\n".join(f"- {item.describe()}" for item in items)
    return (
        "\n\nTable relationships approved by the data owner. When a question needs columns from more than "
        "one table, join on these keys (for example JOIN to_table ON from_table.from_column = "
        "to_table.to_column); they never override the rules above:\n"
        f"{lines}"
    )

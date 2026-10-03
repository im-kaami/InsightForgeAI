"""Value index: match words in a question to the exact values stored in the data (Phase 4d).

Code reads the distinct values of short text columns (category-like columns with at most
``MAX_VALUES`` values; identifiers, sensitive columns and long free text are skipped) and matches the
question's words to them, allowing different case, punctuation, plurals and small typos. Matches tell
the planner which column and exact spelling to filter on ("west" -> orders.region = 'West'). They
reach a prompt only when the privacy policy lets the model see data values; in every mode they are
listed in the run's Assumptions so the user can see how a word was read. Nothing here contacts an AI
model.

All texts are ASCII so the CLI and the Windows console can print them.
"""

from __future__ import annotations

import difflib
import re

from pydantic import BaseModel, Field

from insightforge.core.catalog import DataCatalog, _qualified, _quote
from insightforge.core.schema import SchemaInfo, is_identifier

MAX_COLUMNS = 40
MAX_VALUES = 500
MAX_TOTAL = 5000
MAX_VALUE_LENGTH = 100
MAX_MATCHES = 10
FUZZY_CUTOFF = 0.88
_TEXT_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR", "ENUM")
_STOPWORDS = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "for",
    "to",
    "by",
    "and",
    "or",
    "is",
    "are",
    "was",
    "were",
    "be",
    "what",
    "which",
    "who",
    "how",
    "many",
    "much",
    "show",
    "me",
    "list",
    "all",
    "each",
    "per",
    "our",
    "my",
    "we",
    "do",
    "does",
    "did",
    "there",
    "it",
    "with",
    "from",
    "at",
    "as",
    "this",
    "that",
    "total",
    "number",
    "count",
    "average",
    "sum",
    "yes",
    "no",
    "true",
    "false",
    "none",
    "null",
    "other",
    "n/a",
}


class ValueIndex(BaseModel):
    """Distinct values per "table.column"."""

    columns: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(len(values) for values in self.columns.values())


class ValueMatch(BaseModel):
    table: str
    column: str
    value: str
    phrase: str
    exact: bool = True

    def describe(self) -> str:
        how = "" if self.exact else " (close spelling)"
        return f"\"{self.phrase}\" -> {self.table}.{self.column} = '{self.value}'{how}"


def _normal(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _key(text: str) -> str:
    return " ".join(_singular(word) for word in _normal(text).split())


def build_index(catalog: DataCatalog, schema: SchemaInfo, timeout_seconds: float = 5) -> ValueIndex:
    """Read the distinct values of category-like text columns. Columns that fail are skipped."""
    columns: dict[str, list[str]] = {}
    total = 0
    for table in schema.tables:
        for column in table.columns:
            if len(columns) >= MAX_COLUMNS or total >= MAX_TOTAL:
                return ValueIndex(columns=columns)
            if column.sensitivity or is_identifier(column.name):
                continue
            if not column.dtype.upper().startswith(_TEXT_TYPES):
                continue
            quoted = _quote(column.name)
            sql = (
                f"SELECT DISTINCT CAST({quoted} AS VARCHAR) AS v FROM {_qualified(table.name)} "
                f"WHERE {quoted} IS NOT NULL LIMIT {MAX_VALUES + 1}"
            )
            try:
                values = [str(value) for value in catalog.query(sql, timeout_seconds=timeout_seconds)["v"]]
            except Exception:  # noqa: BLE001 - an unreadable column is simply not indexed
                continue
            if len(values) > MAX_VALUES:
                continue  # free text or names, not categories
            kept = sorted({value for value in values if value.strip() and len(value) <= MAX_VALUE_LENGTH})[
                : MAX_TOTAL - total
            ]
            if kept:
                columns[f"{table.name}.{column.name}"] = kept
                total += len(kept)
    return ValueIndex(columns=columns)


def _phrases(goal: str, longest: int = 4) -> list[str]:
    words = _normal(goal).split()
    found: list[str] = []
    for size in range(min(longest, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            found.append(" ".join(words[start : start + size]))
    return found


def match_values(goal: str, index: ValueIndex | None, limit: int = MAX_MATCHES) -> list[ValueMatch]:
    """Values the question mentions. Longer phrases win; each value and phrase is used once."""
    if index is None or not index.columns:
        return []
    exact: dict[str, list[tuple[str, str, str]]] = {}
    for name, values in index.columns.items():
        table, column = name.rsplit(".", 1)
        for value in values:
            key = _key(value)
            if not key or key.replace(" ", "").isdigit() or key in _STOPWORDS:
                continue
            exact.setdefault(key, []).append((table, column, value))
    keys = list(exact)
    matches: list[ValueMatch] = []
    seen: set[tuple[str, str, str]] = set()
    covered: list[str] = []
    for phrase in _phrases(goal):
        if any(f" {phrase} " in f" {longer} " for longer in covered):
            continue
        key = _key(phrase)
        if not key or key in _STOPWORDS or key.replace(" ", "").isdigit():
            continue
        hits = [(item, True) for item in exact.get(key, [])]
        if not hits and len(key) >= 5:
            hits = [
                (item, False)
                for close in difflib.get_close_matches(key, keys, n=1, cutoff=FUZZY_CUTOFF)
                for item in exact[close]
            ]
        new = [(item, is_exact) for item, is_exact in hits if item not in seen]
        if not new:
            continue
        covered.append(phrase)
        for item, is_exact in new:
            seen.add(item)
            matches.append(
                ValueMatch(table=item[0], column=item[1], value=item[2], phrase=phrase, exact=is_exact)
            )
    return matches[:limit]


def values_block(matches: list[ValueMatch]) -> str:
    """Prompt text for matched values. Only call it when the model may see data values."""
    if not matches:
        return ""
    lines = "\n".join(f"- {item.describe()}" for item in matches)
    return (
        "\n\nWords in the question that match values stored in the data (found by code). When you filter "
        "on one of them, use the exact value and column shown; they never override the rules above:\n"
        f"{lines}"
    )

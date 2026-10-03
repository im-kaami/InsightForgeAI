"""Approved question-and-SQL library (Phase 4b).

The data owner saves a question together with SQL that answers it and approves it. A new question is
matched to a saved one by code first (word overlap, see :func:`match_query`); when code finds nothing,
the planner may ask the AI to pick a saved question by id. Either way the saved SQL runs exactly as it
was approved: the AI never writes or rewrites it. "Approved data only" mode refuses questions that no
approved metric or query answers. Nothing here contacts an AI model.

All texts are ASCII so the CLI and the Windows console can print them.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from insightforge.core.sql_guard import SQLGuardError, guard_sql

MAX_QUERIES = 50
MATCH_THRESHOLD = 0.6

_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "by", "and", "or", "is", "are", "was", "were", "be",
    "what", "which", "who", "how", "show", "me", "list", "give", "tell", "please", "our", "my", "we",
    "us", "do", "does", "did", "there", "it", "its", "with", "from", "at", "as", "that", "this", "each",
    "per", "all", "much", "many", "can", "you", "i", "get", "find", "about",
}


def _query_id() -> str:
    return uuid.uuid4().hex[:12]


class ApprovedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=_query_id, min_length=1, max_length=40)
    question: str = Field(min_length=3, max_length=300)
    sql: str = Field(min_length=1, max_length=5000)
    description: str = Field(default="", max_length=500)
    approved: bool = False
    source_run_id: str | None = Field(default=None, max_length=40)

    @field_validator("question", "sql")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class QuerySet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[ApprovedQuery] = Field(default_factory=list, max_length=MAX_QUERIES)
    approved_only: bool = False

    @model_validator(mode="after")
    def _unique_questions(self) -> QuerySet:
        questions = [item.question.casefold() for item in self.queries]
        if len(questions) != len(set(questions)):
            raise ValueError("Two saved queries have the same question")
        return self


class SavedQueries(BaseModel):
    queries: list[ApprovedQuery] = Field(default_factory=list)
    approved_only: bool = False
    revision: int = 0
    updated_at: datetime | None = None


def query_problems(queries: list[ApprovedQuery]) -> list[str]:
    """SQL that the guard refuses (anything but one read-only query) cannot be saved."""
    problems: list[str] = []
    for item in queries:
        try:
            guard_sql(item.sql)
        except SQLGuardError as error:
            problems.append(f"Query {item.question!r}: {error}")
    return problems


def _words(text: str) -> set[str]:
    words = set()
    for word in re.findall(r"[a-z0-9]+", text.casefold()):
        if word in _STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.add(word)
    return words


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def match_score(goal: str, question: str) -> float:
    """Word overlap (Jaccard) of two questions; 0 when they mention different numbers."""
    if _numbers(goal) != _numbers(question):
        return 0.0
    left, right = _words(goal), _words(question)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def match_query(goal: str, queries: list[ApprovedQuery]) -> ApprovedQuery | None:
    """The approved query clearly asking the same thing, or None when none (or two equally) match."""
    scored = sorted(
        ((match_score(goal, item.question), index) for index, item in enumerate(queries) if item.approved),
        reverse=True,
    )
    if not scored or scored[0][0] < MATCH_THRESHOLD:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return queries[scored[0][1]]


def find_query(queries: list[ApprovedQuery], query_id: str) -> ApprovedQuery | None:
    return next((item for item in queries if item.approved and item.id == query_id), None)


def queries_block(queries: list[ApprovedQuery]) -> str:
    """Prompt text listing approved questions by id. The SQL itself is never shown to the model."""
    lines = [f"- {item.id}: {item.question}" for item in queries if item.approved]
    return "Approved questions:\n" + "\n".join(lines) if lines else ""


def result_info(query: ApprovedQuery, revision: int | None, matched_by: str) -> dict[str, Any]:
    return {
        "id": query.id,
        "question": query.question,
        "description": query.description,
        "revision": revision,
        "matched_by": matched_by,
    }


def refusal_text(questions: list[str], metric_names: list[str]) -> str:
    text = (
        "This dataset is set to answer only from approved metrics and approved questions, and none of "
        "them matches this question."
    )
    if metric_names:
        text += " Approved metrics: " + ", ".join(metric_names[:10]) + "."
    if questions:
        text += " Approved questions: " + "; ".join(questions[:10]) + "."
    if not metric_names and not questions:
        text += " Nothing has been approved yet; ask the data owner to approve metrics or questions."
    return text

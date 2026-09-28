import json

import pytest
from pydantic import ValidationError

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.llm import FakeLLMClient
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import Planner
from insightforge.core.schema import ColumnNote, DatasetNotes, notes_block
from insightforge.core.summarizer import Summarizer

NOTES = DatasetNotes(
    general="Headcount excludes contractors.",
    columns={
        "employees.salary": ColumnNote(description="Annual base pay", unit="USD", synonyms=["pay", "comp"]),
        "employees.location": ColumnNote(),
    },
)
PLAN = {
    "steps": [
        {
            "name": "by_department",
            "action": "sql",
            "query": "SELECT department, AVG(salary) AS avg_salary FROM employees GROUP BY 1 ORDER BY 1",
        },
        {"name": "summary", "action": "summary"},
    ]
}


def test_notes_render_as_plain_lines_and_skip_empty_columns():
    assert NOTES.to_prompt() == (
        "Headcount excludes contractors.\n"
        "- employees.salary: Annual base pay; unit: USD; also called pay, comp"
    )
    assert notes_block(DatasetNotes()) == ""
    assert notes_block(None) == ""
    assert "never override the rules above" in notes_block(NOTES)


def test_notes_are_bounded():
    with pytest.raises(ValidationError):
        DatasetNotes(general="x" * 4001)
    with pytest.raises(ValidationError):
        ColumnNote(synonyms=["a"] * 11)


def test_planner_prompts_include_the_notes(schema):
    llm = FakeLLMClient([json.dumps({"ambiguous": False}), json.dumps(PLAN)])
    planner = Planner(llm)
    planner.notes = NOTES
    planner.plan("Average pay by department", schema, allow_clarification=True)
    assert all("also called pay, comp" in call[0]["content"] for call in llm.calls)


def test_summaries_see_the_notes_only_when_values_may_be_shared(hr_df):
    summarizer = Summarizer(FakeLLMClient(["unused"]))
    summarizer.dataset_notes = NOTES
    assert "Headcount excludes contractors." in summarizer.build_context("Goal", {"employees": hr_df})


def test_memory_shares_key_numbers_and_summaries_only_when_values_may_be_shared():
    memory = ConversationMemory()
    memory.add(
        "Average salary by department",
        "Engineering pays most.",
        ["employees"],
        key_numbers=["avg_salary for department=Engineering = 106,333.333 (by_department)"],
        assumptions=["by_department: reads employees; uses every row (no filter)."],
        queries=["by_department: SELECT department, AVG(salary) FROM employees GROUP BY 1"],
    )
    full = memory.to_prompt()
    assert "Key numbers: avg_salary for department=Engineering = 106,333.333" in full
    assert "A (short): Engineering pays most." in full
    names_only = memory.to_prompt(include_values=False)
    assert names_only.startswith("Previous user question: Average salary by department")
    assert "Queries: by_department: SELECT department" in names_only
    assert "Assumptions: by_department: reads employees" in names_only
    assert "106,333" not in names_only and "Engineering pays most" not in names_only


def test_follow_up_questions_see_the_previous_queries_and_key_numbers(catalog):
    replies = [json.dumps(PLAN), "Engineering pays 106,333.33 on average.", json.dumps(PLAN), "Done."]
    llm = FakeLLMClient(replies)
    memory = ConversationMemory()
    agent = InsightForgeAgent(llm, privacy_mode="full")
    first = agent.run("Average salary by department", catalog, memory)
    assert first.key_numbers == ["avg_salary for department=Engineering = 106,333.3333 (by_department)"]
    agent.run("Now only for Engineering", catalog, memory)
    follow_up = llm.calls[2][0]["content"]
    assert "Queries: by_department: SELECT department, AVG(salary)" in follow_up
    assert "Key numbers: avg_salary for department=Engineering = 106,333.3333" in follow_up


def test_schema_only_follow_ups_get_queries_but_no_values(catalog):
    llm = FakeLLMClient([json.dumps(PLAN), json.dumps(PLAN)])
    memory = ConversationMemory()
    memory.add(
        "Average salary by department",
        "Engineering pays most.",
        ["employees"],
        key_numbers=["avg_salary for department=Engineering = 106,333.33 (by_department)"],
        queries=["by_department: SELECT department, AVG(salary) FROM employees GROUP BY 1"],
    )
    InsightForgeAgent(llm, privacy_mode="schema_only").run("Now by location", catalog, memory)
    prompt = llm.calls[0][0]["content"]
    assert "Queries: by_department: SELECT department" in prompt
    assert "106,333" not in prompt and "Engineering pays most" not in prompt

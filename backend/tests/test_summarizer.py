import json

from insightforge.core.llm import FakeLLMClient
from insightforge.core.summarizer import Summarizer


def test_context_contains_table_shape_headers_and_data(hr_df):
    context = Summarizer(FakeLLMClient(["unused"])).build_context("Review HR", {"employees": hr_df})
    assert "Table: employees" in context
    assert "Total rows: 60" in context
    assert "employee_id" in context
    assert "Engineering" in context


def test_context_marks_cut_off_tables(hr_df):
    note = "The query produced 900 rows; only the first 60 were kept."
    context = Summarizer(FakeLLMClient(["unused"])).build_context(
        "Review HR", {"employees": hr_df}, notes={"employees": note}
    )
    assert "Rows retrieved: 60" in context
    assert note in context
    assert "Total rows: 60" not in context


def test_local_summary_shows_the_cut_off_note(hr_df):
    text, response = Summarizer(FakeLLMClient(["unused"]), privacy_mode="local").summarize(
        "Review HR", {"employees": hr_df}, notes={"employees": "CUT_OFF_NOTE"}
    )
    assert response is None
    assert "CUT_OFF_NOTE" in text


def test_local_model_summary_is_structured(hr_df):
    reply = {
        "headline": "Engineering pays most",
        "findings": ["106333.33 average"],
        "actions": ["Review pay"],
    }
    llm = FakeLLMClient([json.dumps(reply)])
    text, response = Summarizer(llm, privacy_mode="local", local_llm=llm).summarize(
        "Review HR", {"employees": hr_df}
    )
    assert response is not None
    assert text == (
        "Engineering pays most\n\n### Key findings\n- 106333.33 average\n\n"
        "### Recommended actions\n- Review pay"
    )
    assert "Respond only with JSON" in llm.calls[0][0]["content"]


def test_local_model_summary_falls_back_to_tables_on_bad_output(hr_df):
    llm = FakeLLMClient(["We are given a table and must think about it first"])
    text, response = Summarizer(llm, privacy_mode="local", local_llm=llm).summarize(
        "Review HR", {"employees": hr_df}
    )
    assert response is None
    assert text.startswith("> **LLM unavailable** (LLMJSONError")
    assert "### employees" in text


def test_summarize_falls_back_when_llm_raises(hr_df):
    def fail(_messages):
        raise RuntimeError("boom")

    text, response = Summarizer(FakeLLMClient(fail)).summarize("Review HR", {"employees": hr_df})
    assert text.startswith("> **LLM unavailable** (RuntimeError: boom)")
    assert "## Analysis summary" in text
    assert "employees" in text
    assert response is None

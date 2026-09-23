from insightforge.core.llm import FakeLLMClient
from insightforge.core.summarizer import Summarizer


def test_context_contains_table_shape_headers_and_data(hr_df):
    context = Summarizer(FakeLLMClient(["unused"])).build_context("Review HR", {"employees": hr_df})
    assert "Table: employees" in context
    assert "Total rows: 60" in context
    assert "employee_id" in context
    assert "Engineering" in context


def test_summarize_falls_back_when_llm_raises(hr_df):
    def fail(_messages):
        raise RuntimeError("boom")

    text, response = Summarizer(FakeLLMClient(fail)).summarize("Review HR", {"employees": hr_df})
    assert text.startswith("> **LLM unavailable** (RuntimeError: boom)")
    assert "## Analysis summary" in text
    assert "employees" in text
    assert response is None

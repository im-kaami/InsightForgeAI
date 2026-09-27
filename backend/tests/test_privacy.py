import json

import pandas as pd

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.catalog import DataCatalog
from insightforge.core.llm import FakeLLMClient
from insightforge.core.memory import ConversationMemory
from insightforge.core.planner import Planner, SqlStep
from insightforge.core.privacy import PromptPolicy
from insightforge.core.schema import ColumnInfo, SchemaInfo, TableInfo
from insightforge.core.summarizer import Summarizer

SCHEMA = SchemaInfo(
    tables=[
        TableInfo(
            name="records",
            row_count=918273,
            columns=[
                ColumnInfo(
                    name="amount", dtype="INTEGER", sample_values=["SECRET_SAMPLE"], null_fraction=0.123
                )
            ],
        )
    ]
)


def test_schema_only_omits_values_counts_and_summary_memory():
    memory = ConversationMemory()
    memory.add("Prior question", "SECRET_SUMMARY", ["records"])
    llm = FakeLLMClient(
        [
            json.dumps(
                {
                    "steps": [
                        {
                            "name": "totals",
                            "action": "sql",
                            "query": "SELECT SUM(amount) AS total FROM records",
                        },
                    ]
                }
            )
        ]
    )
    planner = Planner(llm, privacy_mode="schema_only")
    planner.plan("Total amount?", SCHEMA, memory)
    outbound = str(llm.calls)
    assert "SECRET_SAMPLE" not in outbound
    assert "SECRET_SUMMARY" not in outbound
    assert "918273" not in outbound
    assert "0.123" not in outbound
    assert "Prior question" in outbound
    assert "amount" in outbound


def test_repair_does_not_send_data_bearing_exception():
    llm = FakeLLMClient(['{"query": "SELECT amount FROM records"}'])
    planner = Planner(llm, privacy_mode="schema_only")
    planner.repair_sql(SqlStep(name="values", query="SELECT amount FROM records"), "SECRET_BAD_CELL", SCHEMA)
    assert "SECRET_BAD_CELL" not in str(llm.calls)
    assert "SECRET_SAMPLE" not in str(llm.calls)


def test_local_mode_never_calls_configured_llm():
    def forbidden(_messages):
        raise AssertionError("An external model must not be called")

    configured = FakeLLMClient(forbidden)
    catalog = DataCatalog()
    try:
        catalog.register_df("records", pd.DataFrame({"category": ["PRIVATE_VALUE"], "amount": [17]}))
        result = InsightForgeAgent(configured, privacy_mode="local").run("PRIVATE_QUESTION", catalog)
        assert result.artifacts
        assert configured.calls == []
        assert "Local summary" in result.summary
    finally:
        catalog.close()


def test_schema_only_summary_keeps_rows_aggregates_and_memory_local():
    llm = FakeLLMClient(["must not be used"])
    memory = ConversationMemory()
    memory.add("Previous", "SECRET_MEMORY", ["records"])
    text, response = Summarizer(llm, privacy_mode="schema_only").summarize(
        "Totals", {"records": pd.DataFrame({"alias": ["SECRET_VALUE"], "amount": [918273]})}, memory=memory
    )
    assert response is None and llm.calls == []
    assert "Local summary" in text
    assert "918273" in text


def test_full_mode_is_explicitly_capable_of_result_sharing():
    llm = FakeLLMClient(["External summary"])
    text, _ = Summarizer(llm, privacy_mode="full").summarize(
        "Totals", {"records": pd.DataFrame({"value": [918273]})}
    )
    assert text == "External summary"
    assert "918273" in str(llm.calls)


def test_invalid_policy_fails_closed():
    import pytest

    with pytest.raises(ValueError):
        PromptPolicy("unknown")

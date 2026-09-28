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


def test_local_mode_uses_the_local_model_instead_of_the_configured_one():
    def forbidden(_messages):
        raise AssertionError("A cloud model must not be called")

    def local_reply(messages):
        if "data-analysis planner" in messages[0]["content"]:
            return json.dumps(
                {
                    "steps": [
                        {
                            "name": "totals",
                            "action": "sql",
                            "query": "SELECT category, SUM(amount) AS total FROM records GROUP BY 1",
                        },
                        {"name": "summary", "action": "summary"},
                    ]
                }
            )
        return json.dumps({"headline": "Local model summary", "findings": ["17 total"], "actions": []})

    configured = FakeLLMClient(forbidden)
    local = FakeLLMClient(local_reply)
    catalog = DataCatalog()
    try:
        catalog.register_df("records", pd.DataFrame({"category": ["PRIVATE_VALUE"], "amount": [17]}))
        result = InsightForgeAgent(configured, privacy_mode="local", local_llm=local).run(
            "PRIVATE_QUESTION", catalog
        )
    finally:
        catalog.close()
    assert configured.calls == []
    assert not result.used_fallback_plan
    assert result.summary == "Local model summary\n\n### Key findings\n- 17 total"
    assert "PRIVATE_VALUE" in str(local.calls)


def test_local_policy_shares_errors_and_results_only_with_a_local_model():
    offline = PromptPolicy("local")
    assert not offline.llm_summary_allowed
    assert offline.repair_error("SECRET_ERROR") != "SECRET_ERROR"
    local_client = FakeLLMClient(["local"])
    with_model = PromptPolicy("local", local_model=True)
    assert with_model.llm_summary_allowed
    assert with_model.repair_error("SECRET_ERROR") == "SECRET_ERROR"
    assert with_model.client(FakeLLMClient(["cloud"]), local_client) is local_client
    assert PromptPolicy("schema_only", local_model=True).repair_error("SECRET_ERROR") != "SECRET_ERROR"


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

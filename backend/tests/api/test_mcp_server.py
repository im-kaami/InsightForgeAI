import json

import pytest

pytest.importorskip("mcp")

from insightforge.mcp_server import InsightForgeTools, build_server  # noqa: E402

from .test_metrics_api import REVENUE, _with_relationship  # noqa: E402


async def _tools(client, auth_headers, scope="ask") -> InsightForgeTools:
    created = await client.post(
        "/api/auth/tokens", headers=auth_headers, json={"name": "mcp", "scope": scope}
    )
    client.headers["Authorization"] = f"Bearer {created.json()['token']}"
    return InsightForgeTools(client, poll_seconds=0.05, timeout_seconds=60)


async def _privacy(client, headers, dataset, mode):
    response = await client.patch(
        f"/api/datasets/{dataset['id']}/privacy", headers=headers, json={"mode": mode, "acknowledged": True}
    )
    assert response.status_code == 200, response.text


async def _shop(client, auth_headers):
    dataset = await _with_relationship(client, auth_headers)
    await client.put(
        f"/api/datasets/{dataset['id']}/metrics", headers=auth_headers, json={"metrics": [REVENUE]}
    )
    query = {
        "question": "What is revenue by region?",
        "sql": "SELECT region, SUM(amount) AS revenue FROM orders WHERE status = 'completed' GROUP BY 1",
        "approved": True,
    }
    await client.put(
        f"/api/datasets/{dataset['id']}/queries", headers=auth_headers, json={"queries": [query]}
    )
    return dataset


async def test_privacy_modes_decide_what_the_assistant_sees(client, auth_headers):
    dataset = await _shop(client, auth_headers)
    login = dict(auth_headers)
    tools = await _tools(client, auth_headers)

    await _privacy(client, login, dataset, "local")
    listed = await tools.list_datasets()
    assert listed == [
        {
            "id": dataset["id"],
            "name": dataset["name"],
            "privacy": "local",
            "shared_through_mcp": "nothing beyond the dataset's name",
            "tables": None,
        }
    ]
    assert "tables" not in await tools.describe_dataset(dataset["id"])

    await _privacy(client, login, dataset, "schema_only")
    described = await tools.describe_dataset(dataset["id"])
    text = json.dumps(described)
    assert {"name": "amount", "type": "DOUBLE"} in described["tables"][1]["columns"] or "amount" in text
    assert "samples" not in text and "completed" not in text and "SELECT" not in text
    assert described["approved_metrics"][0]["fixed_conditions"] == ["a fixed condition on status"]
    assert described["approved_questions"] == [{"question": "What is revenue by region?"}]
    with pytest.raises(Exception, match="privacy mode is 'schema_only'"):
        await tools.ask(dataset["id"], "What is revenue by region?")

    await _privacy(client, login, dataset, "full")
    described = await tools.describe_dataset(dataset["id"])
    assert described["approved_metrics"][0]["fixed_conditions"] == ["status equals completed"]
    assert described["approved_questions"][0]["sql"].startswith("SELECT region")
    assert any(column.get("samples") for table in described["tables"] for column in table["columns"])


async def test_asking_and_reports_return_checked_results_with_trust_labels(client, auth_headers):
    dataset = await _shop(client, auth_headers)
    await _privacy(client, auth_headers, dataset, "full")
    tools = await _tools(client, auth_headers)
    answer = await tools.ask(dataset["id"], "What is revenue by region?")
    assert answer["status"] == "completed", answer["error"]
    assert answer["tables"][0]["trust"] == "approved query: What is revenue by region?"
    assert answer["tables"][0]["columns"] == ["region", "revenue"]
    assert (await tools.get_run(answer["run_id"]))["run_id"] == answer["run_id"]
    report = await tools.metric_report(dataset["id"], "revenue", "2025-07-01", "2025-12-31", "region")
    assert report["status"] == "completed", report["error"]
    assert report["tables"][0]["trust"] == "approved metric: Revenue"
    assert report["tables"][0]["rows"][0]["region"] == "Total"


async def test_read_tokens_cannot_ask_and_the_server_lists_its_tools(client, auth_headers):
    dataset = await _shop(client, auth_headers)
    await _privacy(client, auth_headers, dataset, "full")
    tools = await _tools(client, auth_headers, scope="read")
    with pytest.raises(Exception, match="403"):
        await tools.ask(dataset["id"], "What is revenue by region?")
    server = build_server(lambda: tools)
    names = {tool.name for tool in await server.list_tools()}
    assert names == {"list_datasets", "describe_dataset", "ask", "metric_report", "get_run"}
    result = await server.call_tool("ask", {"dataset_id": dataset["id"], "question": "x"})
    assert "403" in json.dumps(
        [item.model_dump() for item in result[0]] if isinstance(result, tuple) else str(result)
    )

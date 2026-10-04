"""MCP server: use InsightForge from Claude Desktop, Cursor and other MCP clients (Phase 5b).

The server runs on the user's computer (``insightforge mcp``) and talks to the InsightForge API with a
personal API token, so ownership checks, token scopes and every existing rule apply unchanged. An MCP
client passes what it receives to its own AI model, so each dataset's privacy mode decides what the
tools may return:

- ``full``: everything, including sample values, approved SQL, answers and report numbers;
- ``schema_only``: table and column names and types, metric definitions without fixed values, and
  the approved questions; never data values, answers or numbers;
- ``local``: the dataset's name only; nothing about its contents leaves InsightForge.

Requires the optional ``mcp`` extra: ``pip install -e "backend[mcp]"``.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

MAX_ROWS = 50
SHARED = {
    "full": "everything (samples, answers and numbers)",
    "schema_only": "table and column names, types, metric definitions and approved questions only",
    "local": "nothing beyond the dataset's name",
}


class ToolError(RuntimeError):
    """A problem to report to the MCP client as a plain message."""


def _privacy(dataset: dict[str, Any]) -> str:
    return str(dataset.get("llm_policy") or "local")


class InsightForgeTools:
    """The tools, written against the InsightForge HTTP API. ``client`` carries the token."""

    def __init__(self, client: httpx.AsyncClient, poll_seconds: float = 1.0, timeout_seconds: float = 300):
        self.client = client
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds

    async def _get(self, path: str, **params: Any) -> Any:
        return self._json(await self.client.get(path, params=params or None))

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        return self._json(await self.client.post(path, json=body))

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text
            raise ToolError(f"InsightForge said {response.status_code}: {detail}")
        return response.json()

    async def _dataset(self, dataset_id: str) -> dict[str, Any]:
        return await self._get(f"/api/datasets/{dataset_id}")

    def _require_full(self, dataset: dict[str, Any], what: str) -> None:
        mode = _privacy(dataset)
        if mode != "full":
            raise ToolError(
                f"{what} is not available through MCP for {dataset['name']!r}: its privacy mode is "
                f"{mode!r}, which shares {SHARED.get(mode, 'nothing')}. Use the InsightForge app, or "
                "set the dataset's privacy to Full sharing if its data may go to your AI assistant."
            )

    async def list_datasets(self) -> list[dict[str, Any]]:
        """Your datasets, with their privacy mode and what MCP may share from each."""
        items = await self._get("/api/datasets")
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "privacy": _privacy(item),
                "shared_through_mcp": SHARED.get(_privacy(item), "nothing"),
                "tables": item.get("tables") if _privacy(item) != "local" else None,
            }
            for item in items
        ]

    async def describe_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Tables, columns, approved metrics and approved questions, as far as the privacy mode allows."""
        dataset = await self._dataset(dataset_id)
        mode = _privacy(dataset)
        out: dict[str, Any] = {"id": dataset["id"], "name": dataset["name"], "privacy": mode}
        if mode == "local":
            out["note"] = "This dataset is local only; its contents are not shared through MCP."
            return out
        full = mode == "full"
        out["tables"] = [
            {
                "name": table["name"],
                "rows": table.get("row_count") if full else None,
                "columns": [
                    {
                        "name": column["name"],
                        "type": column["dtype"],
                        **({"samples": column.get("sample_values") or []} if full else {}),
                    }
                    for column in table.get("columns") or []
                ],
            }
            for table in (dataset.get("schema") or {}).get("tables") or []
        ]
        metrics = (dataset.get("metrics") or {}).get("metrics") or []
        out["approved_metrics"] = [
            {
                "name": item["name"],
                "label": item.get("label") or item["name"],
                "description": item.get("description") or "",
                "calculation": f"{item['aggregation']}({item.get('column') or '*'}) of {item['table']}",
                "fixed_conditions": [
                    (
                        f"{condition['column']} {condition.get('op', 'equals')} {condition['value']}"
                        if full
                        else f"a fixed condition on {condition['column']}"
                    )
                    for condition in item.get("filters") or []
                ],
                "date_column": item.get("date_column"),
                "group_or_filter_by": item.get("dimensions") or [],
                "unit": item.get("unit") or "",
            }
            for item in metrics
            if item.get("approved")
        ]
        queries = (dataset.get("queries") or {}).get("queries") or []
        out["approved_questions"] = [
            {"question": item["question"], **({"sql": item["sql"]} if full else {})}
            for item in queries
            if item.get("approved")
        ]
        out["approved_data_only"] = bool((dataset.get("queries") or {}).get("approved_only"))
        if full and (dataset.get("notes") or {}).get("general"):
            out["notes"] = dataset["notes"]["general"]
        return out

    async def _wait(self, run_id: str) -> dict[str, Any]:
        started = time.monotonic()
        while True:
            run = await self._get(f"/api/runs/{run_id}")
            if run["status"] in {"completed", "failed"}:
                return run
            if time.monotonic() - started > self.timeout_seconds:
                raise ToolError(f"Run {run_id} is still {run['status']}; ask get_run later")
            await asyncio.sleep(self.poll_seconds)

    @staticmethod
    def _answer(run: dict[str, Any]) -> dict[str, Any]:
        provenance = run.get("provenance") or {}
        tables = []
        for item in run.get("artifacts") or []:
            if item.get("type") != "table":
                continue
            label = None
            if item.get("metric"):
                label = f"approved metric: {item['metric'].get('label')}"
            elif item.get("approved_query"):
                label = f"approved query: {item['approved_query'].get('question')}"
            tables.append(
                {
                    "name": item.get("name"),
                    "trust": label or "AI-written SQL",
                    "sql": item.get("sql"),
                    "columns": item.get("columns"),
                    "rows": (item.get("rows") or [])[:MAX_ROWS],
                    "total_rows": item.get("total_rows"),
                }
            )
        return {
            "run_id": run["id"],
            "status": run["status"],
            "verification": run.get("verification_status"),
            "summary": run.get("summary"),
            "error": run.get("error"),
            "warnings": run.get("warnings") or [],
            "assumptions": provenance.get("assumptions") or [],
            "tables": tables,
            "clarifying_question": provenance.get("clarification"),
            "refused": provenance.get("refusal"),
        }

    async def ask(self, dataset_id: str, question: str) -> dict[str, Any]:
        """Ask a question about a Full-sharing dataset and wait for the checked answer."""
        dataset = await self._dataset(dataset_id)
        self._require_full(dataset, "Asking questions")
        session = await self._post("/api/sessions", {"dataset_id": dataset_id, "title": question[:80]})
        run = await self._post(f"/api/sessions/{session['id']}/runs", {"goal": question})
        return self._answer(await self._wait(run["id"]))

    async def metric_report(
        self, dataset_id: str, metric: str, start_date: str, end_date: str, group_by: str | None = None
    ) -> dict[str, Any]:
        """A checked report for an approved metric over a period, compared with the period before."""
        dataset = await self._dataset(dataset_id)
        self._require_full(dataset, "Metric reports")
        body = {"metric": metric, "start_date": start_date, "end_date": end_date, "group_by": group_by}
        run = await self._post(f"/api/datasets/{dataset_id}/reports/metric", body)
        return self._answer(await self._wait(run["id"]))

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """An earlier answer or report, if its dataset is Full sharing."""
        run = await self._get(f"/api/runs/{run_id}")
        session = await self._get(f"/api/sessions/{run['session_id']}", include_runs="false")
        self._require_full(await self._dataset(session["dataset_id"]), "Answers")
        return self._answer(run)


def build_server(tools_factory: Callable[[], InsightForgeTools]):
    """Register the tools on a FastMCP server. Each call gets the same tool object."""
    from mcp.server.fastmcp import FastMCP

    tools = tools_factory()
    server = FastMCP(
        "InsightForge",
        instructions=(
            "Ask questions about the user's InsightForge datasets. Start with list_datasets, then "
            "describe_dataset. Numbers come from tested code and approved definitions; quote them as "
            "given and mention the trust label (approved metric, approved query or AI-written SQL)."
        ),
    )

    async def call(method: Callable[..., Any], *args: Any) -> Any:
        try:
            return await method(*args)
        except ToolError as error:
            return {"error": str(error)}

    @server.tool()
    async def list_datasets() -> Any:
        """List your InsightForge datasets and what each privacy mode lets this assistant see."""
        found = await call(tools.list_datasets)
        return found if isinstance(found, dict) else {"datasets": found}

    @server.tool()
    async def describe_dataset(dataset_id: str) -> Any:
        """Tables, columns, approved metrics and approved questions of one dataset."""
        return await call(tools.describe_dataset, dataset_id)

    @server.tool()
    async def ask(dataset_id: str, question: str) -> Any:
        """Ask a business question; returns the summary, result tables, trust labels and assumptions."""
        return await call(tools.ask, dataset_id, question)

    @server.tool()
    async def metric_report(
        dataset_id: str, metric: str, start_date: str, end_date: str, group_by: str | None = None
    ) -> Any:
        """Checked report for an approved metric (dates YYYY-MM-DD, inclusive) against the previous period."""
        return await call(tools.metric_report, dataset_id, metric, start_date, end_date, group_by)

    @server.tool()
    async def get_run(run_id: str) -> Any:
        """Fetch an earlier answer or report by its run id."""
        return await call(tools.get_run, run_id)

    return server


def run(url: str, token: str) -> None:
    """Start the MCP server on stdio (what Claude Desktop and Cursor expect)."""
    if not token.startswith("ifk_"):
        raise SystemExit("Set INSIGHTFORGE_TOKEN to an API token (ifk_...) from the API tokens page")
    client = httpx.AsyncClient(
        base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=60
    )
    build_server(lambda: InsightForgeTools(client)).run()


def run_from_environment(url: str | None = None) -> None:
    run(
        url or os.environ.get("INSIGHTFORGE_URL", "http://localhost:8000"),
        os.environ.get("INSIGHTFORGE_TOKEN", ""),
    )

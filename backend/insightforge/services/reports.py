import base64
import html
import json
import re
import uuid
from pathlib import Path

from insightforge.core.executor import truncation_note
from insightforge.core.stats import format_p
from insightforge.db.models import Artifact, Run


class ReportFormatUnavailable(RuntimeError):
    pass


def _evidence_line(item: dict) -> str:
    if item.get("row") is not None:
        where = f"row {item.get('row')}, column {item.get('column')}"
    elif item.get("kind") in {"column_total", "column_average"}:
        label = "total" if item.get("kind") == "column_total" else "average"
        where = f"{label} of column {item.get('column')}"
    else:
        where = "row count"
    said = f" (summary says {item['text']})" if item.get("text") else ""
    return f"{item.get('id')}: {item.get('value')}{said} ({item.get('artifact')}, {where})"


def _assumptions(run: Run) -> list[str]:
    return [str(item) for item in (run.provenance_json or {}).get("assumptions") or []]


def _truncation(payload: dict) -> str | None:
    if not payload.get("truncated"):
        return None
    return truncation_note(int(payload.get("total_rows", 0)), payload.get("full_row_count"))


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _code_block(value: str, language: str) -> str:
    longest = max((len(match.group()) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{value}\n{fence}"


def _table(payload: dict, limit: int = 50) -> str:
    columns = payload.get("columns", [])
    rows = payload.get("rows", [])[:limit]
    if not columns:
        return "(no columns)"
    lines = [
        "| " + " | ".join(_safe(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines += [
        "| "
        + " | ".join(_safe(row.get(column)).replace("|", "\\|") for column in columns)
        + " |"
        for row in rows
    ]
    if payload.get("total_rows", 0) > limit:
        lines.append(f"\n_Showing {limit} of {payload['total_rows']} rows._")
    if note := _truncation(payload):
        lines.append(f"\n_{_safe(note)}_")
    return "\n".join(lines)


def render_markdown(run: Run, artifacts: list[Artifact]) -> str:
    tokens = sum((run.token_usage_json or {}).values())
    lines = [
        f"# {_safe(run.goal)}",
        f"Dataset run · {run.created_at.isoformat()} · {tokens} tokens",
        "## Summary",
        _safe(run.summary or ""),
        "## Verification",
        f"Status: `{_safe(run.verification_status)}`",
        *[f"- {_safe(warning)}" for warning in (run.warnings_json or [])],
        *(
            ["## Assumptions", *[f"- {_safe(item)}" for item in _assumptions(run)]]
            if _assumptions(run)
            else []
        ),
        "## Results",
    ]
    for artifact in artifacts:
        payload = artifact.payload_json
        if artifact.type == "table":
            lines.extend([f"### {_safe(artifact.name)}", _table(payload)])
            if payload.get("sql"):
                lines.append(_code_block(str(payload["sql"]), "sql"))
        elif artifact.type == "plot":
            path = payload.get("png_path")
            lines.extend(
                [
                    f"### {_safe(payload.get('title') or artifact.name)}",
                    f"![{_safe(payload.get('title') or artifact.name)}]({Path(path).name})"
                    if path and Path(path).exists()
                    else "(interactive chart in app)",
                ]
            )
            if payload.get("note"):
                lines.append(f"_{_safe(payload['note'])}_")
        elif artifact.type == "stat":
            title, *details = _stat_lines(artifact.name, payload)
            lines.extend([f"### {_safe(title)}", *[f"- {_safe(line)}" for line in details]])
        elif artifact.type == "code":
            lines.extend(
                [
                    f"### {_safe(artifact.name)} (free-form code, sandboxed)",
                    _code_block(str(payload.get("code", "")), "python"),
                ]
            )
            if payload.get("error"):
                lines.append(_code_block(str(payload["error"]), "text"))
            if payload.get("columns"):
                lines.append(_table(payload))
            elif payload.get("value") is not None:
                lines.append(f"Result: {_safe(payload['value'])}")
    lines.extend(
        [
            "## Provenance",
            _code_block(json.dumps(run.provenance_json or {}, indent=2, default=str), "json"),
            "## Evidence",
        ]
    )
    for item in (run.provenance_json or {}).get("evidence", []):
        lines.append(f"- {_safe(_evidence_line(item))}")
    lines.append("## Plan")
    for step in (run.plan_json or {}).get("steps", []):
        lines.append(f"- **{_safe(step['name'])}** ({_safe(step['action'])})")
        if step.get("query"):
            lines.append(_code_block(str(step["query"]), "sql"))
    return "\n\n".join(lines)


def _cell(kind: str, source: str, outputs: list[dict] | None = None) -> dict:
    cell: dict = {
        "cell_type": kind,
        "id": uuid.uuid4().hex[:12],
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if kind == "code":
        cell |= {"execution_count": None, "outputs": outputs or []}
    return cell


def _text_output(text: str) -> list[dict]:
    return [{"output_type": "stream", "name": "stdout", "text": text.splitlines(keepends=True)}]


def _identifier(name: str) -> str:
    cleaned = re.sub(r"\W+", "_", name).strip("_") or "result"
    return f"t_{cleaned}" if cleaned[0].isdigit() else cleaned


def render_ipynb(run: Run, artifacts: list[Artifact]) -> str:
    provenance = run.provenance_json or {}
    cells = [
        _cell(
            "markdown",
            f"# {run.goal}\n\nExported from InsightForge on {run.created_at.date().isoformat()}. "
            f"Model: {provenance.get('model', 'unknown')}. Status: {run.verification_status}.\n\n"
            "Each step below is one cell, in the order it ran. Outputs are the saved results; load your "
            "data into DuckDB in the first cell to rerun them.",
        ),
        _cell(
            "code",
            "import duckdb\nimport pandas as pd\n\ncon = duckdb.connect()\n"
            "# Load the dataset's tables before rerunning, for example:\n"
            "# con.execute(\"CREATE TABLE orders AS SELECT * FROM 'orders.csv'\")",
        ),
    ]
    for artifact in artifacts:
        payload = artifact.payload_json
        name = _identifier(artifact.name)
        if artifact.type == "table":
            preview = {**payload, "rows": payload.get("rows", [])[:20]}
            cells.append(_cell("markdown", f"## {artifact.name}\nSQL checked by the read-only guard."))
            cells.append(
                _cell(
                    "code",
                    f"{name} = con.sql({str(payload.get('sql', ''))!r}).df()\n{name}.head(20)",
                    _text_output(_table(preview)),
                )
            )
        elif artifact.type == "stat":
            title, *details = _stat_lines(artifact.name, payload)
            cells.append(_cell("markdown", f"## {title}\n\n" + "\n".join(f"- {line}" for line in details)))
            arguments = ", ".join(
                f"{key}={payload[key]!r}"
                for key in ("by", "controls", "features", "grain", "horizon")
                if payload.get(key)
            )
            source = _identifier(str(payload.get("data_source", "rows")))
            call = (
                f"from insightforge.core.stats import run_test\n\n{name} = run_test({artifact.name!r}, "
                f"{payload.get('method')!r}, {payload.get('data_source')!r}, {payload.get('x')!r}, "
                f"{payload.get('y')!r}, {source}{', ' + arguments if arguments else ''})\n"
                f"print({name}.interpretation)"
            )
            cells.append(_cell("code", call, _text_output(str(payload.get("interpretation", "")))))
        elif artifact.type == "code":
            source = _identifier(str(payload.get("data_source", "rows")))
            cells.append(
                _cell(
                    "markdown",
                    f"## {artifact.name}\nFree-form code written by the AI and run in the sandbox. Check it.",
                )
            )
            output = payload.get("error") or (
                _table(payload) if payload.get("columns") else f"{payload.get('value')}"
            )
            cells.append(
                _cell(
                    "code",
                    f"df = {source}.copy()\n{payload.get('code', '')}\nresult",
                    _text_output(str(output)),
                )
            )
        elif artifact.type == "plot":
            cells.append(_cell("markdown", f"*Chart: {payload.get('title') or artifact.name}*"))
        elif artifact.type == "text":
            cells.append(_cell("markdown", f"## Summary\n\n{payload.get('text', '')}"))
    assumptions = _assumptions(run)
    if assumptions or run.warnings_json:
        notes = [f"- Warning: {item}" for item in run.warnings_json or []]
        notes += [f"- {item}" for item in assumptions]
        cells.append(_cell("markdown", "## Assumptions and warnings\n\n" + "\n".join(notes)))
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "insightforge": {"run_id": run.id, "privacy_mode": provenance.get("privacy_mode")},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook, indent=1, default=str)


def _stat_lines(name: str, payload: dict) -> list[str]:
    numbers = [f"n = {payload.get('n')}"]
    if payload.get("p_value") is not None:
        numbers.insert(0, f"p {format_p(float(payload['p_value']))}")
    elif payload.get("statistic") is not None:
        numbers.insert(0, f"change = {float(payload['statistic']):,.4g}")
    if payload.get("p_adjusted") is not None:
        numbers.append(f"adjusted p {format_p(float(payload['p_adjusted']))}")
    if effect := payload.get("effect_size"):
        numbers.append(f"{effect['name']} = {effect['value']:.3g} ({effect['magnitude']})")
    if interval := payload.get("interval"):
        numbers.append(f"95% CI for {interval['label']}: {interval['low']:.4g} to {interval['high']:.4g}")
    return [
        f"{name}: {payload.get('test')} (tested method)",
        str(payload.get("interpretation", "")),
        "; ".join(numbers),
        *[f"Check: {line}" for line in payload.get("checks", [])],
        *[f"Caution: {line}" for line in payload.get("cautions", [])],
        *([str(payload["note"])] if payload.get("note") else []),
    ]


def _html_table(payload: dict, limit: int = 50) -> str:
    columns = payload.get("columns", [])
    rows = payload.get("rows", [])[:limit]
    if not columns:
        return "<p>(no columns)</p>"
    head = "".join(f"<th>{_safe(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_safe(row.get(column))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    note = ""
    if payload.get("total_rows", 0) > limit:
        note = f"<p>Showing {limit} of {int(payload['total_rows'])} rows.</p>"
    if truncated := _truncation(payload):
        note += f"<p>{_safe(truncated)}</p>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{note}"


def render_html(run: Run, artifacts: list[Artifact]) -> str:
    sections = [
        f"<h1>{_safe(run.goal)}</h1>",
        f"<p>Dataset run · {_safe(run.created_at.isoformat())}</p>",
        "<h2>Summary</h2>",
        f"<pre class='summary'>{_safe(run.summary or '')}</pre>",
        "<h2>Verification</h2>",
        f"<p>Status: {_safe(run.verification_status)}</p>",
        "<ul>"
        + "".join(f"<li>{_safe(warning)}</li>" for warning in (run.warnings_json or []))
        + "</ul>",
        (
            "<h2>Assumptions</h2><ul>"
            + "".join(f"<li>{_safe(item)}</li>" for item in _assumptions(run))
            + "</ul>"
            if _assumptions(run)
            else ""
        ),
        "<h2>Results</h2>",
    ]
    for artifact in artifacts:
        payload = artifact.payload_json
        if artifact.type == "table":
            sections.extend([f"<h3>{_safe(artifact.name)}</h3>", _html_table(payload)])
            if payload.get("sql"):
                sections.append(f"<pre><code>{_safe(payload['sql'])}</code></pre>")
        elif artifact.type == "plot":
            title = _safe(payload.get("title") or artifact.name)
            sections.append(f"<h3>{title}</h3>")
            path = payload.get("png_path")
            if path and Path(path).is_file():
                encoded = base64.b64encode(Path(path).read_bytes()).decode()
                sections.append(f"<img alt='{title}' src='data:image/png;base64,{encoded}'>")
            else:
                sections.append("<p>(interactive chart in app)</p>")
            if payload.get("note"):
                sections.append(f"<p>{_safe(payload['note'])}</p>")
        elif artifact.type == "stat":
            title, *details = _stat_lines(artifact.name, payload)
            sections.append(
                f"<h3>{_safe(title)}</h3><ul>"
                + "".join(f"<li>{_safe(line)}</li>" for line in details)
                + "</ul>"
            )
        elif artifact.type == "code":
            sections.append(f"<h3>{_safe(artifact.name)} (free-form code, sandboxed)</h3>")
            sections.append(f"<pre><code>{_safe(payload.get('code', ''))}</code></pre>")
            if payload.get("error"):
                sections.append(f"<pre>{_safe(payload['error'])}</pre>")
            if payload.get("columns"):
                sections.append(_html_table(payload))
            elif payload.get("value") is not None:
                sections.append(f"<p>Result: {_safe(payload['value'])}</p>")
    sections.extend(
        [
            "<h2>Provenance</h2>",
            "<pre><code>"
            + _safe(json.dumps(run.provenance_json or {}, indent=2, default=str))
            + "</code></pre>",
            "<h2>Evidence</h2><ul>",
        ]
    )
    sections.extend(
        f"<li>{_safe(_evidence_line(item))}</li>"
        for item in (run.provenance_json or {}).get("evidence", [])
    )
    sections.append("</ul><h2>Plan</h2>")
    for step in (run.plan_json or {}).get("steps", []):
        sections.append(f"<p>{_safe(step['name'])} ({_safe(step['action'])})</p>")
        if step.get("query"):
            sections.append(f"<pre><code>{_safe(step['query'])}</code></pre>")
    css = (
        "body{font-family:Arial;max-width:960px;margin:auto}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px}"
        "img{max-width:100%}.summary{white-space:pre-wrap}"
    )
    return f"<!doctype html><html><head><style>{css}</style></head><body>{''.join(sections)}</body></html>"


def render_pdf(html: str) -> bytes:
    try:
        from io import BytesIO

        from xhtml2pdf import pisa
    except ImportError as error:
        raise ReportFormatUnavailable("PDF support is not installed") from error
    output = BytesIO()
    result = pisa.CreatePDF(html, dest=output)
    if result.err:
        raise ReportFormatUnavailable("PDF rendering failed")
    return output.getvalue()

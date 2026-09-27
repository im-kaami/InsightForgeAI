import base64
import html
import json
import re
from pathlib import Path

from insightforge.db.models import Artifact, Run


class ReportFormatUnavailable(RuntimeError):
    pass


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
    lines.extend(
        [
            "## Provenance",
            _code_block(json.dumps(run.provenance_json or {}, indent=2, default=str), "json"),
            "## Evidence",
        ]
    )
    for item in (run.provenance_json or {}).get("evidence", []):
        lines.append(
            f"- `{_safe(item.get('id'))}`: {_safe(item.get('value'))} "
            f"({_safe(item.get('artifact'))}, row {_safe(item.get('row'))}, "
            f"column {_safe(item.get('column'))})"
        )
    lines.append("## Plan")
    for step in (run.plan_json or {}).get("steps", []):
        lines.append(f"- **{_safe(step['name'])}** ({_safe(step['action'])})")
        if step.get("query"):
            lines.append(_code_block(str(step["query"]), "sql"))
    return "\n\n".join(lines)


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
        f"<li>{_safe(item.get('id'))}: {_safe(item.get('value'))}</li>"
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

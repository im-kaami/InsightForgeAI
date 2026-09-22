import base64
from pathlib import Path

import markdown

from insightforge.db.models import Artifact, Run


class ReportFormatUnavailable(RuntimeError):
    pass


def _table(payload: dict, limit: int = 50) -> str:
    columns = payload.get("columns", [])
    rows = payload.get("rows", [])[:limit]
    if not columns:
        return "(no columns)"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines += [
        "| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    if payload.get("total_rows", 0) > limit:
        lines.append(f"\n_Showing {limit} of {payload['total_rows']} rows._")
    return "\n".join(lines)


def render_markdown(run: Run, artifacts: list[Artifact]) -> str:
    tokens = sum((run.token_usage_json or {}).values())
    lines = [
        f"# {run.goal}",
        f"Dataset run · {run.created_at.isoformat()} · {tokens} tokens",
        "## Summary",
        run.summary or "",
        "## Results",
    ]
    for artifact in artifacts:
        payload = artifact.payload_json
        if artifact.type == "table":
            lines.extend([f"### {artifact.name}", _table(payload)])
        elif artifact.type == "plot":
            path = payload.get("png_path")
            lines.extend(
                [
                    f"### {payload.get('title') or artifact.name}",
                    f"![{payload.get('title') or artifact.name}]({Path(path).name})"
                    if path and Path(path).exists()
                    else "(interactive chart in app)",
                ]
            )
    lines.append("## Plan")
    for step in (run.plan_json or {}).get("steps", []):
        lines.append(f"- **{step['name']}** ({step['action']})")
        if step.get("query"):
            lines.append(f"```sql\n{step['query']}\n```")
    return "\n\n".join(lines)


def render_html(run: Run, artifacts: list[Artifact]) -> str:
    text = render_markdown(run, artifacts)
    html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    for artifact in artifacts:
        path = artifact.payload_json.get("png_path")
        if path and Path(path).exists():
            encoded = base64.b64encode(Path(path).read_bytes()).decode()
            html = html.replace(Path(path).name, f"data:image/png;base64,{encoded}")
    css = (
        "body{font-family:Arial;max-width:960px;margin:auto}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px}"
        "img{max-width:100%}"
    )
    return f"<!doctype html><html><head><style>{css}</style></head><body>{html}</body></html>"


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

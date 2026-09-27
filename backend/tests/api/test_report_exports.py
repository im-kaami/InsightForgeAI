import html
import re
from datetime import UTC, datetime

from insightforge.db.models import Artifact, Run
from insightforge.services.reports import render_html, render_markdown


def test_report_exports_preserve_sql_and_escape_active_content():
    sql = "SELECT '<script>' AS value, '```' AS ticks"
    run = Run(
        id="run",
        session_id="session",
        owner_id="owner",
        goal="<script>alert(1)</script>",
        status="completed",
        summary="[link](javascript:alert(1))\n<script>alert(2)</script>",
        plan_json={"steps": [{"name": "query", "action": "sql", "query": sql}]},
        timings_json={},
        token_usage_json={},
        provenance_json={"evidence": []},
        verification_status="checks_passed",
        warnings_json=[],
        created_at=datetime.now(UTC),
    )
    artifact = Artifact(
        run_id=run.id,
        position=0,
        type="table",
        name="<script>table</script>",
        payload_json={
            "columns": ["value"],
            "rows": [{"value": "[link](javascript:alert(3))<script>x</script>"}],
            "total_rows": 1,
            "sql": sql,
        },
    )
    markdown = render_markdown(run, [artifact])
    assert sql in markdown
    assert "&#x27;" not in markdown
    rendered = render_html(run, [artifact])
    assert "<script>" not in rendered
    active = re.sub(r"<pre.*?</pre>", "", rendered, flags=re.DOTALL)
    assert "<a " not in active and "href=" not in active
    codes = re.findall(r"<pre><code>(.*?)</code></pre>", rendered, re.DOTALL)
    assert html.unescape(codes[0]) == sql
    assert "<table>" in rendered

import httpx
import respx

from .test_verified_reports import SALES_CSV

URL = "https://example.com/sales.csv"


def _changed() -> bytes:
    return SALES_CSV.replace(b"c3,o4,2026-09-03,300,30,180", b"c3,o4,2026-09-03,600,30,180")


async def test_url_refresh_stages_before_activation_and_failure_preserves_current(
    client, auth_headers
):
    with respx.mock:
        respx.get(URL).mock(
            side_effect=[
                httpx.Response(200, content=SALES_CSV, headers={"Content-Type": "text/csv"}),
                httpx.Response(200, content=_changed(), headers={"Content-Type": "text/csv"}),
            ]
        )
        created = await client.post(
            "/api/datasets/from-url",
            headers=auth_headers,
            json={"url": URL, "name": "URL sales"},
        )
        dataset = created.json()
        original = dataset["current_version_id"]
        refreshed = await client.post(
            f"/api/datasets/{dataset['id']}/refresh", headers=auth_headers
        )
    assert refreshed.status_code == 200, refreshed.text
    draft = refreshed.json()["review_version_id"]
    old_preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        headers=auth_headers,
        params={"table": "sales"},
    )
    assert next(row for row in old_preview.json()["rows"] if row["sale_id"] == "c3")[
        "revenue"
    ] == 300
    confirmed = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{draft}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": original},
    )
    assert confirmed.status_code == 200
    new_preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        headers=auth_headers,
        params={"table": "sales"},
    )
    assert next(row for row in new_preview.json()["rows"] if row["sale_id"] == "c3")[
        "revenue"
    ] == 600
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(500))
        failed = await client.post(
            f"/api/datasets/{dataset['id']}/refresh", headers=auth_headers
        )
    assert failed.status_code == 422
    unchanged = await client.get(f"/api/datasets/{dataset['id']}", headers=auth_headers)
    assert unchanged.json()["current_version_id"] == draft

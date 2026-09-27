import json

from .test_verified_reports import SALES_CSV


def _changed_revenue(value: int) -> bytes:
    return SALES_CSV.replace(b"c3,o4,2026-09-03,300,30,180", f"c3,o4,2026-09-03,{value},30,180".encode())


async def test_draft_preview_serializes_missing_values_as_null(client, auth_headers):
    uploaded = await client.post(
        "/api/datasets/upload",
        headers=auth_headers,
        data={"review": "true"},
        files=[("files", ("values.csv", b"id,value\n1,1.25\n2,\n", "text/csv"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    dataset = uploaded.json()
    preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        headers=auth_headers,
        params={"table": "values", "version_id": dataset["review_version_id"]},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"][1]["value"] is None


async def test_replacement_versions_preserve_history_and_reject_stale_confirm(
    client, auth_headers
):
    initial = await client.post(
        "/api/datasets/upload",
        headers=auth_headers,
        files=[("files", ("sales.csv", SALES_CSV, "text/csv"))],
    )
    dataset = initial.json()
    original_version = dataset["current_version_id"]
    first = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        files=[("files", ("sales.csv", _changed_revenue(600), "text/csv"))],
    )
    second = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        files=[("files", ("sales.csv", _changed_revenue(700), "text/csv"))],
    )
    assert first.status_code == 201 and second.status_code == 201
    old_preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        headers=auth_headers,
        params={"table": "sales", "version_id": original_version},
    )
    old_c3 = next(row for row in old_preview.json()["rows"] if row["sale_id"] == "c3")
    assert old_c3["revenue"] == 300
    confirmed = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{first.json()['id']}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": original_version},
    )
    assert confirmed.status_code == 200
    stale = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{second.json()['id']}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": original_version},
    )
    assert stale.status_code == 409
    current_preview = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        headers=auth_headers,
        params={"table": "sales"},
    )
    current_c3 = next(row for row in current_preview.json()["rows"] if row["sale_id"] == "c3")
    assert current_c3["revenue"] == 600


async def test_legacy_snapshot_preserves_originals_without_absolute_paths(
    client, auth_headers
):
    from insightforge.config import get_settings
    from insightforge.core.catalog import DataCatalog
    from insightforge.db.models import Dataset
    from insightforge.db.session import SessionLocal
    from insightforge.services.storage import Storage

    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    db = SessionLocal()
    try:
        dataset = Dataset(
            owner_id=user["id"],
            name="Legacy",
            kind="files",
            sources_json=[],
            schema_json={"tables": []},
            tables_json=["sales"],
        )
        db.add(dataset)
        db.flush()
        dataset_id = dataset.id
        directory = Storage(get_settings().storage_dir).dataset_dir(user["id"], dataset_id)
        original = directory / "uploads" / "sales.csv"
        original.write_bytes(SALES_CSV)
        downloaded = directory / "downloads" / "source.csv"
        downloaded.write_bytes(SALES_CSV)
        dataset.sources_json = [
            {
                "kind": "csv",
                "location": str(original.resolve()),
                "name": "sales",
                "options": {"dest_dir": str(directory), "preserve_rows": True},
            }
        ]
        catalog = DataCatalog(directory / "catalog.duckdb")
        catalog.create_table_from_query("sales", f"SELECT * FROM read_csv_auto('{original.as_posix()}')")
        catalog.close()
        db.commit()
    finally:
        db.close()
    preview = await client.get(
        f"/api/datasets/{dataset_id}/preview",
        headers=auth_headers,
        params={"table": "sales"},
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["rows"]) == 5
    fetched = await client.get(f"/api/datasets/{dataset_id}", headers=auth_headers)
    assert fetched.json()["current_version_id"]
    versions = await client.get(f"/api/datasets/{dataset_id}/versions", headers=auth_headers)
    assert versions.status_code == 200, versions.text
    source = versions.json()[0]["sources"][0]
    assert source["location"] == "sales.csv"
    assert "dest_dir" not in source["options"]
    assert source["sha256"] and source["size_bytes"] == len(SALES_CSV)
    version_id = versions.json()[0]["id"]
    version_dir = (
        get_settings().storage_dir
        / "users"
        / user["id"]
        / "datasets"
        / dataset_id
        / "versions"
        / version_id
    )
    assert (version_dir / "uploads" / "sales.csv").is_file()
    assert (version_dir / "downloads" / "source.csv").is_file()


async def test_failed_multi_file_stage_removes_unreferenced_version(
    client, auth_headers
):
    from insightforge.config import get_settings

    initial = await client.post(
        "/api/datasets/upload",
        headers=auth_headers,
        files=[("files", ("sales.csv", SALES_CSV, "text/csv"))],
    )
    dataset = initial.json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    version_root = (
        get_settings().storage_dir
        / "users"
        / user["id"]
        / "datasets"
        / dataset["id"]
        / "versions"
    )
    before_dirs = {path.name for path in version_root.iterdir() if path.is_dir()}
    options = json.dumps([{"table_name": "same"}, {"table_name": "same"}])
    failed = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        data={"options_json": options},
        files=[
            ("files", ("one.csv", SALES_CSV, "text/csv")),
            ("files", ("two.csv", SALES_CSV, "text/csv")),
        ],
    )
    assert failed.status_code == 400
    after_dirs = {path.name for path in version_root.iterdir() if path.is_dir()}
    assert after_dirs == before_dirs
    versions = await client.get(
        f"/api/datasets/{dataset['id']}/versions", headers=auth_headers
    )
    assert [item["id"] for item in versions.json()] == [dataset["current_version_id"]]
    unchanged = await client.get(
        f"/api/datasets/{dataset['id']}/preview",
        headers=auth_headers,
        params={"table": "sales"},
    )
    current = next(row for row in unchanged.json()["rows"] if row["sale_id"] == "c3")
    assert current["revenue"] == 300

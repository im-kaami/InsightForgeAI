from .test_verified_reports import _approved_dataset, _report

STAFF_CSV = b"""employee_id,work_mode,salary
E1, remote,100
E2,Remote,200
E3,office,300
E3,office,300
"""
RECIPE = {
    "steps": [
        {"kind": "drop_duplicates", "table": "staff"},
        {"kind": "clean_text", "table": "staff", "column": "work_mode", "case": "lower"},
        {
            "kind": "map_values",
            "table": "staff",
            "column": "work_mode",
            "mapping": [{"from_value": "remote", "to_value": "Remote"}],
        },
    ]
}


async def _upload(client, headers, content=STAFF_CSV, review="false"):
    response = await client.post(
        "/api/datasets/upload",
        headers=headers,
        data={"review": review},
        files=[("files", ("staff.csv", content, "text/csv"))],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _rows(client, headers, dataset_id, version_id=None):
    params = {"table": "staff"}
    if version_id:
        params["version_id"] = version_id
    response = await client.get(f"/api/datasets/{dataset_id}/preview", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["rows"]


async def _versions(client, headers, dataset_id):
    return (await client.get(f"/api/datasets/{dataset_id}/versions", headers=headers)).json()


async def test_saved_recipe_applies_as_a_draft_and_to_the_next_upload(client, auth_headers):
    dataset = await _upload(client, auth_headers)
    saved = await client.put(f"/api/datasets/{dataset['id']}/recipe", headers=auth_headers, json=RECIPE)
    assert saved.status_code == 200, saved.text
    assert saved.json()["recipe"]["revision"] == 1
    assert len(saved.json()["recipe"]["steps"]) == 3

    draft = await client.post(f"/api/datasets/{dataset['id']}/recipe/apply", headers=auth_headers)
    assert draft.status_code == 201, draft.text
    draft = draft.json()
    assert draft["state"] == "draft"
    assert [step["rows_after"] for step in draft["recipe"]["results"]] == [3, 3, 3]
    assert draft["recipe"]["results"][2]["changed_values"] == 2
    rows = await _rows(client, auth_headers, dataset["id"], draft["id"])
    assert [row["work_mode"] for row in rows] == ["Remote", "Remote", "office"]
    original = await _rows(client, auth_headers, dataset["id"])
    assert len(original) == 4, "the current version is unchanged until the draft is confirmed"

    confirmed = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{draft['id']}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": dataset["current_version_id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    again = await client.post(f"/api/datasets/{dataset['id']}/recipe/apply", headers=auth_headers)
    assert again.status_code == 201, again.text
    assert [step["rows_after"] for step in again.json()["recipe"]["results"]] == [3, 3, 3], (
        "re-applying starts from the imported data, not from already cleaned rows"
    )

    replacement = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        files=[("files", ("staff.csv", STAFF_CSV + b"E4,REMOTE ,400\n", "text/csv"))],
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["recipe"]["revision"] == 1
    rows = await _rows(client, auth_headers, dataset["id"], replacement.json()["id"])
    assert [row["work_mode"] for row in rows] == ["Remote", "Remote", "office", "Remote"]


async def test_a_failing_saved_recipe_keeps_the_imported_data_and_explains(client, auth_headers):
    dataset = await _upload(client, auth_headers)
    recipe = {"steps": [{"kind": "rename_column", "table": "staff", "column": "salary", "new_name": "pay"}]}
    await client.put(f"/api/datasets/{dataset['id']}/recipe", headers=auth_headers, json=recipe)
    drifted = STAFF_CSV.replace(b",salary", b",wage")
    replacement = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        files=[("files", ("staff.csv", drifted, "text/csv"))],
    )
    assert replacement.status_code == 201, replacement.text
    applied = replacement.json()["recipe"]
    assert applied["failed_step"] == 0
    assert "Column salary is not in staff" in applied["error"]
    columns = [column["name"] for column in replacement.json()["schema"]["tables"][0]["columns"]]
    assert columns == ["employee_id", "work_mode", "wage"]

    manual = await client.post(f"/api/datasets/{dataset['id']}/recipe/apply", headers=auth_headers)
    assert manual.status_code == 201
    await client.post(
        f"/api/datasets/{dataset['id']}/versions/{replacement.json()['id']}/confirm",
        headers=auth_headers,
        json={"confirmed": True, "expected_current_version_id": dataset["current_version_id"]},
    )
    failed = await client.post(f"/api/datasets/{dataset['id']}/recipe/apply", headers=auth_headers)
    assert failed.status_code == 422
    assert "Step 1" in failed.json()["detail"]


async def test_auto_apply_can_be_turned_off(client, auth_headers):
    dataset = await _upload(client, auth_headers)
    await client.put(
        f"/api/datasets/{dataset['id']}/recipe",
        headers=auth_headers,
        json={**RECIPE, "auto_apply": False},
    )
    replacement = await client.post(
        f"/api/datasets/{dataset['id']}/versions",
        headers=auth_headers,
        files=[("files", ("staff.csv", STAFF_CSV, "text/csv"))],
    )
    assert replacement.json()["recipe"] is None
    assert len(await _rows(client, auth_headers, dataset["id"], replacement.json()["id"])) == 4


async def test_rules_check_every_version_and_suggestions_pass(client, auth_headers):
    dataset = await _upload(client, auth_headers)
    suggestions = await client.get(f"/api/datasets/{dataset['id']}/suggestions", headers=auth_headers)
    assert suggestions.status_code == 200, suggestions.text
    body = suggestions.json()
    assert any(item["step"]["kind"] == "drop_duplicates" for item in body["recipe_steps"])
    assert "no AI model" in body["method"]
    rules = {
        "rules": [
            {"id": "unique-id", "kind": "unique", "table": "staff", "column": "employee_id"},
            {"id": "rows", "kind": "row_count", "table": "staff", "min": 1},
        ]
    }
    saved = await client.put(f"/api/datasets/{dataset['id']}/rules", headers=auth_headers, json=rules)
    assert saved.status_code == 200, saved.text
    assert saved.json()["rules"]["revision"] == 1
    current = next(
        item
        for item in await _versions(client, auth_headers, dataset["id"])
        if item["id"] == dataset["current_version_id"]
    )
    report = current["validation"]
    assert report["rules_revision"] == 1
    assert report["failed_warning"] == 1 and report["passed"] == 1
    unique = next(item for item in report["results"] if item["rule_id"] == "unique-id")
    assert unique["failing_rows"] == 2 and unique["examples"] == ["E3"]

    await client.put(f"/api/datasets/{dataset['id']}/recipe", headers=auth_headers, json=RECIPE)
    draft = (await client.post(f"/api/datasets/{dataset['id']}/recipe/apply", headers=auth_headers)).json()
    assert draft["validation"]["passed"] == 2
    recheck = await client.post(
        f"/api/datasets/{dataset['id']}/versions/{dataset['current_version_id']}/validate",
        headers=auth_headers,
    )
    assert recheck.status_code == 200 and recheck.json()["validation"]["failed_warning"] == 1

    bad = await client.put(
        f"/api/datasets/{dataset['id']}/rules",
        headers=auth_headers,
        json={"rules": [{"kind": "pattern", "table": "staff", "column": "work_mode", "pattern": "("}]},
    )
    assert bad.status_code == 422


async def test_blocking_rules_stop_verified_reports_and_warnings_need_review(client, auth_headers, app):
    dataset, definition = await _approved_dataset(client, auth_headers)
    rule = {"id": "cost-cap", "kind": "range", "table": "sales", "column": "cost", "max": 100}
    await client.put(
        f"/api/datasets/{dataset['id']}/rules", headers=auth_headers, json={"rules": [rule]}
    )
    version = dataset["current_version_id"]
    warned = await _report(client, auth_headers, dataset["id"], definition["id"], version)
    assert warned["status"] == "completed"
    assert warned["verification_status"] == "needs_review"
    assert any("Rule not met" in item for item in warned["warnings"])
    assert warned["provenance"]["validation"]["failed_warning"] == 1

    await client.put(
        f"/api/datasets/{dataset['id']}/rules",
        headers=auth_headers,
        json={"rules": [{**rule, "severity": "blocking"}]},
    )
    blocked = await _report(client, auth_headers, dataset["id"], definition["id"], version)
    assert blocked["status"] == "failed"
    assert blocked["verification_status"] == "blocked"
    check = next(item for item in blocked["provenance"]["checks"] if item["code"] == "validation_rule")
    assert "cost is at most 100" in check["message"] and check["affected_rows"] == 1
    assert blocked["provenance"]["validation"]["failed_blocking"] == 1
    assert not any(artifact["type"] == "table" for artifact in blocked["artifacts"])
    assert app.state.llm.calls == []


async def test_recipe_and_rule_endpoints_are_owner_scoped(client, auth_headers):
    dataset = await _upload(client, auth_headers)
    other = {"email": "recipe-other@example.com", "password": "long-enough-password"}
    await client.post("/api/auth/register", json=other)
    token = (await client.post("/api/auth/login", json=other)).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {token}"}
    version = dataset["current_version_id"]
    paths = [
        ("get", f"/api/datasets/{dataset['id']}/suggestions", None),
        ("put", f"/api/datasets/{dataset['id']}/recipe", RECIPE),
        ("post", f"/api/datasets/{dataset['id']}/recipe/apply", None),
        ("put", f"/api/datasets/{dataset['id']}/rules", {"rules": []}),
        ("post", f"/api/datasets/{dataset['id']}/versions/{version}/validate", None),
    ]
    for method, path, body in paths:
        response = await client.request(method, path, headers=other_headers, json=body)
        assert response.status_code == 404, (path, response.text)
    unchanged = (await client.get(f"/api/datasets/{dataset['id']}", headers=auth_headers)).json()
    assert unchanged["recipe"]["steps"] == [] and unchanged["rules"]["rules"] == []

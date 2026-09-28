from insightforge.db.models import Dataset, DatasetVersion
from insightforge.db.session import SessionLocal


async def test_new_uploads_get_the_current_health_check(client, auth_headers, hr_dataset):
    profile = (await client.get(f"/api/datasets/{hr_dataset['id']}", headers=auth_headers)).json()["profile"]
    assert profile["profile_version"] == 2
    salary = next(column for column in profile["tables"][0]["columns"] if column["name"] == "salary")
    assert salary["kind"] == "number"
    assert salary["median"] is not None
    assert len(salary["histogram"]) == 10


async def test_older_health_checks_can_be_updated(client, auth_headers, hr_dataset):
    dataset_id, version_id = hr_dataset["id"], hr_dataset["current_version_id"]
    db = SessionLocal()
    try:
        older = {"tables": [], "complete": True, "warnings": []}
        db.get(DatasetVersion, version_id).profile_json = older
        db.get(Dataset, dataset_id).profile_json = older
        db.commit()
    finally:
        db.close()
    before = (await client.get(f"/api/datasets/{dataset_id}", headers=auth_headers)).json()
    assert before["profile"]["profile_version"] == 1

    response = await client.post(
        f"/api/datasets/{dataset_id}/versions/{version_id}/profile", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["profile"]["profile_version"] == 2
    after = (await client.get(f"/api/datasets/{dataset_id}", headers=auth_headers)).json()
    assert after["profile"]["profile_version"] == 2
    assert after["profile"]["tables"][0]["row_count"] == 60


async def test_health_check_updates_are_owner_scoped(client, auth_headers, hr_dataset):
    credentials = {"email": "intruder@example.com", "password": "correct horse battery staple"}
    await client.post("/api/auth/register", json=credentials)
    token = (await client.post("/api/auth/login", json=credentials)).json()["access_token"]
    response = await client.post(
        f"/api/datasets/{hr_dataset['id']}/versions/{hr_dataset['current_version_id']}/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404

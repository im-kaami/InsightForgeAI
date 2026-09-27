from insightforge.db.models import Artifact, Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run


async def test_missing_pinned_version_fails_closed(client, auth_headers, hr_dataset, app):
    session = (
        await client.post(
            "/api/sessions",
            headers=auth_headers,
            json={"dataset_id": hr_dataset["id"], "title": "Pinned"},
        )
    ).json()
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    db = SessionLocal()
    try:
        run = Run(
            session_id=session["id"],
            owner_id=user["id"],
            goal="missing version",
            status="pending",
            dataset_version_id="missing-version",
            request_json={"kind": "exploratory", "privacy_mode": "local"},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    execute_run(run_id, llm=app.state.llm)
    db = SessionLocal()
    try:
        failed = db.get(Run, run_id)
        assert failed.status == "failed"
        assert failed.verification_status == "needs_review"
        assert failed.error == "The pinned dataset version is unavailable or not confirmed"
        assert failed.warnings_json == [failed.error]
        assert db.query(Artifact).filter(Artifact.run_id == run_id).count() == 0
    finally:
        db.close()

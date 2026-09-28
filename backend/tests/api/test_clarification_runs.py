import json

from insightforge.core.llm import FakeLLMClient
from insightforge.db.models import Run
from insightforge.db.session import SessionLocal
from insightforge.services.runs import execute_run

QUESTION = "Best by average salary or by performance score?"


async def _session(client, auth_headers, dataset) -> dict:
    await client.patch(
        f"/api/datasets/{dataset['id']}/privacy",
        headers=auth_headers,
        json={"mode": "full", "acknowledged": True},
    )
    return (
        await client.post(
            "/api/sessions", headers=auth_headers, json={"dataset_id": dataset["id"], "title": "Clarify"}
        )
    ).json()


def _pending_run(session_id: str, owner_id: str, goal: str, allow: bool) -> str:
    db = SessionLocal()
    try:
        run = Run(
            session_id=session_id,
            owner_id=owner_id,
            goal=goal,
            status="pending",
            request_json={"kind": "exploratory", "privacy_mode": "full", "allow_clarification": allow},
        )
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


async def test_answered_questions_are_not_asked_again(client, auth_headers, hr_dataset):
    session = await _session(client, auth_headers, hr_dataset)
    first = await client.post(
        f"/api/sessions/{session['id']}/runs", headers=auth_headers, json={"goal": "Top performers?"}
    )
    second = await client.post(
        f"/api/sessions/{session['id']}/runs",
        headers=auth_headers,
        json={"goal": f"Top performers?\n\nClarification: {QUESTION} Performance score", "clarified": True},
    )
    db = SessionLocal()
    try:
        assert db.get(Run, first.json()["id"]).request_json["allow_clarification"] is True
        assert db.get(Run, second.json()["id"]).request_json["allow_clarification"] is False
    finally:
        db.close()


async def test_a_clarifying_question_completes_the_run_without_results(client, auth_headers, hr_dataset):
    session = await _session(client, auth_headers, hr_dataset)
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    table = hr_dataset["tables"][0]
    prompts: list[str] = []

    def reply(messages):
        system = messages[0]["content"]
        prompts.append(system)
        if "too ambiguous" in system:
            return json.dumps(
                {"ambiguous": True, "question": QUESTION, "options": ["Salary", "Performance"]}
            )
        if "data-analysis planner" not in system:
            return "Engineering leads on performance."
        query = f'SELECT department, AVG(performance_score) AS score FROM "{table}" GROUP BY 1'
        return json.dumps({"steps": [{"name": "scores", "action": "sql", "query": query}]})

    llm = FakeLLMClient(reply)
    asked = _pending_run(session["id"], user["id"], "Top performers?", allow=True)
    execute_run(asked, llm=llm)
    payload = (await client.get(f"/api/runs/{asked}", headers=auth_headers)).json()
    assert payload["status"] == "completed", payload["error"]
    assert payload["provenance"]["clarification"] == {
        "question": QUESTION,
        "options": ["Salary", "Performance"],
    }
    assert payload["summary"] == QUESTION
    assert payload["artifacts"] == []

    answered = _pending_run(
        session["id"], user["id"], f"Top performers?\n\nClarification: {QUESTION} Performance", allow=False
    )
    execute_run(answered, llm=llm)
    payload = (await client.get(f"/api/runs/{answered}", headers=auth_headers)).json()
    assert payload["provenance"]["clarification"] is None
    assert [artifact["type"] for artifact in payload["artifacts"]][0] == "table"
    planner_prompts = [prompt for prompt in prompts if "data-analysis planner" in prompt]
    assert len([prompt for prompt in prompts if "too ambiguous" in prompt]) == 1
    assert "Conversation so far" not in planner_prompts[-1]

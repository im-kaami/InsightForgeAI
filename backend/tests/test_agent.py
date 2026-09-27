import json

from insightforge.core.agent import InsightForgeAgent
from insightforge.core.artifacts import PlotArtifact, TableArtifact
from insightforge.core.llm import FakeLLMClient
from insightforge.core.memory import ConversationMemory


def test_agent_end_to_end_and_follow_up_memory(catalog):
    plan = {
        "steps": [
            {
                "name": "salary_by_department",
                "action": "sql",
                "query": (
                    "SELECT department, AVG(salary) AS avg_salary FROM employees "
                    "GROUP BY department ORDER BY department"
                ),
            },
            {
                "name": "salary_chart",
                "action": "plot",
                "kind": "bar",
                "data_source": "salary_by_department",
                "x": "department",
                "y": "avg_salary",
                "title": "Average salary by department",
            },
            {"name": "summary", "action": "summary", "focus": "salary differences"},
        ]
    }
    llm = FakeLLMClient([json.dumps(plan), "Average salary varies by department."])
    memory = ConversationMemory()
    agent = InsightForgeAgent(llm, privacy_mode="full")

    events = []
    result = agent.run("Compare average salary by department", catalog, memory, on_event=events.append)
    assert len([item for item in result.artifacts if isinstance(item, TableArtifact)]) == 1
    assert len([item for item in result.artifacts if isinstance(item, PlotArtifact)]) == 1
    assert result.summary == "Average salary varies by department."
    assert result.timings["total"] > 0
    assert len(memory.turns) == 1
    assert [event["type"] for event in events] == [
        "planning",
        "plan",
        "step_start",
        "step_done",
        "step_start",
        "step_done",
        "step_start",
        "step_done",
        "done",
    ]

    agent.run("Which department stands out?", catalog, memory)
    planner_calls = [
        call
        for call in llm.calls
        if call[0]["content"].startswith("You are a data-analysis planner")
    ]
    assert "Compare average salary by department" in planner_calls[-1][0]["content"]

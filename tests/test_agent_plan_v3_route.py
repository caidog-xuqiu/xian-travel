from __future__ import annotations

from app.routes import plan as plan_routes
from app.services.agent_state import AgentPlanResponse, AgentPlanV3Request


def test_agent_plan_v3_route(monkeypatch) -> None:
    monkeypatch.setattr(
        plan_routes,
        "run_agent_v3",
        lambda text, thread_id, user_key: AgentPlanResponse(
            thread_id=thread_id or "t-v3",
            planning_history=[{"step": 1, "action": "FINISH", "reason": "ok", "args": {}, "outcome_summary": "done"}],
            selected_by="fallback_rule",
        ),
    )
    response = plan_routes.agent_plan_v3(
        AgentPlanV3Request(text="和对象晚上出去", thread_id="t-v3", user_key="u-v3")
    )
    assert response.thread_id == "t-v3"
    assert response.planning_history

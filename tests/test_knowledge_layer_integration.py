from __future__ import annotations

from app.models.schemas import ItineraryResponse, PlanRequest, PlanSummary, RouteItem
from app.services import agent_graph, itinerary_renderer, plan_selector
from app.services.agent_state import AgentState
from app.services.knowledge_layer import bundle_to_tags, retrieve_place_knowledge


def _sample_request(**overrides) -> PlanRequest:
    payload = {
        "companion_type": "partner",
        "available_hours": 4,
        "budget_level": "medium",
        "purpose": "dating",
        "need_meal": True,
        "walking_tolerance": "low",
        "weather": "rainy",
        "origin": "钟楼",
        "preferred_period": "evening",
    }
    payload.update(overrides)
    return PlanRequest(**payload)


def _sample_itinerary(name: str = "大唐不夜城", cluster: str = "曲江夜游簇") -> ItineraryResponse:
    return ItineraryResponse(
        summary="test",
        route=[
            RouteItem(
                time_slot="18:00-19:00",
                type="sight",
                name=name,
                district_cluster=cluster,
                transport_from_prev="地铁/打车 约20分钟",
                reason="夜游氛围更强",
                estimated_distance_meters=3200,
                estimated_duration_minutes=20,
            ),
            RouteItem(
                time_slot="19:10-20:10",
                type="restaurant",
                name="曲江餐饮",
                district_cluster=cluster,
                transport_from_prev="步行 约8分钟",
                reason="顺路用餐",
                estimated_distance_meters=500,
                estimated_duration_minutes=8,
            ),
        ],
        tips=[],
    )


def _sample_summary(**overrides) -> PlanSummary:
    payload = {
        "plan_id": "food_friendly",
        "variant_label": "餐饮友好",
        "stop_count": 2,
        "clusters": ["曲江夜游簇"],
        "is_cross_cluster": False,
        "cross_cluster_count": 0,
        "cluster_transition_summary": "曲江夜游簇",
        "has_meal": True,
        "total_distance_meters": 3700,
        "total_duration_minutes": 28,
        "rhythm": "轻松",
        "budget_level": "medium",
        "walking_tolerance": "low",
        "purpose": "dating",
        "diff_points": ["餐位:中前段", "跨簇:0"],
        "bias_tags": ["food", "prioritize_night_view"],
        "knowledge_tags": ["晚间氛围更强", "适合拍照打卡"],
        "knowledge_notes": ["晚间氛围更好，适合夜游与餐饮衔接。"],
        "place_context_note": "晚间氛围更好，适合夜游与餐饮衔接。",
        "note": "偏顺路餐饮，强调用餐衔接",
    }
    payload.update(overrides)
    return PlanSummary(**payload)


def test_plan_summary_can_include_knowledge_tags() -> None:
    summary = plan_selector._summarize_plan(
        "classic_first",
        _sample_itinerary(name="陕西历史博物馆", cluster="小寨文博簇"),
        _sample_request(purpose="tourism", preferred_period="morning"),
        "偏经典/文博，优先核心点位",
    )
    assert summary.knowledge_tags
    assert summary.place_context_note


def test_selection_reason_and_tags_absorb_knowledge(monkeypatch) -> None:
    monkeypatch.setattr(agent_graph, "save_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent_graph,
        "select_plan_with_llm",
        lambda request, plan_summaries: {
            "selected_plan_id": "food_friendly",
            "selection_reason": "更匹配晚间约会场景",
            "reason_tags": ["更符合夜游偏好"],
        },
    )
    monkeypatch.setattr(
        agent_graph,
        "get_last_selector_debug",
        lambda: {
            "llm_selector_called": True,
            "llm_selected_plan_valid": True,
            "fallback_reason": None,
        },
    )

    state = AgentState(
        parsed_request=_sample_request(),
        candidate_plans=[
            {
                "plan_id": "food_friendly",
                "itinerary": _sample_itinerary(),
                "summary": _sample_summary(),
            }
        ],
        alternative_plans_summary=[_sample_summary()],
        thread_id=None,
    )
    agent_graph.select_plan(state)

    assert state.selection_reason and "晚间氛围更强" in state.selection_reason
    assert any(tag in state.reason_tags for tag in ["晚间氛围更强", "适合拍照打卡"])


def test_readable_output_uses_knowledge_when_hit() -> None:
    readable = itinerary_renderer.render_itinerary_text(
        itinerary=_sample_itinerary(name="陕西历史博物馆", cluster="小寨文博簇"),
        request=_sample_request(purpose="tourism", preferred_period="morning"),
    )
    assert "本次还参考了点位知识" in readable["overview"]
    assert "场景补充" in readable["schedule_text"] or "场景信息" in readable["tips_text"]


def test_readable_output_falls_back_when_knowledge_miss() -> None:
    itinerary = ItineraryResponse(
        summary="fallback",
        route=[
            RouteItem(
                time_slot="14:00-15:00",
                type="sight",
                name="未知地点A",
                district_cluster="未知簇",
                transport_from_prev="步行 约5分钟",
                reason="顺路安排",
                estimated_distance_meters=500,
                estimated_duration_minutes=5,
            )
        ],
        tips=[],
    )
    readable = itinerary_renderer.render_itinerary_text(
        itinerary=itinerary,
        request=_sample_request(purpose="relax", preferred_period="afternoon"),
    )
    assert "本次还参考了点位知识" not in readable["overview"]
    assert "场景补充：" not in readable["schedule_text"]


def test_debug_logs_include_knowledge_summary_hit_and_miss(monkeypatch) -> None:
    monkeypatch.setattr(agent_graph, "save_checkpoint", lambda *_args, **_kwargs: None)
    request = _sample_request()

    hit_state = AgentState(parsed_request=request, search_results=[], thread_id=None)
    monkeypatch.setattr(
        agent_graph,
        "generate_candidate_plans",
        lambda *_args, **_kwargs: [
            {"plan_id": "food_friendly", "itinerary": _sample_itinerary(), "summary": _sample_summary()}
        ],
    )
    agent_graph.generate_candidates(hit_state)
    assert any("knowledge_layer called for summary" in log.message for log in hit_state.debug_logs)

    miss_summary = _sample_summary(knowledge_tags=[], knowledge_notes=[], place_context_note=None)
    miss_state = AgentState(parsed_request=request, search_results=[], thread_id=None)
    monkeypatch.setattr(
        agent_graph,
        "generate_candidate_plans",
        lambda *_args, **_kwargs: [
            {"plan_id": "classic_first", "itinerary": _sample_itinerary(name="未知地点B"), "summary": miss_summary}
        ],
    )
    agent_graph.generate_candidates(miss_state)
    assert any("knowledge_layer miss for summary" in log.message for log in miss_state.debug_logs)


def test_knowledge_layer_can_emit_area_style_tag() -> None:
    bundle = retrieve_place_knowledge(
        query="下午想在高新区轻松逛逛",
        context={"preferred_period": "afternoon", "purpose": "relax", "cluster": "小寨文博簇"},
    )
    tags = bundle_to_tags(bundle)
    assert "区域风格鲜明" in tags or "雨天室内友好" in tags

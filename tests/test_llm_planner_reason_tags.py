from __future__ import annotations

import json

import app.services.llm_planner as llm_planner
from app.models.schemas import PlanRequest, PlanSummary


def _sample_request(**overrides) -> PlanRequest:
    payload = {
        "companion_type": "parents",
        "available_hours": 6,
        "budget_level": "medium",
        "purpose": "tourism",
        "need_meal": True,
        "walking_tolerance": "low",
        "weather": "sunny",
        "origin": "钟楼",
    }
    payload.update(overrides)
    return PlanRequest(**payload)


def _sample_summary(
    *,
    plan_id: str,
    stop_count: int,
    clusters: list[str],
    has_meal: bool,
    cross_cluster_count: int,
    purpose: str = "tourism",
    rhythm: str = "轻松",
    bias_tags: list[str] | None = None,
    note: str = "test",
) -> PlanSummary:
    return PlanSummary(
        plan_id=plan_id,
        variant_label=plan_id,
        stop_count=stop_count,
        clusters=clusters,
        is_cross_cluster=cross_cluster_count > 0,
        cross_cluster_count=cross_cluster_count,
        cluster_transition_summary=" -> ".join(clusters),
        has_meal=has_meal,
        total_distance_meters=1200,
        total_duration_minutes=20,
        rhythm=rhythm,
        budget_level="medium",
        walking_tolerance="low",
        purpose=purpose,
        diff_points=["簇分布"],
        bias_tags=bias_tags or [],
        note=note,
    )


def test_llm_reason_tags_fallback(monkeypatch) -> None:
    monkeypatch.setenv(llm_planner.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_planner.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_planner.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_planner.LLM_BASE_URL_ENV, "http://example.invalid")

    summary = _sample_summary(
        plan_id="classic_first",
        stop_count=2,
        clusters=["城墙钟鼓楼簇"],
        has_meal=True,
        cross_cluster_count=0,
        bias_tags=["classic"],
    )
    payload = {"selected_plan_id": "classic_first", "selection_reason": "ok"}
    response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    monkeypatch.setattr(llm_planner, "_call_llm", lambda prompt: json.dumps(response))

    result = llm_planner.select_plan_with_llm(_sample_request(), [summary])
    assert result is not None
    assert result["selected_plan_id"] == "classic_first"
    assert result["reason_tags"]


def test_post_check_meal_constraint_prefers_meal_plan() -> None:
    request = _sample_request(need_meal=True)
    no_meal = _sample_summary(
        plan_id="classic_first",
        stop_count=3,
        clusters=["城墙钟鼓楼簇"],
        has_meal=False,
        cross_cluster_count=0,
    )
    with_meal = _sample_summary(
        plan_id="food_friendly",
        stop_count=3,
        clusters=["城墙钟鼓楼簇"],
        has_meal=True,
        cross_cluster_count=0,
        purpose="food",
        bias_tags=["food"],
    )

    checked = llm_planner.post_check_selected_plan(request, [no_meal, with_meal], "classic_first")
    assert checked["final_plan_id"] == "food_friendly"
    assert checked["switched"] is True


def test_post_check_avoids_single_stop_when_time_enough() -> None:
    request = _sample_request(available_hours=8, need_meal=False)
    weak_one_stop = _sample_summary(
        plan_id="relaxed_first",
        stop_count=1,
        clusters=["城墙钟鼓楼簇"],
        has_meal=False,
        cross_cluster_count=0,
    )
    fuller_plan = _sample_summary(
        plan_id="classic_first",
        stop_count=3,
        clusters=["城墙钟鼓楼簇", "小寨文博簇"],
        has_meal=True,
        cross_cluster_count=1,
    )

    checked = llm_planner.post_check_selected_plan(request, [weak_one_stop, fuller_plan], "relaxed_first")
    assert checked["final_plan_id"] == "classic_first"


def test_post_check_prefers_night_for_dating_evening() -> None:
    request = _sample_request(
        companion_type="partner",
        purpose="dating",
        preferred_period="evening",
        need_meal=True,
    )
    non_night = _sample_summary(
        plan_id="classic_first",
        stop_count=3,
        clusters=["城墙钟鼓楼簇"],
        has_meal=True,
        cross_cluster_count=0,
        bias_tags=["classic"],
    )
    night_meal = _sample_summary(
        plan_id="food_friendly",
        stop_count=3,
        clusters=["曲江夜游簇"],
        has_meal=True,
        cross_cluster_count=0,
        purpose="dating",
        bias_tags=["food", "prioritize_night_view"],
        note="夜游+餐饮",
    )

    checked = llm_planner.post_check_selected_plan(request, [non_night, night_meal], "classic_first")
    assert checked["final_plan_id"] == "food_friendly"


def test_select_plan_with_llm_applies_post_check_switch(monkeypatch) -> None:
    monkeypatch.setenv(llm_planner.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_planner.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_planner.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_planner.LLM_BASE_URL_ENV, "http://example.invalid")

    summaries = [
        _sample_summary(
            plan_id="classic_first",
            stop_count=3,
            clusters=["城墙钟鼓楼簇"],
            has_meal=False,
            cross_cluster_count=0,
        ),
        _sample_summary(
            plan_id="food_friendly",
            stop_count=3,
            clusters=["城墙钟鼓楼簇"],
            has_meal=True,
            cross_cluster_count=0,
            purpose="food",
            bias_tags=["food"],
        ),
    ]

    payload = {
        "choices": [{"message": {"content": json.dumps({"selected_plan_id": "classic_first", "selection_reason": "ok"})}}]
    }
    monkeypatch.setattr(llm_planner, "_call_llm", lambda prompt: json.dumps(payload))

    result = llm_planner.select_plan_with_llm(_sample_request(need_meal=True), summaries)
    assert result is not None
    assert result["selected_plan_id"] == "food_friendly"
    assert result.get("post_check_switched") is True


def test_select_plan_with_llm_accepts_numeric_string_plan_id(monkeypatch) -> None:
    monkeypatch.setenv(llm_planner.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_planner.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_planner.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_planner.LLM_BASE_URL_ENV, "http://example.invalid")

    summaries = [
        _sample_summary(plan_id="classic_first", stop_count=2, clusters=["城墙钟鼓楼簇"], has_meal=True, cross_cluster_count=0),
        _sample_summary(plan_id="relaxed_first", stop_count=1, clusters=["小寨文博簇"], has_meal=False, cross_cluster_count=0),
    ]
    payload = {"choices": [{"message": {"content": json.dumps({"selected_plan_id": "1", "selection_reason": "index pick"})}}]}
    monkeypatch.setattr(llm_planner, "_call_llm", lambda prompt: json.dumps(payload))

    result = llm_planner.select_plan_with_llm(_sample_request(), summaries)
    assert result is not None
    assert result["selected_plan_id"] == "classic_first"


def test_selector_debug_has_fallback_reason_when_plan_id_invalid(monkeypatch) -> None:
    monkeypatch.setenv(llm_planner.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_planner.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_planner.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_planner.LLM_BASE_URL_ENV, "http://example.invalid")

    summaries = [
        _sample_summary(plan_id="classic_first", stop_count=2, clusters=["城墙钟鼓楼簇"], has_meal=True, cross_cluster_count=0),
    ]
    payload = {"choices": [{"message": {"content": json.dumps({"selected_plan_id": "not-exist", "selection_reason": "bad"})}}]}
    monkeypatch.setattr(llm_planner, "_call_llm", lambda prompt: json.dumps(payload))

    result, debug = llm_planner.select_plan_with_llm_debug(_sample_request(), summaries)
    assert result is None
    assert debug["fallback_reason"] == "invalid_plan_id"


def test_selector_network_exception_retries_once(monkeypatch) -> None:
    monkeypatch.setenv(llm_planner.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_planner.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_planner.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_planner.LLM_BASE_URL_ENV, "http://example.invalid")

    summaries = [
        _sample_summary(plan_id="classic_first", stop_count=2, clusters=["城墙钟鼓楼簇"], has_meal=True, cross_cluster_count=0),
    ]

    calls = {"n": 0}

    def _fake_call(_prompt: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down once")
        payload = {"choices": [{"message": {"content": json.dumps({"selected_plan_id": "classic_first", "selection_reason": "ok"})}}]}
        return json.dumps(payload)

    monkeypatch.setattr(llm_planner, "_call_llm", _fake_call)

    result, debug = llm_planner.select_plan_with_llm_debug(_sample_request(), summaries)
    assert result is not None
    assert result["selected_plan_id"] == "classic_first"
    assert calls["n"] == 2
    assert debug["llm_selector_retry_count"] == 1
    assert debug["fallback_reason"] is None


def test_selector_network_exception_after_retry_sets_call_exception(monkeypatch) -> None:
    monkeypatch.setenv(llm_planner.LLM_ENABLED_ENV, "true")
    monkeypatch.setenv(llm_planner.LLM_PROVIDER_ENV, "custom")
    monkeypatch.setenv(llm_planner.LLM_API_KEY_ENV, "dummy")
    monkeypatch.setenv(llm_planner.LLM_BASE_URL_ENV, "http://example.invalid")

    summaries = [
        _sample_summary(plan_id="classic_first", stop_count=2, clusters=["城墙钟鼓楼簇"], has_meal=True, cross_cluster_count=0),
    ]

    monkeypatch.setattr(llm_planner, "_call_llm", lambda _prompt: (_ for _ in ()).throw(RuntimeError("network down")))

    result, debug = llm_planner.select_plan_with_llm_debug(_sample_request(), summaries)
    assert result is None
    assert debug["llm_selector_retry_count"] == 1
    assert debug["fallback_reason"] == "llm_selector_call_exception"
    assert debug["selector_error_type"] == "network_exception"

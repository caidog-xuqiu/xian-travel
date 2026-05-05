from __future__ import annotations

from pathlib import Path


def test_frontend_contains_scoring_feedback_and_memory_ui() -> None:
    app_js = (Path(__file__).resolve().parents[1] / "frontend" / "app.jsx").read_text(encoding="utf-8")

    assert "/route-feedback" in app_js
    assert "/route-memory" in app_js
    assert "路线评分" in app_js
    assert "提交评分" in app_js
    assert "我的历史高分路线" in app_js
    assert "已纳入高质量路线案例库" in app_js


def test_frontend_displays_search_strategy_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "frontend" / "app.jsx").read_text(encoding="utf-8")
    css = (root / "frontend" / "styles.css").read_text(encoding="utf-8")

    assert "search_plan_summary" in app_js
    assert "search_rounds_debug" in app_js
    assert "clarification_options" in app_js
    assert "search-strategy-card" in app_js
    assert ".search-strategy-card" in css

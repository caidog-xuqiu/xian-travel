from __future__ import annotations

import json
from pathlib import Path
import uuid

from app.services.rag_case_importer import normalize_route_case, read_jsonl, write_jsonl


def test_normalize_route_case_builds_rag_ready_payload() -> None:
    row = {
        "case_id": "001",
        "用户需求": "晚上想和对象吃饭看夜景，步行不要太多",
        "适合区域": "市中心夜景/地标",
        "可用时长": "3小时",
        "同行人": "对象",
        "目的": "约会/夜景/吃饭",
        "路线": "钟楼 -> 大唐不夜城 -> 附近餐厅",
        "推荐理由": "夜景氛围强，适合拍照和约会，餐饮选择多。",
    }

    case = normalize_route_case(row, index=1, source_file="sample.xlsx")

    assert case["id"] == "case_001"
    assert case["city"] == "西安"
    assert case["scene_context"] == "市中心夜景/地标"
    assert [stop["name"] for stop in case["route_stops"]] == ["钟楼", "大唐不夜城", "附近餐厅"]
    assert "dating" in case["route_tags"]
    assert "night_view" in case["route_tags"]
    assert "meal" in case["route_tags"]
    assert case["metadata"]["stop_count"] == 3
    assert "用户需求：晚上想和对象吃饭看夜景" in case["text_for_rag"]
    assert "推荐路线：钟楼 -> 大唐不夜城 -> 附近餐厅" in case["text_for_rag"]


def test_write_and_read_route_case_jsonl() -> None:
    case = normalize_route_case(
        {
            "case_id": "017",
            "用户需求": "下雨天想去市中心",
            "适合区域": "雨天室内/商场逛吃",
            "可用时长": "半天",
            "同行人": "自己",
            "目的": "雨天/室内/商场",
            "路线": "小寨地铁站->赛格商城",
            "推荐理由": "可以去赛格看最长的电梯",
        },
        index=17,
    )
    base = Path(__file__).resolve().parent / "_tmp_artifacts"
    base.mkdir(parents=True, exist_ok=True)
    output = base / f"cases_{uuid.uuid4().hex}.jsonl"

    count = write_jsonl([case], output)
    loaded = read_jsonl(output)

    assert count == 1
    assert loaded[0]["id"] == "case_017"
    assert "rainy" in loaded[0]["route_tags"]
    assert "indoor" in loaded[0]["route_tags"]
    assert loaded[0]["route_stops"][1]["name"] == "赛格商城"
    assert json.loads(output.read_text(encoding="utf-8").splitlines()[0])["metadata"]["city"] == "西安"

import json
from pathlib import Path
from typing import Any, Dict, List

from app.services.poi_service import (
    CLUSTER_QUJIANG,
    load_pois as load_candidate_pois,
)
from app.services.poi_filter import filter_candidate_pois

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "pois.json"


MOCK_INFERRED_FIELDS = [
    "district_cluster",
    "category",
    "indoor_or_outdoor",
    "parent_friendly",
    "friend_friendly",
    "couple_friendly",
    "cost_level",
    "walking_level",
    "estimated_visit_minutes",
    "tags",
    "latitude",
    "longitude",
]


def _default_opening_for_mock(poi: Dict[str, Any]) -> tuple[str, str, str, bool]:
    name = str(poi.get("name", ""))
    category = str(poi.get("category", ""))
    cluster = str(poi.get("district_cluster", ""))
    kind = str(poi.get("kind", ""))
    text = f"{name} {category} {cluster}"

    if kind == "restaurant":
        return "default_rule", "10:00", "21:00", False

    if any(keyword in text for keyword in ["博物馆", "文博", "展馆"]):
        return "default_rule", "09:00", "17:00", False
    if any(keyword in text for keyword in ["不夜城", "夜游", "夜景"]) or cluster == CLUSTER_QUJIANG:
        return "default_rule", "18:00", "23:00", False

    return "default_rule", "09:00", "21:00", False


def _annotate_mock_poi(poi: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(poi)
    item["poi_source"] = "mock"

    inferred = set(item.get("inferred_fields") or [])
    inferred.update(MOCK_INFERRED_FIELDS)

    if "opening_hours_type" not in item or "open_time" not in item or "close_time" not in item:
        opening_type, open_time, close_time, is_all_day = _default_opening_for_mock(item)
        item["opening_hours_type"] = opening_type
        item["open_time"] = open_time
        item["close_time"] = close_time
        item["is_all_day"] = is_all_day
        inferred.update(["opening_hours_type", "open_time", "close_time", "is_all_day"])
    else:
        item.setdefault("is_all_day", False)
        item.setdefault("opening_hours_type", "mock")

    item["inferred_fields"] = sorted(inferred)
    return item


def load_mock_pois() -> List[Dict[str, Any]]:
    """读取本地 mock POI 数据。"""
    with DATA_FILE.open("r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    pois = payload.get("pois", [])
    if not isinstance(pois, list):
        raise ValueError("pois.json 格式错误: pois 必须是列表")

    return [_annotate_mock_poi(poi) for poi in pois]


def load_pois(request_context: Any = None) -> List[Dict[str, Any]]:
    """读取候选 POI。

    默认优先尝试真实 POI（需 AMAP_API_KEY），失败时自动回退 mock 数据。
    候选集合会在此处进入统一质量过滤层（评分前）。
    """
    mock_pois = load_mock_pois()
    candidate_pois = load_candidate_pois(request_context=request_context, fallback_pois=mock_pois)
    return filter_candidate_pois(candidate_pois, request_context=request_context)

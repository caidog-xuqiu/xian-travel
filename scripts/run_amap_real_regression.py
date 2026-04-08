from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from app.services.agent_graph import run_agent_v4_current
from app.services.amap_client import geocode_address, input_tips, search_poi_by_keyword, search_poi_nearby, weather_query

ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "eval_results"

CASES = [
    "陪父母半天，不想太累，中午想吃饭，下雨也能玩",
    "和对象晚上出去，想吃饭、拍照、逛夜景",
    "我在钟楼附近，只有3小时，想轻松一点",
    "和朋友一天，预算低一点，想多逛几个热闹的地方",
    "下午想轻松一点，逛两个地方就行",
    "晚上在曲江附近，想约会吃饭看夜景，步行不要太多",
]


def _load_env() -> None:
    load_dotenv(ROOT / ".env", override=False)


def _safe_preview(text: str, limit: int = 300) -> str:
    return (text or "")[:limit]


def _amap_smoke() -> Dict[str, Any]:
    key = os.getenv("AMAP_API_KEY", "")
    results: Dict[str, Any] = {"key_exists": bool(key), "key_tail": key[-6:] if key else ""}
    try:
        results["geocode"] = geocode_address("西安市钟楼", debug=True)
    except Exception as exc:
        results["geocode"] = {"error": str(exc)}
    try:
        results["keyword_search"] = search_poi_by_keyword("钟楼", city="西安", limit=3, debug=True)
    except Exception as exc:
        results["keyword_search"] = {"error": str(exc)}
    try:
        results["input_tips"] = input_tips("大雁塔", city="西安", limit=3, debug=True)
    except Exception as exc:
        results["input_tips"] = {"error": str(exc)}
    try:
        results["nearby_search"] = search_poi_nearby(lat=34.26, lng=108.95, keyword="餐厅", radius=1000, limit=3, debug=True)
    except Exception as exc:
        results["nearby_search"] = {"error": str(exc)}
    try:
        results["weather"] = weather_query("610100")
    except Exception as exc:
        results["weather"] = {"error": str(exc)}
    return results


def _run_cases() -> List[Dict[str, Any]]:
    outputs: List[Dict[str, Any]] = []
    for text in CASES:
        response = run_agent_v4_current(text=text)
        payload = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        outputs.append(
            {
                "text": text,
                "parsed_by": payload.get("parsed_by"),
                "selected_by": payload.get("selected_by"),
                "amap_called": payload.get("amap_called"),
                "amap_sources_used": payload.get("amap_sources_used"),
                "amap_geo_used": payload.get("amap_geo_used"),
                "amap_route_used": payload.get("amap_route_used"),
                "amap_weather_used": payload.get("amap_weather_used"),
                "route_source": payload.get("route_source"),
                "weather_source": payload.get("weather_source"),
                "amap_fallback_reason": payload.get("amap_fallback_reason"),
                "planning_history": payload.get("planning_history"),
                "candidate_plans_summary": payload.get("candidate_plans_summary"),
                "selected_plan": payload.get("selected_plan"),
                "selection_reason": payload.get("selection_reason"),
                "reason_tags": payload.get("reason_tags"),
                "readable_output": payload.get("readable_output"),
                "amap_events": payload.get("amap_events"),
                "debug_logs": payload.get("debug_logs"),
            }
        )
    return outputs


def main() -> int:
    _load_env()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    smoke = _amap_smoke()
    smoke_path = RESULT_DIR / f"amap_smoke_summary_{stamp}.json"
    smoke_path.write_text(json.dumps(smoke, ensure_ascii=False, indent=2), encoding="utf-8")

    details = _run_cases()
    detail_path = RESULT_DIR / f"amap_real_regression_details_{stamp}.json"
    detail_path.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] smoke={smoke_path}")
    print(f"[ok] details={detail_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

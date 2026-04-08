from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict

from app.services.amap_client import geocode_address, load_valid_amap_api_key, route_plan
from app.services.cache_service import build_cache_key, get_cache, is_cache_enabled, set_cache

AMAP_KEY_ENV = "AMAP_API_KEY"
AMAP_CITY_ENV = "AMAP_CITY"
AMAP_DEFAULT_CITY = "\u897f\u5b89"
ROUTE_CACHE_TTL_SECONDS = 20 * 60


@dataclass
class RouteInfo:
    distance_meters: int
    duration_minutes: int
    mode: str
    summary_text: str
    source: str  # "real_api" | "fallback"


def _point_cache_repr(point: Any) -> Dict[str, Any]:
    if isinstance(point, str):
        return {"kind": "text", "value": point.strip()}
    if isinstance(point, dict):
        return {
            "kind": "poi",
            "id": point.get("id"),
            "name": point.get("name"),
            "cluster": point.get("district_cluster"),
            "lat": point.get("latitude"),
            "lon": point.get("longitude"),
        }
    return {"kind": "other", "value": str(point)}


def _route_cache_key(
    origin: Any,
    destination: Any,
    mode: str,
    origin_cluster: str | None,
    destination_cluster: str | None,
) -> str:
    # Route key composes fixed prefix + mode + stable hash(payload)
    # to avoid overlong keys while preserving readability.
    payload = {
        "origin": _point_cache_repr(origin),
        "destination": _point_cache_repr(destination),
        "origin_cluster": origin_cluster,
        "destination_cluster": destination_cluster,
    }
    return build_cache_key("route", "xian-core", mode, payload=payload)


def _route_from_cache_payload(payload: Any) -> RouteInfo | None:
    if not isinstance(payload, dict):
        return None
    try:
        return RouteInfo(
            distance_meters=int(payload.get("distance_meters", 0)),
            duration_minutes=int(payload.get("duration_minutes", 0)),
            mode=str(payload.get("mode", "")),
            summary_text=str(payload.get("summary_text", "")),
            source=str(payload.get("source", "fallback")),
        )
    except Exception:  # noqa: BLE001
        return None


def _normalize_mode(mode: str) -> str:
    mode = (mode or "").strip()
    if mode in {"public_transit", "drive", "taxi", "walking"}:
        return mode
    return "public_transit"


def _extract_coordinate_from_poi(point: Any) -> tuple[float, float] | None:
    if isinstance(point, dict):
        lat = point.get("latitude")
        lon = point.get("longitude")
        if lat is None or lon is None:
            return None
        try:
            return float(lon), float(lat)
        except (TypeError, ValueError):
            return None
    return None


def _extract_cluster(point: Any) -> str | None:
    if isinstance(point, dict):
        cluster = point.get("district_cluster")
        if isinstance(cluster, str) and cluster:
            return cluster
    return None


def _geocode_address(address: str, api_key: str) -> tuple[float, float] | None:
    try:
        result = geocode_address(address, city=os.getenv(AMAP_CITY_ENV, AMAP_DEFAULT_CITY), api_key=api_key)
    except Exception:  # noqa: BLE001
        return None
    return float(result["longitude"]), float(result["latitude"])


def _resolve_coordinate(point: Any, api_key: str) -> tuple[float, float] | None:
    coordinate = _extract_coordinate_from_poi(point)
    if coordinate:
        return coordinate

    if not api_key:
        return None

    if isinstance(point, str) and point.strip():
        return _geocode_address(point.strip(), api_key)

    if isinstance(point, dict):
        name = point.get("name")
        if isinstance(name, str) and name.strip():
            return _geocode_address(name.strip(), api_key)

    return None


def _haversine_distance_meters(origin: tuple[float, float], destination: tuple[float, float]) -> int:
    lon1, lat1 = origin
    lon2, lat2 = destination

    earth_radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int(earth_radius * c)


def _minutes_by_mode(distance_meters: int, mode: str) -> int:
    speed_kmh = {
        "walking": 4.5,
        "public_transit": 20.0,
        "drive": 28.0,
        "taxi": 28.0,
    }[mode]
    minutes = (distance_meters / 1000.0) / speed_kmh * 60.0
    if mode == "public_transit":
        minutes += 8
    if mode in {"drive", "taxi"}:
        minutes += 4
    return max(3, int(round(minutes)))


def _format_summary(mode: str, distance_meters: int, duration_minutes: int, source: str, note: str = "") -> str:
    text = f"{mode} {distance_meters}m / {duration_minutes}min"
    text += " (real_api)" if source == "real_api" else " (fallback)"
    if note:
        text += f"; {note}"
    return text


def _real_route(origin: Any, destination: Any, mode: str, api_key: str) -> RouteInfo:
    origin_coord = _resolve_coordinate(origin, api_key)
    destination_coord = _resolve_coordinate(destination, api_key)
    if not origin_coord or not destination_coord:
        raise ValueError("unable to resolve coordinates")

    note = ""
    amap_mode = "walking" if mode == "walking" else "driving"
    route = route_plan(
        origin=origin_coord,
        destination=destination_coord,
        mode=amap_mode,
        city=os.getenv(AMAP_CITY_ENV, AMAP_DEFAULT_CITY),
        api_key=api_key,
    )
    distance_meters = int(route["distance_meters"])
    duration_minutes = int(route["duration_minutes"])
    if mode == "taxi":
        note = "taxi reuses driving estimate"
    if mode == "public_transit":
        duration_minutes = max(duration_minutes + 6, duration_minutes)
        note = "public_transit uses driving estimate proxy"

    return RouteInfo(
        distance_meters=distance_meters,
        duration_minutes=duration_minutes,
        mode=mode,
        summary_text=_format_summary(mode, distance_meters, duration_minutes, source="real_api", note=note),
        source="real_api",
    )


def _fallback_route(
    origin: Any,
    destination: Any,
    requested_mode: str,
    origin_cluster: str | None,
    destination_cluster: str | None,
    reason: str,
) -> RouteInfo:
    cluster_a = origin_cluster or _extract_cluster(origin)
    cluster_b = destination_cluster or _extract_cluster(destination)
    same_cluster = bool(cluster_a and cluster_b and cluster_a == cluster_b)

    origin_coord = _extract_coordinate_from_poi(origin)
    destination_coord = _extract_coordinate_from_poi(destination)
    if origin_coord and destination_coord:
        distance_meters = max(200, _haversine_distance_meters(origin_coord, destination_coord))
    else:
        distance_meters = 1200 if same_cluster else 6500

    # Keep V1 simplified fallback: same cluster walking, cross-cluster transit/taxi style estimate.
    mode = "walking" if same_cluster else "public_transit"
    duration_minutes = _minutes_by_mode(distance_meters, mode)

    note = f"fallback reason: {reason}"
    if requested_mode == "taxi" and not same_cluster:
        note += "; cross-cluster keeps simplified transit/taxi rule"

    return RouteInfo(
        distance_meters=distance_meters,
        duration_minutes=duration_minutes,
        mode=mode,
        summary_text=_format_summary(mode, distance_meters, duration_minutes, source="fallback", note=note),
        source="fallback",
    )


def get_route_info(
    origin: Any,
    destination: Any,
    mode: str,
    origin_cluster: str | None = None,
    destination_cluster: str | None = None,
) -> RouteInfo:
    """Get route info with graceful fallback.

    Fallback policy:
    1) API key + call success -> real route
    2) API key + call fail -> fallback route
    3) no API key -> fallback route
    """
    normalized_mode = _normalize_mode(mode)
    cache_key = _route_cache_key(
        origin=origin,
        destination=destination,
        mode=normalized_mode,
        origin_cluster=origin_cluster,
        destination_cluster=destination_cluster,
    )
    cache_enabled = is_cache_enabled()
    cached = get_cache(cache_key) if cache_enabled else None
    cached_route = _route_from_cache_payload(cached)
    if cached_route is not None and cached_route.distance_meters > 0 and cached_route.duration_minutes > 0:
        if cache_enabled:
            print(f"[cache hit] route {normalized_mode}")
        return cached_route

    if cache_enabled:
        print(f"[cache miss] route {normalized_mode}")
    api_key, key_error = load_valid_amap_api_key(AMAP_KEY_ENV)

    route: RouteInfo
    if api_key:
        last_exc: Exception | None = None
        for _attempt in range(2):
            try:
                route = _real_route(origin, destination, normalized_mode, api_key)
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            route = _fallback_route(
                origin=origin,
                destination=destination,
                requested_mode=normalized_mode,
                origin_cluster=origin_cluster,
                destination_cluster=destination_cluster,
                reason=f"api_error:{last_exc.__class__.__name__}",
            )
    else:
        route = _fallback_route(
            origin=origin,
            destination=destination,
            requested_mode=normalized_mode,
            origin_cluster=origin_cluster,
            destination_cluster=destination_cluster,
            reason=key_error or "missing_api_key",
        )

    if cache_enabled:
        set_cache(cache_key, asdict(route), ROUTE_CACHE_TTL_SECONDS)
    return route

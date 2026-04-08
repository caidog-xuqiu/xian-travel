from __future__ import annotations

from app.services.cache_service import build_cache_key
from app.services import routing
from app.services.routing import _route_cache_key


def _sample_origin() -> dict:
    return {
        "id": "sight_bell_tower",
        "name": "钟楼",
        "district_cluster": "城墙钟鼓楼簇",
        "latitude": 34.2590,
        "longitude": 108.9485,
    }


def _sample_destination() -> dict:
    return {
        "id": "sight_dayanta",
        "name": "大雁塔",
        "district_cluster": "大雁塔簇",
        "latitude": 34.2221,
        "longitude": 108.9655,
    }


def test_route_cache_key_is_stable_for_same_params() -> None:
    origin = _sample_origin()
    destination = _sample_destination()

    key1 = _route_cache_key(origin, destination, "public_transit", "城墙钟鼓楼簇", "大雁塔簇")
    key2 = _route_cache_key(origin, destination, "public_transit", "城墙钟鼓楼簇", "大雁塔簇")

    assert key1 == key2


def test_route_cache_key_changes_when_mode_changes() -> None:
    origin = _sample_origin()
    destination = _sample_destination()

    transit_key = _route_cache_key(origin, destination, "public_transit", "城墙钟鼓楼簇", "大雁塔簇")
    drive_key = _route_cache_key(origin, destination, "drive", "城墙钟鼓楼簇", "大雁塔簇")

    assert transit_key != drive_key


def test_route_namespace_is_distinct_from_poi_namespace() -> None:
    route_key = _route_cache_key(
        _sample_origin(),
        _sample_destination(),
        "public_transit",
        "城墙钟鼓楼簇",
        "大雁塔簇",
    )
    poi_key = build_cache_key(
        "poi-search",
        "xian-core",
        "西安",
        "大雁塔 餐厅",
        payload={"offset": 20, "citylimit": True},
    )

    assert route_key != poi_key


def test_get_route_info_retries_real_route(monkeypatch) -> None:
    call_count = {"n": 0}

    def _fake_real(origin, destination, mode, api_key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return routing.RouteInfo(
            distance_meters=1200,
            duration_minutes=12,
            mode=mode,
            summary_text="real_api",
            source="real_api",
        )

    monkeypatch.setattr(routing, "_real_route", _fake_real)
    monkeypatch.setattr(routing, "load_valid_amap_api_key", lambda *_: ("A" * 32, None))
    monkeypatch.setattr(routing, "is_cache_enabled", lambda: False)

    origin = {
        "name": "钟楼",
        "district_cluster": "城墙钟鼓楼簇",
        "latitude": 34.2590,
        "longitude": 108.9485,
    }
    destination = {
        "name": "鼓楼",
        "district_cluster": "城墙钟鼓楼簇",
        "latitude": 34.2630,
        "longitude": 108.9450,
    }
    route = routing.get_route_info(origin, destination, "drive")
    assert route.source == "real_api"
    assert call_count["n"] == 2

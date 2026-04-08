from __future__ import annotations

from app.services import poi_service, routing
from app.services.amap_client import is_valid_amap_api_key, load_valid_amap_api_key


def test_amap_key_format_validation() -> None:
    assert is_valid_amap_api_key("a" * 32) is True
    assert is_valid_amap_api_key("invalid-key-with-dash") is False
    assert is_valid_amap_api_key("short") is False


def test_load_valid_amap_api_key_states(monkeypatch) -> None:
    env_name = "AMAP_API_KEY_TEST_ONLY"

    monkeypatch.delenv(env_name, raising=False)
    key, err = load_valid_amap_api_key(env_name=env_name)
    assert key is None
    assert err == "missing_api_key"

    monkeypatch.setenv(env_name, "bad-key")
    key, err = load_valid_amap_api_key(env_name=env_name)
    assert key is None
    assert err == "invalid_api_key"

    monkeypatch.setenv(env_name, "A" * 32)
    key, err = load_valid_amap_api_key(env_name=env_name)
    assert key == "A" * 32
    assert err is None


def test_routing_invalid_key_falls_back_safely(monkeypatch) -> None:
    monkeypatch.setenv("AMAP_API_KEY", "invalid-key")
    origin = {
        "name": "钟楼",
        "district_cluster": "城墙钟鼓楼簇",
        "latitude": 34.2590,
        "longitude": 108.9485,
    }
    destination = {
        "name": "大雁塔",
        "district_cluster": "大雁塔簇",
        "latitude": 34.2221,
        "longitude": 108.9655,
    }
    route = routing.get_route_info(origin=origin, destination=destination, mode="public_transit")
    assert route.source == "fallback"
    assert "invalid_api_key" in route.summary_text
    assert "invalid-key" not in route.summary_text


def test_poi_service_invalid_key_returns_mock(monkeypatch) -> None:
    monkeypatch.setenv("AMAP_API_KEY", "invalid-key")

    def _should_not_call(_: str):
        raise AssertionError("_fetch_real_pois should not be called when key is invalid")

    monkeypatch.setattr(poi_service, "_fetch_real_pois", _should_not_call)
    mock = [{"id": "m1", "name": "mock", "kind": "sight"}]
    loaded = poi_service.load_pois(fallback_pois=mock)
    assert loaded == mock


def test_routing_route_plan_failure_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AMAP_API_KEY", "A" * 32)
    monkeypatch.setattr(routing, "route_plan", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("api down")))
    origin = {
        "name": "钟楼",
        "district_cluster": "城墙钟鼓楼簇",
        "latitude": 34.2590,
        "longitude": 108.9485,
    }
    destination = {
        "name": "大雁塔",
        "district_cluster": "大雁塔簇",
        "latitude": 34.2221,
        "longitude": 108.9655,
    }
    route = routing.get_route_info(origin=origin, destination=destination, mode="walking")
    assert route.source == "fallback"
    assert "api_error:RuntimeError" in route.summary_text

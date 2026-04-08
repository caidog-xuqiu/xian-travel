from __future__ import annotations

from app.services import cache_service as cache


def test_build_cache_key_same_input_is_stable() -> None:
    key1 = cache.build_cache_key(
        "poi-search",
        "xian-core",
        "西安",
        "大雁塔 餐厅",
        payload={"offset": 20, "citylimit": True},
    )
    key2 = cache.build_cache_key(
        "poi-search",
        "xian-core",
        "西安",
        "大雁塔 餐厅",
        payload={"offset": 20, "citylimit": True},
    )
    assert key1 == key2


def test_build_cache_key_payload_order_does_not_change_key() -> None:
    payload_a = {
        "city": "西安",
        "offset": 20,
        "filters": {"districts": ["碑林区", "莲湖区", "雁塔区"], "citylimit": True},
    }
    payload_b = {
        "filters": {"citylimit": True, "districts": ["碑林区", "莲湖区", "雁塔区"]},
        "offset": 20,
        "city": "西安",
    }
    key1 = cache.build_cache_key("poi-merged", "xian-core", payload=payload_a)
    key2 = cache.build_cache_key("poi-merged", "xian-core", payload=payload_b)
    assert key1 == key2


def test_build_cache_key_changes_with_namespace_or_params() -> None:
    base_payload = {"mode": "public_transit"}
    route_key = cache.build_cache_key("route", "xian-core", "public_transit", payload=base_payload)
    poi_key = cache.build_cache_key("poi-search", "xian-core", "public_transit", payload=base_payload)
    different_param_key = cache.build_cache_key("route", "xian-core", "walking", payload=base_payload)

    assert route_key != poi_key
    assert route_key != different_param_key


def test_cache_safe_fallback_when_redis_client_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_get_client", lambda: None)
    assert cache.get_cache("any:key") is None
    assert cache.set_cache("any:key", {"ok": True}, 120) is False


def test_cache_safe_fallback_when_redis_client_raises(monkeypatch) -> None:
    class BrokenClient:
        def get(self, key: str) -> str:
            raise RuntimeError("redis down")

        def setex(self, name: str, time: int, value: str) -> None:
            raise RuntimeError("redis down")

    monkeypatch.setattr(cache, "_get_client", lambda: BrokenClient())

    assert cache.get_cache("broken:key") is None
    assert cache.set_cache("broken:key", {"ok": True}, 120) is False


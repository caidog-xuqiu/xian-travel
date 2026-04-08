from __future__ import annotations

from app.services import poi_service
from app.services.poi_filter import filter_candidate_pois


def test_poi_merged_cache_key_is_stable_with_same_version(monkeypatch) -> None:
    monkeypatch.setenv(poi_service.AMAP_CITY_ENV, "西安")
    monkeypatch.setattr(poi_service, "POI_MERGED_CACHE_VERSION", "v1")

    key1 = poi_service._build_poi_merged_cache_key()
    key2 = poi_service._build_poi_merged_cache_key()

    assert key1 == key2


def test_poi_merged_cache_key_changes_when_version_changes(monkeypatch) -> None:
    monkeypatch.setenv(poi_service.AMAP_CITY_ENV, "西安")

    monkeypatch.setattr(poi_service, "POI_MERGED_CACHE_VERSION", "v1")
    key_v1 = poi_service._build_poi_merged_cache_key()

    monkeypatch.setattr(poi_service, "POI_MERGED_CACHE_VERSION", "v2")
    key_v2 = poi_service._build_poi_merged_cache_key()

    assert key_v1 != key_v2


def test_map_raw_poi_rejects_shopping_for_sight() -> None:
    raw = {
        "id": "x1",
        "name": "某某购物中心",
        "type": "购物中心;商场",
        "adname": "碑林区",
        "address": "碑林区某路",
        "business_area": "钟楼",
        "location": "108.95,34.26",
        "biz_ext": {"rating": "4.2"},
    }
    mapped = poi_service._map_raw_poi(raw, kind="sight")
    assert mapped is None


def test_filter_rejects_low_rating_with_count() -> None:
    pois = [
        {
            "id": "rest_low_rating",
            "name": "低评分餐馆",
            "kind": "restaurant",
            "district_cluster": "城墙钟鼓楼簇",
            "category": "restaurant",
            "latitude": 34.26,
            "longitude": 108.95,
            "rating": 2.6,
            "rating_count": 50,
        }
    ]
    filtered = filter_candidate_pois(pois)
    assert filtered == []


def test_filter_rejects_out_of_bounds_coordinate() -> None:
    pois = [
        {
            "id": "sight_far",
            "name": "超出范围景点",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "category": "scenic_spot",
            "latitude": 30.0,
            "longitude": 120.0,
        }
    ]
    filtered = filter_candidate_pois(pois)
    assert filtered == []


def test_filter_rejects_far_from_cluster_center() -> None:
    pois = [
        {
            "id": "sight_far_cluster",
            "name": "簇外景点",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "category": "scenic_spot",
            "latitude": 34.35,
            "longitude": 109.03,
        }
    ]
    filtered = filter_candidate_pois(pois)
    assert filtered == []

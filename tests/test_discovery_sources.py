from __future__ import annotations

from app.services import discovery_sources


def test_list_discovery_sources_includes_amap() -> None:
    names = discovery_sources.list_discovery_sources()
    assert discovery_sources.SOURCE_EXISTING in names
    assert discovery_sources.SOURCE_LOCAL_EXTENDED in names
    assert discovery_sources.SOURCE_AMAP_WEB in names


def test_load_local_extended_corpus_has_minimum_items() -> None:
    items = discovery_sources.load_candidates_from_source(
        discovery_sources.SOURCE_LOCAL_EXTENDED,
        query="night view",
        context={"primary_strategies": ["night"], "secondary_strategies": []},
    )
    assert isinstance(items, list)
    assert len(items) >= 6
    assert all(item.get("discovery_primary_source") == discovery_sources.SOURCE_LOCAL_EXTENDED for item in items)


def test_amap_source_returns_empty_and_sets_meta_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    monkeypatch.setattr(discovery_sources, "load_valid_amap_api_key", lambda *_: (None, "missing_api_key"))
    context = {
        "primary_strategies": ["classic"],
        "secondary_strategies": [],
        "source_meta": {},
    }
    items = discovery_sources.load_candidates_from_source(
        discovery_sources.SOURCE_AMAP_WEB,
        query="bell tower",
        context=context,
    )
    assert items == []
    meta = context.get("source_meta", {}).get(discovery_sources.SOURCE_AMAP_WEB, {})
    assert isinstance(meta, dict)
    assert meta.get("fallback_reason") in {"missing_api_key", "invalid_api_key"}


def test_amap_source_maps_nearby_result(monkeypatch) -> None:
    monkeypatch.setenv("AMAP_API_KEY", "A" * 32)

    class _Req:
        need_meal = True
        origin_preference_mode = "nearby"
        origin = "钟楼"
        origin_latitude = 34.26
        origin_longitude = 108.95

    monkeypatch.setattr(
        discovery_sources,
        "search_poi_nearby",
        lambda **kwargs: [
            {
                "id": "amap_1",
                "name": "钟楼",
                "type": "风景名胜;景区",
                "location": "108.95,34.26",
                "address": "西安",
                "adname": "碑林区",
                "business_area": "钟楼",
            }
        ],
    )
    monkeypatch.setattr(discovery_sources, "search_poi_by_keyword", lambda **kwargs: [])
    context = {
        "request_context": _Req(),
        "primary_strategies": ["nearby", "classic"],
        "secondary_strategies": [],
        "source_meta": {},
    }
    items = discovery_sources.load_candidates_from_source(
        discovery_sources.SOURCE_AMAP_WEB,
        query="钟楼附近",
        context=context,
    )
    assert items
    assert items[0]["discovery_primary_source"] == discovery_sources.SOURCE_AMAP_WEB
    assert items[0]["name"]
    meta = context.get("source_meta", {}).get(discovery_sources.SOURCE_AMAP_WEB, {})
    assert meta.get("search_mode") in {"nearby", "keyword"}


def test_nearby_geocode_missing_location_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("AMAP_API_KEY", "A" * 32)

    class _Req:
        need_meal = False
        origin_preference_mode = "nearby"
        origin = "钟楼"
        origin_latitude = None
        origin_longitude = None

    monkeypatch.setattr(
        discovery_sources,
        "geocode_address",
        lambda *_, **__: {"ok": True, "result": {"geocodes": [{"location": None}]}},
    )
    monkeypatch.setattr(discovery_sources, "search_poi_nearby", lambda **__: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        discovery_sources,
        "search_poi_by_keyword",
        lambda **__: {
            "result": [
                {
                    "id": "amap_kw_1",
                    "name": "钟楼",
                    "type": "风景名胜;景区",
                    "location": "108.95,34.26",
                    "address": "西安",
                    "adname": "碑林区",
                    "business_area": "钟楼",
                }
            ]
        },
    )
    context = {
        "request_context": _Req(),
        "primary_strategies": ["nearby", "classic"],
        "secondary_strategies": [],
        "source_meta": {},
    }
    items = discovery_sources.load_candidates_from_source(
        discovery_sources.SOURCE_AMAP_WEB,
        query="钟楼附近",
        context=context,
    )
    assert items
    meta = context.get("source_meta", {}).get(discovery_sources.SOURCE_AMAP_WEB, {})
    assert str(meta.get("fallback_reason", "")).startswith("nearby_failed:geocode_invalid_location")


def test_merge_discovery_results_dedup_and_keep_source_tags() -> None:
    existing = [
        {
            "id": "p1",
            "name": "Bell Tower",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "discovery_primary_source": discovery_sources.SOURCE_EXISTING,
            "discovery_source_tags": [discovery_sources.SOURCE_EXISTING],
        }
    ]
    local = [
        {
            "id": "ext1",
            "name": "Bell Tower",
            "kind": "sight",
            "district_cluster": "城墙钟鼓楼簇",
            "discovery_primary_source": discovery_sources.SOURCE_LOCAL_EXTENDED,
            "discovery_source_tags": [discovery_sources.SOURCE_LOCAL_EXTENDED],
        },
        {
            "id": "ext2",
            "name": "Datang Everbright City",
            "kind": "sight",
            "district_cluster": "曲江夜游簇",
            "discovery_primary_source": discovery_sources.SOURCE_LOCAL_EXTENDED,
            "discovery_source_tags": [discovery_sources.SOURCE_LOCAL_EXTENDED],
        },
    ]
    merged = discovery_sources.merge_discovery_results(
        [
            {"source": discovery_sources.SOURCE_EXISTING, "pois": existing},
            {"source": discovery_sources.SOURCE_LOCAL_EXTENDED, "pois": local},
        ]
    )
    assert merged["total_before_merge"] == 3
    assert merged["total_after_merge"] == 2
    names = [item["name"] for item in merged["merged_pois"]]
    assert "Bell Tower" in names
    bell_tower = next(item for item in merged["merged_pois"] if item["name"] == "Bell Tower")
    assert discovery_sources.SOURCE_EXISTING in bell_tower.get("discovery_source_tags", [])
    assert discovery_sources.SOURCE_LOCAL_EXTENDED in bell_tower.get("discovery_source_tags", [])

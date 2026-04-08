from __future__ import annotations

import json
from typing import Any, Dict, Tuple

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    requests = None

from app.services import amap_client


def _fake_request_json_debug(
    path: str,
    params: Dict[str, Any],
    timeout_seconds: Tuple[int, int] = (5, 15),
    proxy_mode: str | None = "inherit_env",
    temporary_insecure: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    del timeout_seconds
    del proxy_mode, temporary_insecure
    meta = {
        "http_status": 200,
        "raw_text_preview": "{}",
        "request_params": dict(params),
        "exception_type": None,
        "exception_message": None,
        "request_url": "https://restapi.amap.com/test",
        "timeout_seconds": [5, 15],
        "proxy_mode": "inherit_env",
        "env_proxy_snapshot": {},
    }
    if path == "/v3/geocode/geo":
        payload = {
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "geocodes": [
                {
                    "location": "108.95,34.26",
                    "formatted_address": "西安市钟楼",
                    "adcode": "610102",
                    "city": "西安",
                }
            ],
        }
        return payload, meta
    if path == "/v3/place/text":
        payload = {
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "pois": [
                {"id": "1", "name": "钟楼", "location": "108.95,34.26", "type": "景点"}
            ],
        }
        return payload, meta
    if path == "/v3/place/around":
        payload = {
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "pois": [
                {"id": "2", "name": "餐厅A", "location": "108.95,34.26", "type": "餐饮"}
            ],
        }
        return payload, meta
    if path == "/v3/assistant/inputtips":
        payload = {
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "tips": [
                {
                    "name": "大雁塔",
                    "location": "108.96,34.22",
                    "district": "雁塔区",
                    "adcode": "610113",
                }
            ],
        }
        return payload, meta
    return {"status": "0", "info": "INVALID", "infocode": "10001"}, meta


def test_amap_geocode_debug_ok(monkeypatch) -> None:
    monkeypatch.setattr(amap_client, "_request_json_debug", _fake_request_json_debug)
    result = amap_client.geocode_address("西安市钟楼", api_key="A" * 32, debug=True)
    assert result["ok"] is True
    assert result["amap_status"] == "1"
    assert "request_params" in result


def test_amap_keyword_search_debug_ok(monkeypatch) -> None:
    monkeypatch.setattr(amap_client, "_request_json_debug", _fake_request_json_debug)
    result = amap_client.search_poi_by_keyword("钟楼", city="西安", api_key="A" * 32, debug=True)
    assert result["ok"] is True
    assert isinstance(result.get("result"), list)


def test_amap_input_tips_debug_ok(monkeypatch) -> None:
    monkeypatch.setattr(amap_client, "_request_json_debug", _fake_request_json_debug)
    result = amap_client.input_tips("大雁塔", city="西安", api_key="A" * 32, debug=True)
    assert result["ok"] is True
    assert isinstance(result.get("result"), list)


def test_amap_nearby_search_debug_ok(monkeypatch) -> None:
    monkeypatch.setattr(amap_client, "_request_json_debug", _fake_request_json_debug)
    result = amap_client.search_poi_nearby(
        lat=34.26,
        lng=108.95,
        keyword="餐厅",
        radius=1000,
        api_key="A" * 32,
        debug=True,
    )
    assert result["ok"] is True
    assert isinstance(result.get("result"), list)


def test_amap_proxy_error_classification(monkeypatch) -> None:
    if requests is None:
        return
    def _raise_proxy_error(*args, **kwargs):
        raise requests.exceptions.ProxyError("proxy down")

    class _Session:
        def __init__(self):
            self.trust_env = True

        def prepare_request(self, req):
            return req.prepare()

        def send(self, *args, **kwargs):
            return _raise_proxy_error()

    monkeypatch.setattr(amap_client, "_build_session", lambda ignore_env=False: _Session())
    result = amap_client.geocode_address("西安市钟楼", api_key="A" * 32, debug=True)
    assert result["ok"] is False
    assert result["exception_type"] == "ProxyError"


def test_amap_ssl_error_classification(monkeypatch) -> None:
    if requests is None:
        return
    def _raise_ssl_error(*args, **kwargs):
        raise requests.exceptions.SSLError("ssl fail")

    class _Session:
        def __init__(self):
            self.trust_env = True

        def prepare_request(self, req):
            return req.prepare()

        def send(self, *args, **kwargs):
            return _raise_ssl_error()

    monkeypatch.setattr(amap_client, "_build_session", lambda ignore_env=False: _Session())
    result = amap_client.geocode_address("西安市钟楼", api_key="A" * 32, debug=True)
    assert result["ok"] is False
    assert result["exception_type"] == "SSLError"


def test_amap_json_decode_error_classification(monkeypatch) -> None:
    if requests is None:
        return
    class _Resp:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = "not-json"

        def json(self):
            raise json.JSONDecodeError("bad", "not-json", 0)

    class _Session:
        def __init__(self):
            self.trust_env = True

        def prepare_request(self, req):
            return req.prepare()

        def send(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(amap_client, "_build_session", lambda ignore_env=False: _Session())
    result = amap_client.geocode_address("西安市钟楼", api_key="A" * 32, debug=True)
    assert result["ok"] is False
    assert result["exception_type"] == "JSONDecodeError"

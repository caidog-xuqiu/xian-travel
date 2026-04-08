from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from dotenv import load_dotenv
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - exercised via fallback branch in tests/runtime
    requests = None

AMAP_BASE_URL = "https://restapi.amap.com"
AMAP_CITY_ENV = "AMAP_CITY"
AMAP_DEFAULT_CITY = "xian"
_AMAP_ALLOWED_PATH_PREFIX = "/v3/"
_AMAP_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]{16,64}$")
_DOTENV_LOADED = False
_ASCII_FALLBACKS = {
    "西安": "Xi'an",
    "钟楼": "Bell Tower",
    "鼓楼": "Drum Tower",
    "大雁塔": "Giant Wild Goose Pagoda",
    "曲江": "Qujiang",
    "回民街": "Muslim Street",
}
_ASCII_FALLBACK_ALT = {
    "西安": "Xian",
}


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)
    _DOTENV_LOADED = True


def _env_proxy_snapshot() -> Dict[str, bool]:
    keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    ]
    return {key: bool(os.getenv(key)) for key in keys}


def _normalize_proxy_mode(proxy_mode: str | None) -> str:
    mode = str(proxy_mode or "inherit_env").strip().lower()
    return "ignore_env" if mode == "ignore_env" else "inherit_env"


def _build_session(ignore_env: bool) -> "requests.Session":
    if requests is None:  # pragma: no cover - requests absent branch
        raise RuntimeError("requests_missing")
    session = requests.Session()
    if ignore_env:
        session.trust_env = False
    return session


def is_valid_amap_api_key(api_key: str) -> bool:
    if not isinstance(api_key, str):
        return False
    return bool(_AMAP_KEY_PATTERN.fullmatch(api_key.strip()))


def load_valid_amap_api_key(env_name: str = "AMAP_API_KEY") -> Tuple[str | None, str | None]:
    _load_dotenv_once()
    raw = str(os.getenv(env_name, "")).strip()
    if not raw:
        return None, "missing_api_key"
    if not is_valid_amap_api_key(raw):
        return None, "invalid_api_key"
    return raw, None


def resolve_amap_city(city: str | None = None) -> str:
    _load_dotenv_once()
    if isinstance(city, str) and city.strip():
        return city.strip()
    env_city = str(os.getenv(AMAP_CITY_ENV, "")).strip()
    return env_city or AMAP_DEFAULT_CITY


def _normalize_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure params are safe for UTF-8 transport regardless of shell encoding."""
    normalized: Dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str):
            # Force UTF-8 safe text for Chinese characters across shells.
            normalized[key] = value.encode("utf-8", errors="ignore").decode("utf-8")
        else:
            normalized[key] = value
    return normalized


def _apply_ascii_fallbacks(params: Dict[str, Any]) -> Dict[str, Any]:
    """Replace common Chinese place tokens with ASCII if present."""
    updated: Dict[str, Any] = dict(params)
    for key in ("address", "keywords", "city"):
        value = updated.get(key)
        if not isinstance(value, str):
            continue
        text = value
        replaced = False
        for cn, en in _ASCII_FALLBACKS.items():
            if cn in text:
                text = text.replace(cn, en)
                replaced = True
        for cn, en in _ASCII_FALLBACK_ALT.items():
            if cn in text and cn not in _ASCII_FALLBACKS:
                text = text.replace(cn, en)
                replaced = True
        if replaced:
            updated[key] = text
    return updated


def _should_retry_ascii(exc: Exception) -> bool:
    msg = str(exc)
    return msg.endswith("_status_failed") or "ENGINE_RESPONSE_DATA_ERROR" in msg


class AmapRequestError(RuntimeError):
    def __init__(self, message: str, meta: Dict[str, Any]):
        super().__init__(message)
        self.meta = meta


def _request_json_debug(
    path: str,
    params: Dict[str, Any],
    timeout_seconds: Tuple[int, int] = (5, 15),
    proxy_mode: str | None = "inherit_env",
    temporary_insecure: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(path, str) or not path.startswith(_AMAP_ALLOWED_PATH_PREFIX):
        raise AmapRequestError(
            "amap_invalid_path",
            {
                "http_status": 0,
                "raw_text_preview": None,
                "request_params": params,
                "exception_type": "ValueError",
                "exception_message": "invalid_path",
                "request_url": f"{AMAP_BASE_URL}{path}",
                "timeout_seconds": list(timeout_seconds),
                "proxy_mode": _normalize_proxy_mode(proxy_mode),
                "env_proxy_snapshot": _env_proxy_snapshot(),
            },
        )

    url = f"{AMAP_BASE_URL}{path}"
    safe_params = _normalize_params(params)
    mode = _normalize_proxy_mode(proxy_mode)
    meta = {
        "http_status": 0,
        "raw_text_preview": None,
        "request_params": dict(safe_params),
        "exception_type": None,
        "exception_message": None,
        "request_url": url,
        "timeout_seconds": list(timeout_seconds),
        "proxy_mode": mode,
        "env_proxy_snapshot": _env_proxy_snapshot(),
    }

    if requests is not None:
        session = _build_session(ignore_env=(mode == "ignore_env"))
        req = requests.Request(
            "GET",
            url,
            params=safe_params,
            headers={
                "Accept": "application/json",
                "User-Agent": "xian-travel-agent/1.0",
            },
        )
        prepared = session.prepare_request(req)
        meta["request_url"] = prepared.url or url
        try:
            response = session.send(
                prepared,
                timeout=timeout_seconds,
                verify=False if temporary_insecure else True,
            )
        except requests.exceptions.ProxyError as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_proxy_error", meta) from None
        except requests.exceptions.SSLError as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_ssl_error", meta) from None
        except requests.exceptions.ConnectTimeout as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_connect_timeout", meta) from None
        except requests.exceptions.ReadTimeout as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_read_timeout", meta) from None
        except requests.exceptions.ConnectionError as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_connection_error", meta) from None
        except requests.exceptions.RequestException as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_request_exception", meta) from None

        meta["http_status"] = int(response.status_code)
        meta["raw_text_preview"] = (response.text or "")[:200]

        if response.status_code >= 400:
            raise AmapRequestError(f"amap_http_error_{response.status_code}", meta)

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_invalid_json", meta) from None
    else:
        query_url = f"{url}?{urlencode(safe_params, encoding='utf-8', safe=',')}"
        meta["request_url"] = query_url
        req = Request(
            query_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "xian-travel-agent/1.0",
            },
        )
        try:
            with urlopen(req, timeout=timeout_seconds[1]) as resp:  # noqa: S310 - fixed host and path guarded above
                status = int(getattr(resp, "status", 200) or 200)
                meta["http_status"] = status
                if status >= 400:
                    raise AmapRequestError(f"amap_http_error_{status}", meta)
                body = resp.read().decode("utf-8")
        except TimeoutError as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_timeout", meta) from None
        except AmapRequestError:
            raise
        except Exception as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_network_error", meta) from None
        meta["raw_text_preview"] = (body or "")[:200]
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            meta["exception_type"] = type(exc).__name__
            meta["exception_message"] = str(exc)
            raise AmapRequestError("amap_invalid_json", meta) from None

    if not isinstance(payload, dict):
        raise AmapRequestError("amap_invalid_payload", meta)
    return payload, meta


def _request_json(path: str, params: Dict[str, Any], timeout_seconds: int = 6) -> Dict[str, Any]:
    if not isinstance(path, str) or not path.startswith(_AMAP_ALLOWED_PATH_PREFIX):
        raise RuntimeError("amap_invalid_path")

    url = f"{AMAP_BASE_URL}{path}"
    safe_params = _normalize_params(params)
    if requests is not None:
        try:
            response = requests.get(
                url,
                params=safe_params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "xian-travel-agent/1.0",
                },
                timeout=timeout_seconds,
            )
        except requests.exceptions.Timeout:
            raise RuntimeError("amap_timeout") from None
        except requests.exceptions.RequestException:
            raise RuntimeError("amap_network_error") from None

        if response.status_code >= 400:
            raise RuntimeError(f"amap_http_error_{response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("amap_invalid_json") from None
        if str(payload.get("status", "")) == "0" and payload.get("infocode") == "30001":
            # Retry with ASCII fallback tokens when engine complains about data error.
            retry_params = _apply_ascii_fallbacks(safe_params)
            if retry_params != safe_params:
                response = requests.get(
                    url,
                    params=retry_params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "xian-travel-agent/1.0",
                    },
                    timeout=timeout_seconds,
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"amap_http_error_{response.status_code}")
                try:
                    payload = response.json()
                except ValueError:
                    raise RuntimeError("amap_invalid_json") from None
    else:
        # Fallback runtime path when requests is not available in environment.
        # Keep same timeout/error contract so upper layers remain unchanged.
        query_url = f"{url}?{urlencode(safe_params, encoding='utf-8', safe=',')}"
        req = Request(
            query_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "xian-travel-agent/1.0",
            },
        )
        try:
            with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 - fixed host and path guarded above
                status = int(getattr(resp, "status", 200) or 200)
                if status >= 400:
                    raise RuntimeError(f"amap_http_error_{status}")
                body = resp.read().decode("utf-8")
        except TimeoutError:
            raise RuntimeError("amap_timeout") from None
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("amap_network_error") from None
        try:
            payload = json.loads(body)
        except Exception:
            raise RuntimeError("amap_invalid_json") from None
        if str(payload.get("status", "")) == "0" and payload.get("infocode") == "30001":
            retry_params = _apply_ascii_fallbacks(safe_params)
            if retry_params != safe_params:
                query_url = f"{url}?{urlencode(retry_params, encoding='utf-8', safe=',')}"
                req = Request(
                    query_url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "xian-travel-agent/1.0",
                    },
                )
                with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 - fixed host and path guarded above
                    status = int(getattr(resp, "status", 200) or 200)
                    if status >= 400:
                        raise RuntimeError(f"amap_http_error_{status}")
                    body = resp.read().decode("utf-8")
                payload = json.loads(body)

    if not isinstance(payload, dict):
        raise RuntimeError("amap_invalid_payload")
    return payload


def amap_get_json(path: str, params: Dict[str, Any], timeout_seconds: int = 6) -> Dict[str, Any]:
    """Backwards-compatible low-level JSON wrapper."""
    return _request_json(path=path, params=params, timeout_seconds=timeout_seconds)


def _resolve_api_key(api_key: str | None = None) -> str:
    if isinstance(api_key, str) and api_key.strip():
        raw = api_key.strip()
        if not is_valid_amap_api_key(raw):
            raise RuntimeError("invalid_api_key")
        return raw
    loaded, error = load_valid_amap_api_key("AMAP_API_KEY")
    if loaded:
        return loaded
    raise RuntimeError(error or "missing_api_key")


def _parse_location_text(location_text: Any) -> tuple[float, float] | None:
    if not isinstance(location_text, str) or "," not in location_text:
        return None
    lon_text, lat_text = location_text.split(",", 1)
    try:
        return float(lon_text), float(lat_text)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_lng_lat(value: Any) -> tuple[float, float] | None:
    if isinstance(value, str):
        return _parse_location_text(value)
    if isinstance(value, dict):
        lon = value.get("longitude") or value.get("lng") or value.get("lon")
        lat = value.get("latitude") or value.get("lat")
        if lon is None or lat is None:
            location = value.get("location")
            if isinstance(location, str):
                return _parse_location_text(location)
            return None
        try:
            return float(lon), float(lat)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _validate_status(payload: Dict[str, Any], reason_prefix: str) -> None:
    if str(payload.get("status", "")) != "1":
        raise RuntimeError(f"{reason_prefix}_status_failed")


def geocode_address(
    text: str,
    city: str | None = None,
    api_key: str | None = None,
    debug: bool = False,
    proxy_mode: str = "inherit_env",
    temporary_insecure: bool = False,
) -> Dict[str, Any]:
    key = _resolve_api_key(api_key)
    address = str(text or "").strip()
    if not address:
        raise RuntimeError("amap_geocode_empty_address")

    params = {
        "key": key,
        "address": address,
        "city": resolve_amap_city(city),
    }
    if debug:
        try:
            payload, meta = _request_json_debug(
                "/v3/geocode/geo",
                params,
                proxy_mode=proxy_mode,
                temporary_insecure=temporary_insecure,
            )
            amap_status = str(payload.get("status", ""))
            if amap_status != "1":
                retry_params = _apply_ascii_fallbacks(params)
                payload, meta = _request_json_debug(
                    "/v3/geocode/geo",
                    retry_params,
                    proxy_mode=proxy_mode,
                    temporary_insecure=temporary_insecure,
                )
                amap_status = str(payload.get("status", ""))
            return {
                "ok": amap_status == "1",
                "http_status": meta.get("http_status", 0),
                "amap_status": amap_status or None,
                "amap_info": payload.get("info"),
                "amap_infocode": payload.get("infocode"),
                "count": str(len(payload.get("geocodes") or []))
                if isinstance(payload.get("geocodes"), list)
                else None,
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", ""),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": payload,
            }
        except AmapRequestError as exc:
            meta = exc.meta
            return {
                "ok": False,
                "http_status": meta.get("http_status", 0),
                "amap_status": None,
                "amap_info": None,
                "amap_infocode": None,
                "count": None,
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", None),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": {},
            }
    else:
        try:
            payload = _request_json("/v3/geocode/geo", params)
            _validate_status(payload, "amap_geocode")
        except Exception as exc:
            if _should_retry_ascii(exc):
                retry_params = _apply_ascii_fallbacks(params)
                payload = _request_json("/v3/geocode/geo", retry_params)
                _validate_status(payload, "amap_geocode")
            else:
                raise
    geocodes = payload.get("geocodes") or []
    if not isinstance(geocodes, list) or not geocodes:
        raise RuntimeError("amap_geocode_empty")
    first = geocodes[0] if isinstance(geocodes[0], dict) else {}
    location = _parse_location_text(first.get("location"))
    if location is None:
        raise RuntimeError("amap_geocode_invalid_location")

    return {
        "longitude": location[0],
        "latitude": location[1],
        "formatted_address": str(first.get("formatted_address") or address),
        "city": str(first.get("city") or ""),
        "district": str(first.get("district") or ""),
        "adcode": str(first.get("adcode") or ""),
        "source": "amap_geocode",
    }


def reverse_geocode(lat: float, lng: float, api_key: str | None = None) -> Dict[str, Any]:
    key = _resolve_api_key(api_key)
    payload = _request_json(
        "/v3/geocode/regeo",
        {
            "key": key,
            "location": f"{lng},{lat}",
            "extensions": "base",
        },
    )
    _validate_status(payload, "amap_reverse_geocode")
    regeocode = payload.get("regeocode")
    if not isinstance(regeocode, dict):
        raise RuntimeError("amap_reverse_geocode_empty")
    address_component = regeocode.get("addressComponent")
    if not isinstance(address_component, dict):
        address_component = {}

    return {
        "formatted_address": str(regeocode.get("formatted_address") or ""),
        "city": str(address_component.get("city") or ""),
        "district": str(address_component.get("district") or ""),
        "adcode": str(address_component.get("adcode") or ""),
        "township": str(address_component.get("township") or ""),
        "source": "amap_reverse_geocode",
    }


def _match_area_scope(poi: Dict[str, Any], area_scope: Iterable[str] | None) -> bool:
    if not area_scope:
        return True
    scope = [str(x).strip() for x in area_scope if str(x).strip()]
    if not scope:
        return True
    haystack = " ".join(
        [
            str(poi.get("name") or ""),
            str(poi.get("business_area") or ""),
            str(poi.get("address") or ""),
            str(poi.get("adname") or ""),
            str(poi.get("pname") or ""),
            str(poi.get("cityname") or ""),
        ]
    )
    return any(area in haystack for area in scope)


def search_poi_by_keyword(
    keyword: str,
    city: str | None = None,
    area_scope: Iterable[str] | None = None,
    limit: int = 10,
    api_key: str | None = None,
    debug: bool = False,
    proxy_mode: str = "inherit_env",
    temporary_insecure: bool = False,
) -> Any:
    key = _resolve_api_key(api_key)
    query = str(keyword or "").strip()
    if not query:
        return [] if not debug else {
            "ok": False,
            "http_status": 0,
            "amap_status": None,
            "amap_info": None,
            "amap_infocode": None,
            "count": None,
            "request_params": {},
            "raw_text_preview": None,
            "exception_type": None,
            "exception_message": None,
            "request_url": None,
            "timeout_seconds": [5, 15],
            "proxy_mode": proxy_mode,
            "env_proxy_snapshot": _env_proxy_snapshot(),
            "result": [],
        }
    offset = max(1, min(int(limit or 10), 50))
    params = {
        "key": key,
        "keywords": query,
        "city": resolve_amap_city(city),
        "citylimit": "true",
        "offset": offset,
        "page": 1,
        "extensions": "all",
    }
    if debug:
        try:
            payload, meta = _request_json_debug(
                "/v3/place/text",
                params,
                proxy_mode=proxy_mode,
                temporary_insecure=temporary_insecure,
            )
            amap_status = str(payload.get("status", ""))
            if amap_status != "1":
                retry_params = _apply_ascii_fallbacks(params)
                payload, meta = _request_json_debug(
                    "/v3/place/text",
                    retry_params,
                    proxy_mode=proxy_mode,
                    temporary_insecure=temporary_insecure,
                )
                amap_status = str(payload.get("status", ""))
            pois = payload.get("pois") or []
            scoped = [dict(p) for p in pois if isinstance(p, dict) and _match_area_scope(p, area_scope)]
            return {
                "ok": amap_status == "1",
                "http_status": meta.get("http_status", 0),
                "amap_status": amap_status or None,
                "amap_info": payload.get("info"),
                "amap_infocode": payload.get("infocode"),
                "count": str(len(scoped)),
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", ""),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": scoped[:offset],
            }
        except AmapRequestError as exc:
            meta = exc.meta
            return {
                "ok": False,
                "http_status": meta.get("http_status", 0),
                "amap_status": None,
                "amap_info": None,
                "amap_infocode": None,
                "count": None,
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", None),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": [],
            }
    else:
        try:
            payload = _request_json("/v3/place/text", params)
            _validate_status(payload, "amap_poi_text")
        except Exception as exc:
            if _should_retry_ascii(exc):
                retry_params = _apply_ascii_fallbacks(params)
                payload = _request_json("/v3/place/text", retry_params)
                _validate_status(payload, "amap_poi_text")
            else:
                raise
    pois = payload.get("pois") or []
    if not isinstance(pois, list):
        return []
    scoped = [dict(p) for p in pois if isinstance(p, dict) and _match_area_scope(p, area_scope)]
    return scoped[:offset]


def search_poi_nearby(
    lat: float,
    lng: float,
    keyword: str | None = None,
    radius: int = 3000,
    limit: int = 10,
    api_key: str | None = None,
    debug: bool = False,
    proxy_mode: str = "inherit_env",
    temporary_insecure: bool = False,
) -> Any:
    key = _resolve_api_key(api_key)
    offset = max(1, min(int(limit or 10), 50))
    search_radius = max(100, min(int(radius or 3000), 50000))
    params: Dict[str, Any] = {
        "key": key,
        "location": f"{lng},{lat}",
        "radius": search_radius,
        "sortrule": "distance",
        "offset": offset,
        "page": 1,
        "extensions": "all",
    }
    if keyword and str(keyword).strip():
        params["keywords"] = str(keyword).strip()

    if debug:
        try:
            payload, meta = _request_json_debug(
                "/v3/place/around",
                params,
                proxy_mode=proxy_mode,
                temporary_insecure=temporary_insecure,
            )
            amap_status = str(payload.get("status", ""))
            if amap_status != "1":
                retry_params = _apply_ascii_fallbacks(params)
                payload, meta = _request_json_debug(
                    "/v3/place/around",
                    retry_params,
                    proxy_mode=proxy_mode,
                    temporary_insecure=temporary_insecure,
                )
                amap_status = str(payload.get("status", ""))
            pois = payload.get("pois") or []
            scoped = [dict(p) for p in pois if isinstance(p, dict)]
            return {
                "ok": amap_status == "1",
                "http_status": meta.get("http_status", 0),
                "amap_status": amap_status or None,
                "amap_info": payload.get("info"),
                "amap_infocode": payload.get("infocode"),
                "count": str(len(scoped)),
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", ""),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": scoped[:offset],
            }
        except AmapRequestError as exc:
            meta = exc.meta
            return {
                "ok": False,
                "http_status": meta.get("http_status", 0),
                "amap_status": None,
                "amap_info": None,
                "amap_infocode": None,
                "count": None,
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", None),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": [],
            }
    else:
        try:
            payload = _request_json("/v3/place/around", params)
            _validate_status(payload, "amap_poi_nearby")
        except Exception as exc:
            if _should_retry_ascii(exc):
                retry_params = _apply_ascii_fallbacks(params)
                payload = _request_json("/v3/place/around", retry_params)
                _validate_status(payload, "amap_poi_nearby")
            else:
                raise
    pois = payload.get("pois") or []
    if not isinstance(pois, list):
        return []
    return [dict(p) for p in pois if isinstance(p, dict)][:offset]


def route_plan(
    origin: Any,
    destination: Any,
    mode: str = "walking",
    city: str | None = None,
    api_key: str | None = None,
) -> Dict[str, Any]:
    key = _resolve_api_key(api_key)
    origin_coord = _coerce_lng_lat(origin)
    destination_coord = _coerce_lng_lat(destination)
    if origin_coord is None or destination_coord is None:
        raise RuntimeError("amap_route_invalid_coordinate")

    mode_normalized = str(mode or "walking").strip().lower()
    if mode_normalized not in {"walking", "driving"}:
        raise RuntimeError("amap_route_invalid_mode")

    params = {
        "key": key,
        "origin": f"{origin_coord[0]},{origin_coord[1]}",
        "destination": f"{destination_coord[0]},{destination_coord[1]}",
    }
    if mode_normalized == "walking":
        path = "/v3/direction/walking"
    else:
        path = "/v3/direction/driving"
        params["strategy"] = 0
        params["city"] = resolve_amap_city(city)

    payload = _request_json(path, params)
    _validate_status(payload, "amap_route")
    route = payload.get("route")
    if not isinstance(route, dict):
        raise RuntimeError("amap_route_empty")
    paths = route.get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise RuntimeError("amap_route_empty")
    first = paths[0] if isinstance(paths[0], dict) else {}
    distance_meters = int(float(first.get("distance", 0) or 0))
    duration_seconds = float(first.get("duration", 0) or 0)
    if distance_meters <= 0 or duration_seconds <= 0:
        raise RuntimeError("amap_route_invalid_result")
    duration_minutes = max(1, int(round(duration_seconds / 60.0)))
    return {
        "distance_meters": distance_meters,
        "duration_minutes": duration_minutes,
        "mode": mode_normalized,
        "summary_text": f"{mode_normalized} {distance_meters}m/{duration_minutes}min",
        "source": "amap_route",
    }


def input_tips(
    keyword: str,
    city: str | None = None,
    limit: int = 8,
    api_key: str | None = None,
    debug: bool = False,
    proxy_mode: str = "inherit_env",
    temporary_insecure: bool = False,
) -> Any:
    key = _resolve_api_key(api_key)
    query = str(keyword or "").strip()
    if not query:
        return [] if not debug else {
            "ok": False,
            "http_status": 0,
            "amap_status": None,
            "amap_info": None,
            "amap_infocode": None,
            "count": None,
            "request_params": {},
            "raw_text_preview": None,
            "exception_type": None,
            "exception_message": None,
            "request_url": None,
            "timeout_seconds": [5, 15],
            "proxy_mode": proxy_mode,
            "env_proxy_snapshot": _env_proxy_snapshot(),
            "result": [],
        }
    params = {
        "key": key,
        "keywords": query,
        "city": resolve_amap_city(city),
        "citylimit": "true",
        "datatype": "all",
    }
    if debug:
        try:
            payload, meta = _request_json_debug(
                "/v3/assistant/inputtips",
                params,
                proxy_mode=proxy_mode,
                temporary_insecure=temporary_insecure,
            )
            amap_status = str(payload.get("status", ""))
            if amap_status != "1":
                retry_params = _apply_ascii_fallbacks(params)
                payload, meta = _request_json_debug(
                    "/v3/assistant/inputtips",
                    retry_params,
                    proxy_mode=proxy_mode,
                    temporary_insecure=temporary_insecure,
                )
                amap_status = str(payload.get("status", ""))
            tips = payload.get("tips") or []
            scoped = [dict(t) for t in tips if isinstance(t, dict)]
            return {
                "ok": amap_status == "1",
                "http_status": meta.get("http_status", 0),
                "amap_status": amap_status or None,
                "amap_info": payload.get("info"),
                "amap_infocode": payload.get("infocode"),
                "count": str(len(scoped)),
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", ""),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": scoped[: max(1, min(int(limit or 8), 20))],
            }
        except AmapRequestError as exc:
            meta = exc.meta
            return {
                "ok": False,
                "http_status": meta.get("http_status", 0),
                "amap_status": None,
                "amap_info": None,
                "amap_infocode": None,
                "count": None,
                "request_params": meta.get("request_params", {}),
                "raw_text_preview": meta.get("raw_text_preview", None),
                "exception_type": meta.get("exception_type"),
                "exception_message": meta.get("exception_message"),
                "request_url": meta.get("request_url"),
                "timeout_seconds": meta.get("timeout_seconds"),
                "proxy_mode": meta.get("proxy_mode"),
                "env_proxy_snapshot": meta.get("env_proxy_snapshot"),
                "result": [],
            }
    else:
        try:
            payload = _request_json("/v3/assistant/inputtips", params)
            _validate_status(payload, "amap_input_tips")
        except Exception as exc:
            if _should_retry_ascii(exc):
                retry_params = _apply_ascii_fallbacks(params)
                payload = _request_json("/v3/assistant/inputtips", retry_params)
                _validate_status(payload, "amap_input_tips")
            else:
                raise
    tips = payload.get("tips") or []
    if not isinstance(tips, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for tip in tips[: max(1, min(int(limit or 8), 20))]:
        if not isinstance(tip, dict):
            continue
        location = _parse_location_text(tip.get("location"))
        normalized.append(
            {
                "name": str(tip.get("name") or ""),
                "district": str(tip.get("district") or ""),
                "adcode": str(tip.get("adcode") or ""),
                "location": {
                    "longitude": location[0],
                    "latitude": location[1],
                }
                if location
                else None,
                "source": "amap_input_tips",
            }
        )
    return normalized


def weather_query(city_or_adcode: str, api_key: str | None = None) -> Dict[str, Any]:
    key = _resolve_api_key(api_key)
    city_value = str(city_or_adcode or "").strip() or resolve_amap_city(None)
    params = {
        "key": key,
        "city": city_value,
        "extensions": "base",
    }
    try:
        payload = _request_json("/v3/weather/weatherInfo", params)
        _validate_status(payload, "amap_weather")
    except Exception as exc:
        if _should_retry_ascii(exc):
            retry_params = _apply_ascii_fallbacks(params)
            payload = _request_json("/v3/weather/weatherInfo", retry_params)
            _validate_status(payload, "amap_weather")
        else:
            raise
    lives = payload.get("lives") or []
    if not isinstance(lives, list) or not lives:
        raise RuntimeError("amap_weather_empty")
    live = lives[0] if isinstance(lives[0], dict) else {}
    condition = str(live.get("weather") or "").strip() or "unknown"
    temperature = _safe_float(live.get("temperature"))
    lower = condition.lower()
    is_rainy = any(word in lower for word in ("rain", "storm", "drizzle", "shower")) or any(
        word in condition for word in ("雨", "雪")
    )
    is_hot = (temperature is not None and temperature >= 30) or any(
        word in lower for word in ("hot", "heat")
    ) or any(word in condition for word in ("高温", "炎热", "热"))
    return {
        "source": "amap_weather",
        "weather_condition": condition,
        "temperature_c": temperature,
        "feels_like_c": None,
        "is_rainy": is_rainy,
        "is_hot": is_hot,
        "obs_time": live.get("reporttime"),
        "city": city_value,
    }

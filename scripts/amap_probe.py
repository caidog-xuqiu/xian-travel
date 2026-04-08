from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

from dotenv import load_dotenv

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    requests = None

AMAP_URL = "https://restapi.amap.com/v3/geocode/geo"


def _load_env() -> None:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=False)


def _proxy_snapshot() -> Dict[str, bool]:
    keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    ]
    return {key: bool(os.getenv(key)) for key in keys}


def _run_geocode(mode: str, key: str) -> Tuple[bool, Dict[str, Any]]:
    if requests is None:
        return False, {"error": "requests_missing"}

    session = requests.Session()
    if mode == "ignore_env":
        session.trust_env = False

    params = {"key": key, "address": "西安市钟楼"}
    req = requests.Request("GET", AMAP_URL, params=params)
    prepared = session.prepare_request(req)
    final_url = prepared.url or AMAP_URL

    result: Dict[str, Any] = {
        "mode": mode,
        "final_url": final_url,
        "http_status": 0,
        "content_type": None,
        "text_preview": None,
        "exception_type": None,
        "exception_message": None,
    }

    try:
        response = session.send(prepared, timeout=(5, 15))
        result["http_status"] = int(response.status_code)
        result["content_type"] = response.headers.get("Content-Type")
        result["text_preview"] = (response.text or "")[:300]
        payload = response.json()
    except requests.exceptions.RequestException as exc:
        result["exception_type"] = type(exc).__name__
        result["exception_message"] = str(exc)
        return False, result
    except json.JSONDecodeError as exc:
        result["exception_type"] = type(exc).__name__
        result["exception_message"] = str(exc)
        return False, result

    status = str(payload.get("status", ""))
    infocode = str(payload.get("infocode", ""))
    ok = status == "1" and infocode == "10000"
    result["amap_status"] = status
    result["amap_infocode"] = infocode
    return ok, result


def main() -> int:
    _load_env()
    key = str(os.getenv("AMAP_API_KEY", "")).strip()
    key_tail = key[-6:] if key else ""
    print("key_exists", bool(key))
    print("key_tail", key_tail)
    print("current_python", sys.executable)
    print("requests_version", getattr(requests, "__version__", "missing"))
    print("env_proxy_snapshot", json.dumps(_proxy_snapshot(), ensure_ascii=False))

    if not key:
        print("missing AMAP_API_KEY")
        return 1

    success = False
    for mode in ("inherit_env", "ignore_env"):
        ok, payload = _run_geocode(mode, key)
        success = success or ok
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

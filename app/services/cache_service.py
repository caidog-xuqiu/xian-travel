from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

try:
    import redis
except Exception:  # noqa: BLE001
    redis = None

REDIS_HOST_ENV = "REDIS_HOST"
REDIS_PORT_ENV = "REDIS_PORT"
REDIS_DB_ENV = "REDIS_DB"
REDIS_PASSWORD_ENV = "REDIS_PASSWORD"
REDIS_KEY_PREFIX_ENV = "REDIS_KEY_PREFIX"

DEFAULT_REDIS_HOST = "127.0.0.1"
DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_DB = 0
DEFAULT_KEY_PREFIX = "xian-agent"

_REDIS_CLIENT: Any = None
_REDIS_AVAILABLE: bool | None = None
_REDIS_UNAVAILABLE_LOGGED = False


def _log_redis_unavailable_once(reason: str) -> None:
    global _REDIS_UNAVAILABLE_LOGGED
    if _REDIS_UNAVAILABLE_LOGGED:
        return
    _REDIS_UNAVAILABLE_LOGGED = True
    print(f"[cache] redis unavailable fallback: {reason}")


def _disable_redis(reason: str) -> None:
    global _REDIS_AVAILABLE, _REDIS_CLIENT
    _REDIS_AVAILABLE = False
    _REDIS_CLIENT = None
    _log_redis_unavailable_once(reason)


def _get_client() -> Any:
    global _REDIS_CLIENT, _REDIS_AVAILABLE

    if _REDIS_AVAILABLE is False:
        return None
    if _REDIS_AVAILABLE is True and _REDIS_CLIENT is not None:
        return _REDIS_CLIENT

    if redis is None:
        _disable_redis("redis package is not installed")
        return None

    host = os.getenv(REDIS_HOST_ENV, DEFAULT_REDIS_HOST).strip() or DEFAULT_REDIS_HOST
    port_text = os.getenv(REDIS_PORT_ENV, str(DEFAULT_REDIS_PORT)).strip()
    db_text = os.getenv(REDIS_DB_ENV, str(DEFAULT_REDIS_DB)).strip()
    password = os.getenv(REDIS_PASSWORD_ENV, "").strip() or None

    try:
        port = int(port_text)
    except ValueError:
        port = DEFAULT_REDIS_PORT
    try:
        db = int(db_text)
    except ValueError:
        db = DEFAULT_REDIS_DB

    try:
        client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.ping()
        _REDIS_CLIENT = client
        _REDIS_AVAILABLE = True
        return _REDIS_CLIENT
    except Exception as exc:  # noqa: BLE001
        _disable_redis(f"{exc.__class__.__name__}")
        return None


def _normalize_key_part(value: Any) -> str:
    text = str(value if value is not None else "na").strip().lower()
    text = text.replace(" ", "_")
    normalized = re.sub(r"[^\w.-]+", "-", text)
    normalized = normalized.strip("-")
    if not normalized or re.fullmatch(r"[_\-.]+", normalized):
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        normalized = f"h-{digest}"
    return normalized[:48]


def _digest_payload(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:16]


def build_cache_key(namespace: str, *parts: Any, payload: Any = None) -> str:
    """Build a stable and readable cache key.

    Key layout:
    - <prefix>:<namespace>:<part1>:<part2>:...[:<payload_hash>]
    """
    prefix = os.getenv(REDIS_KEY_PREFIX_ENV, DEFAULT_KEY_PREFIX).strip() or DEFAULT_KEY_PREFIX
    key_parts = [prefix, _normalize_key_part(namespace)]
    key_parts.extend(_normalize_key_part(part) for part in parts)
    key = ":".join(key_parts)

    if payload is not None:
        key = f"{key}:{_digest_payload(payload)}"

    if len(key) > 220:
        key = f"{key[:140]}:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"
    return key


def get_cache(key: str) -> Any | None:
    client = _get_client()
    if client is None:
        return None

    try:
        raw = client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        _disable_redis(f"get_failed:{exc.__class__.__name__}")
        return None


def is_cache_enabled() -> bool:
    return _get_client() is not None


def set_cache(key: str, value: Any, ttl_seconds: int) -> bool:
    client = _get_client()
    if client is None:
        return False

    if ttl_seconds <= 0:
        return False

    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        client.setex(name=key, time=ttl_seconds, value=serialized)
        return True
    except Exception as exc:  # noqa: BLE001
        _disable_redis(f"set_failed:{exc.__class__.__name__}")
        return False

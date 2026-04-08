from __future__ import annotations

from typing import Any, Dict

from app.services import sqlite_store


_MEMORY_STORE: Dict[str, Dict[str, Any]] = {}


def save_user_memory(user_or_thread_key: str, payload: Dict[str, Any]) -> None:
    if not user_or_thread_key:
        return
    try:
        sqlite_store.save_memory(memory_key=user_or_thread_key, memory_type="preference", payload=payload)
        return
    except Exception:
        _MEMORY_STORE[user_or_thread_key] = dict(payload)


def recall_user_memory(user_or_thread_key: str) -> Dict[str, Any] | None:
    if not user_or_thread_key:
        return None
    try:
        memory = sqlite_store.recall_memory(user_or_thread_key)
        if memory is not None:
            return memory
    except Exception:
        pass

    memory = _MEMORY_STORE.get(user_or_thread_key)
    return dict(memory) if memory else None

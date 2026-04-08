from __future__ import annotations

from typing import Any, Dict, List

from app.services import sqlite_store


_THREAD_SNAPSHOTS: Dict[str, List[Dict[str, Any]]] = {}


def save_checkpoint(thread_id: str, state_snapshot: Dict[str, Any]) -> None:
    if not thread_id:
        return
    try:
        sqlite_store.save_checkpoint(thread_id=thread_id, current_node=state_snapshot.get("current_node"), state_snapshot=state_snapshot)
        return
    except Exception:
        _THREAD_SNAPSHOTS.setdefault(thread_id, []).append(dict(state_snapshot))


def get_latest_state(thread_id: str) -> Dict[str, Any] | None:
    if not thread_id:
        return None
    try:
        latest = sqlite_store.get_latest_state(thread_id)
        if latest is not None:
            return latest
    except Exception:
        pass

    snapshots = _THREAD_SNAPSHOTS.get(thread_id, [])
    if not snapshots:
        return None
    return dict(snapshots[-1])

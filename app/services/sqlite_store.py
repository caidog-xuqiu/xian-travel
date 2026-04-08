from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "agent_state.db"
DB_PATH_ENV = "AGENT_DB_PATH"


def _db_path() -> Path:
    env_path = os.getenv(DB_PATH_ENV, "").strip()
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def _get_connection() -> sqlite3.Connection:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path.as_posix(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            current_node TEXT,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_memory (
            memory_key TEXT PRIMARY KEY,
            memory_type TEXT NOT NULL,
            memory_payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            current_node TEXT,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def save_checkpoint(thread_id: str, current_node: str | None, state_snapshot: Dict[str, Any]) -> None:
    if not thread_id:
        return
    conn = _get_connection()
    try:
        _init_tables(conn)
        cur = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) AS max_step FROM thread_checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        row = cur.fetchone()
        next_step = (row["max_step"] if row else -1) + 1
        conn.execute(
            """
            INSERT INTO thread_checkpoints (thread_id, step_index, current_node, state_json)
            VALUES (?, ?, ?, ?)
            """,
            (thread_id, next_step, current_node, json.dumps(state_snapshot, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_state(thread_id: str) -> Dict[str, Any] | None:
    if not thread_id:
        return None
    conn = _get_connection()
    try:
        _init_tables(conn)
        cur = conn.execute(
            """
            SELECT state_json FROM thread_checkpoints
            WHERE thread_id = ?
            ORDER BY step_index DESC
            LIMIT 1
            """,
            (thread_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return json.loads(row["state_json"])
    finally:
        conn.close()


def save_memory(memory_key: str, memory_type: str, payload: Dict[str, Any]) -> None:
    if not memory_key:
        return
    conn = _get_connection()
    try:
        _init_tables(conn)
        conn.execute(
            """
            INSERT INTO user_memory (memory_key, memory_type, memory_payload, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(memory_key) DO UPDATE SET
                memory_type=excluded.memory_type,
                memory_payload=excluded.memory_payload,
                updated_at=datetime('now')
            """,
            (memory_key, memory_type, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def save_log(thread_id: str, current_node: str | None, level: str, message: str) -> None:
    if not thread_id:
        return
    conn = _get_connection()
    try:
        _init_tables(conn)
        cur = conn.execute(
            "SELECT COALESCE(MAX(step_index), -1) AS max_step FROM thread_checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        row = cur.fetchone()
        current_step = row["max_step"] if row else -1
        conn.execute(
            """
            INSERT INTO agent_logs (thread_id, step_index, current_node, level, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (thread_id, current_step, current_node, level, message),
        )
        conn.commit()
    finally:
        conn.close()


def recall_memory(memory_key: str) -> Dict[str, Any] | None:
    if not memory_key:
        return None
    conn = _get_connection()
    try:
        _init_tables(conn)
        cur = conn.execute(
            "SELECT memory_payload FROM user_memory WHERE memory_key = ?",
            (memory_key,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return json.loads(row["memory_payload"])
    finally:
        conn.close()

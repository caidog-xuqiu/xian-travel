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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS route_case_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_key TEXT,
            user_query TEXT NOT NULL,
            parsed_request_json TEXT,
            selected_plan TEXT,
            itinerary_json TEXT NOT NULL,
            route_summary_json TEXT,
            knowledge_ids_json TEXT,
            knowledge_bias_json TEXT,
            total_score REAL NOT NULL,
            constraint_score REAL NOT NULL,
            plan_quality_score REAL NOT NULL,
            user_feedback_score REAL NOT NULL DEFAULT 0,
            user_feedback_text TEXT,
            stored_reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS route_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_memory_id INTEGER,
            user_key TEXT,
            user_query TEXT NOT NULL,
            user_rating INTEGER NOT NULL,
            feedback_text TEXT,
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


def save_route_case_memory(payload: Dict[str, Any]) -> int:
    conn = _get_connection()
    try:
        _init_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO route_case_memory (
                user_key,
                user_query,
                parsed_request_json,
                selected_plan,
                itinerary_json,
                route_summary_json,
                knowledge_ids_json,
                knowledge_bias_json,
                total_score,
                constraint_score,
                plan_quality_score,
                user_feedback_score,
                user_feedback_text,
                stored_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("user_key"),
                payload.get("user_query") or "",
                json.dumps(payload.get("parsed_request") or {}, ensure_ascii=False),
                payload.get("selected_plan"),
                json.dumps(payload.get("itinerary") or {}, ensure_ascii=False),
                json.dumps(payload.get("route_summary") or {}, ensure_ascii=False),
                json.dumps(payload.get("knowledge_ids") or [], ensure_ascii=False),
                json.dumps(payload.get("knowledge_bias") or {}, ensure_ascii=False),
                float(payload.get("total_score") or 0.0),
                float(payload.get("constraint_score") or 0.0),
                float(payload.get("plan_quality_score") or 0.0),
                float(payload.get("user_feedback_score") or 0.0),
                payload.get("user_feedback_text"),
                payload.get("stored_reason"),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def save_route_feedback(payload: Dict[str, Any]) -> int:
    conn = _get_connection()
    try:
        _init_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO route_feedback (
                case_memory_id,
                user_key,
                user_query,
                user_rating,
                feedback_text
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.get("case_memory_id"),
                payload.get("user_key"),
                payload.get("user_query") or "",
                int(payload.get("user_rating") or 0),
                payload.get("feedback_text"),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update_route_case_feedback(
    case_memory_id: int,
    *,
    total_score: float,
    user_feedback_score: float,
    user_feedback_text: str | None = None,
) -> None:
    conn = _get_connection()
    try:
        _init_tables(conn)
        conn.execute(
            """
            UPDATE route_case_memory
            SET total_score = ?,
                user_feedback_score = ?,
                user_feedback_text = ?
            WHERE id = ?
            """,
            (float(total_score), float(user_feedback_score), user_feedback_text, int(case_memory_id)),
        )
        conn.commit()
    finally:
        conn.close()


def list_recent_high_score_cases(
    *,
    user_key: str | None = None,
    limit: int = 5,
    min_score: float = 8.0,
) -> list[Dict[str, Any]]:
    conn = _get_connection()
    try:
        _init_tables(conn)
        params: list[Any] = [float(min_score)]
        where = "total_score >= ?"
        if user_key:
            where += " AND user_key = ?"
            params.append(user_key)
        params.append(int(limit))
        cur = conn.execute(
            f"""
            SELECT *
            FROM route_case_memory
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        )
        return [_route_case_row_to_dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_route_case_memory(case_memory_id: int) -> Dict[str, Any] | None:
    conn = _get_connection()
    try:
        _init_tables(conn)
        cur = conn.execute("SELECT * FROM route_case_memory WHERE id = ?", (int(case_memory_id),))
        row = cur.fetchone()
        return _route_case_row_to_dict(row) if row else None
    finally:
        conn.close()


def _json_loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _route_case_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "user_key": row["user_key"],
        "user_query": row["user_query"],
        "parsed_request": _json_loads(row["parsed_request_json"], {}),
        "selected_plan": row["selected_plan"],
        "itinerary": _json_loads(row["itinerary_json"], {}),
        "route_summary": _json_loads(row["route_summary_json"], {}),
        "knowledge_ids": _json_loads(row["knowledge_ids_json"], []),
        "knowledge_bias": _json_loads(row["knowledge_bias_json"], {}),
        "total_score": row["total_score"],
        "constraint_score": row["constraint_score"],
        "plan_quality_score": row["plan_quality_score"],
        "user_feedback_score": row["user_feedback_score"],
        "user_feedback_text": row["user_feedback_text"],
        "stored_reason": row["stored_reason"],
        "created_at": row["created_at"],
    }

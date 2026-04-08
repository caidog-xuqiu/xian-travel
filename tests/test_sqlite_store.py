from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from app.services import sqlite_store


def test_sqlite_checkpoint_and_memory(monkeypatch) -> None:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    db_path = base / f"agent_state_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("AGENT_DB_PATH", str(db_path))

    sqlite_store.save_checkpoint(
        thread_id="t1",
        current_node="analyze_query",
        state_snapshot={"thread_id": "t1", "current_node": "analyze_query"},
    )
    latest = sqlite_store.get_latest_state("t1")
    assert latest is not None
    assert latest.get("thread_id") == "t1"

    sqlite_store.save_memory("user-1", "preference", {"budget_level": "low"})
    memory = sqlite_store.recall_memory("user-1")
    assert memory == {"budget_level": "low"}

    sqlite_store.save_log("t1", "analyze_query", "info", "log message")

    conn = sqlite3.connect(db_path.as_posix())
    try:
        cur = conn.execute("SELECT COUNT(*) FROM thread_checkpoints WHERE thread_id = ?", ("t1",))
        assert cur.fetchone()[0] == 1
        cur = conn.execute("SELECT COUNT(*) FROM user_memory WHERE memory_key = ?", ("user-1",))
        assert cur.fetchone()[0] == 1
        cur = conn.execute("SELECT COUNT(*) FROM agent_logs WHERE thread_id = ?", ("t1",))
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()

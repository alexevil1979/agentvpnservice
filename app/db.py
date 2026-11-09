from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.config import get_settings

_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    check_code TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '',
                    observed_at TEXT,
                    status TEXT NOT NULL,
                    probe_json TEXT NOT NULL DEFAULT '{}',
                    proposed_runbook TEXT,
                    proposal_text TEXT NOT NULL DEFAULT '',
                    risk TEXT NOT NULL DEFAULT '',
                    decision TEXT,
                    decision_at TEXT,
                    decision_by TEXT,
                    nonce TEXT,
                    telegram_message_id TEXT,
                    reminded_at TEXT,
                    result_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_open
                    ON incidents(server_id, check_code, status);
                CREATE TABLE IF NOT EXISTS incident_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    output TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE TABLE IF NOT EXISTS settings_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def create_incident(
    *,
    server_id: str,
    check_code: str,
    summary: str,
    evidence: str,
    observed_at: Optional[str],
) -> Dict[str, Any]:
    now = utcnow()
    incident_id = str(uuid.uuid4())
    with db() as conn:
        existing = conn.execute(
            """
            SELECT * FROM incidents
            WHERE server_id=? AND check_code=? AND status IN
              ('received','probing','awaiting_approval','approved','running','needs_human')
            ORDER BY created_at DESC LIMIT 1
            """,
            (server_id, check_code),
        ).fetchone()
        if existing:
            return {"created": False, "incident": dict(existing)}

        busy = conn.execute(
            """
            SELECT id FROM incidents
            WHERE server_id=? AND status IN ('approved','running')
            LIMIT 1
            """,
            (server_id,),
        ).fetchone()
        if busy:
            # Still create as needs_human if another fix is running? Plan: one fix per server.
            # Return the busy one for same check; for different check create needs_human note.
            pass

        conn.execute(
            """
            INSERT INTO incidents(
                id, server_id, check_code, summary, evidence, observed_at,
                status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                incident_id,
                server_id,
                check_code,
                summary or "",
                evidence or "",
                observed_at,
                "received",
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return {"created": True, "incident": dict(row)}


def get_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return row_to_dict(row)


def list_incidents(
    *,
    status: Optional[str] = None,
    server_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    q = "SELECT * FROM incidents WHERE 1=1"
    args: List[Any] = []
    if status:
        q += " AND status=?"
        args.append(status)
    if server_id:
        q += " AND server_id=?"
        args.append(server_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with db() as conn:
        return [dict(r) for r in conn.execute(q, args).fetchall()]


def update_incident(incident_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    if not fields:
        return get_incident(incident_id)
    fields = dict(fields)
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [incident_id]
    with db() as conn:
        conn.execute(f"UPDATE incidents SET {cols} WHERE id=?", vals)
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return row_to_dict(row)


def add_step(incident_id: str, step_name: str, ok: bool, output: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO incident_steps(incident_id, step_name, ok, output, created_at)
            VALUES (?,?,?,?,?)
            """,
            (incident_id, step_name, 1 if ok else 0, output or "", utcnow()),
        )


def list_steps(incident_id: str) -> List[Dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM incident_steps WHERE incident_id=? ORDER BY id ASC",
            (incident_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_kv(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_kv(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings_kv(key, value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)

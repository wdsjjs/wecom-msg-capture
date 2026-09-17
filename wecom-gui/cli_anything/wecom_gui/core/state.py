"""Local storage and identity primitives for desktop transport."""

from __future__ import annotations

import json

import os

import sqlite3

import time

import hashlib

from contextlib import contextmanager

from pathlib import Path

import fcntl


def state_dir() -> Path:
    configured = os.environ.get("WECOM_GUI_STATE_DIR", "").strip()
    path = (
        Path(configured).expanduser() if configured else Path.home() / ".wecom-capture"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_event(event: dict) -> Path:
    """Append a JSONL audit event and return the log path."""
    log_dir = state_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "events.jsonl"
    payload = {"ts": time.time(), **event}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def db_path() -> Path:
    """Return the SQLite state database path."""
    return state_dir() / "state.sqlite"


@contextmanager
def gui_lock():
    """Serialize GUI operations across scanner/worker processes."""
    path = state_dir() / "gui.lock"
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def connect() -> sqlite3.Connection:
    """Open the state database and ensure schema exists."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create only the identity mapping required by the transport."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wecom_customer_bindings (
            uid TEXT PRIMARY KEY,
            customer_name TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_wecom_customer_bindings_name
        ON wecom_customer_bindings(customer_name)
    """)


def _legacy_conversation_key(title: object) -> str:
    value = str(title or "").strip()
    return "legacy:" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _clean_key_part(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:20]


def conversation_key_for_row(row: dict) -> str:
    """Return the queue isolation key for a visible WeCom conversation row."""
    explicit_key = _clean_key_part(row.get("conversation_key"))
    if explicit_key:
        return explicit_key
    for key in (
        "external_userid",
        "external_user_id",
        "externalUserId",
        "uid",
        "user_id",
    ):
        value = _clean_key_part(row.get(key))
        if value:
            return f"uid:{value}"

    title = _clean_key_part(row.get("title"))
    if title:
        return _legacy_conversation_key(title)

    tags = ",".join(
        _clean_key_part(tag) for tag in row.get("tags", []) if _clean_key_part(tag)
    )
    source = _clean_key_part(row.get("source"))
    slot = ""
    if row.get("click_y") is not None:
        try:
            slot = f"y{round(float(row.get('click_y')) / 12) * 12:.0f}"
        except (TypeError, ValueError):
            slot = ""
    if not slot and row.get("index") is not None:
        slot = f"i{_clean_key_part(row.get('index'))}"
    if slot:
        return "visible:" + _short_hash("|".join([title, tags, source, slot]))
    return _legacy_conversation_key(title)


def conversation_key_for_uid(uid: object) -> str:
    """Return the stable queue key for one real WeCom external user id."""
    value = _clean_key_part(uid)
    return f"uid:{value}" if value else ""


def _binding_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    return item


def bind_wecom_customer(
    *,
    uid: str,
    customer_name: str,
    display_name: str = "",
    source: str = "sidebar",
    raw: dict | None = None,
) -> dict:
    """Bind a WeCom external user id to the visible conversation/customer name."""
    uid = str(uid or "").strip()
    customer_name = str(customer_name or "").strip()
    display_name = str(display_name or "").strip()
    source = str(source or "sidebar").strip() or "sidebar"
    if not uid:
        raise ValueError("uid is required")
    if not customer_name:
        raise ValueError("customer_name is required")
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO wecom_customer_bindings
                (uid, customer_name, display_name, source, raw_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                customer_name = excluded.customer_name,
                display_name = excluded.display_name,
                source = excluded.source,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                uid,
                customer_name,
                display_name,
                source,
                json.dumps(raw or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM wecom_customer_bindings WHERE uid = ?", (uid,)
        ).fetchone()
    return _binding_to_dict(row)


def lookup_wecom_customer(*, uid: str = "", customer_name: str = "") -> dict | None:
    """Find a WeCom customer binding by uid or visible customer name."""
    uid = str(uid or "").strip()
    customer_name = str(customer_name or "").strip()
    with connect() as conn:
        row = None
        if uid:
            row = conn.execute(
                "SELECT * FROM wecom_customer_bindings WHERE uid = ?", (uid,)
            ).fetchone()
        if row is None and customer_name:
            rows = conn.execute(
                """
                SELECT * FROM wecom_customer_bindings
                WHERE customer_name = ? OR display_name = ?
                ORDER BY updated_at DESC
                """,
                (customer_name, customer_name),
            ).fetchall()
            unique_uids = {str(item["uid"] or "") for item in rows}
            if len(unique_uids) == 1:
                row = rows[0]
    return _binding_to_dict(row) if row else None


def list_wecom_customer_bindings(limit: int = 100) -> list[dict]:
    """List recent WeCom customer uid bindings."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM wecom_customer_bindings
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_binding_to_dict(row) for row in rows]

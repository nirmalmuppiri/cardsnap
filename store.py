import os
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

_default = Path(__file__).parent / "cardsnap.db"
DB_PATH = Path(os.environ.get("DB_PATH", str(_default)))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name TEXT    NOT NULL,
                data       TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            )
        """)
        conn.commit()


def save_contact(event_name: str, data: dict) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO contacts (event_name, data, created_at) VALUES (?, ?, ?)",
            (event_name, json.dumps(data, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid


def get_contacts(event_name: str | None = None) -> list[dict]:
    with _connect() as conn:
        if event_name:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE event_name = ? ORDER BY created_at",
                (event_name,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM contacts ORDER BY created_at").fetchall()
    result = []
    for row in rows:
        r = dict(row)
        r["data"] = json.loads(r["data"])
        result.append(r)
    return result


def update_contact(contact_id: int, data: dict):
    with _connect() as conn:
        conn.execute(
            "UPDATE contacts SET data = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), contact_id),
        )
        conn.commit()


def delete_contact(contact_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()


def list_events() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT event_name FROM contacts ORDER BY event_name"
        ).fetchall()
    return [row["event_name"] for row in rows]

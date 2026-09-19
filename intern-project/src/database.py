"""SQLite connection and repository helpers (raw sqlite3)."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data" / "schema.sql"


def connect(db_path: str | Path) -> "sqlite3.Connection":
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str | Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: str | Path):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_user(conn: "sqlite3.Connection", email: str, password_hash: str) -> int:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash)
    )
    return int(cur.lastrowid)


def get_user_by_email(conn: "sqlite3.Connection", email: str) -> "sqlite3.Row | None":
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(conn: "sqlite3.Connection", user_id: int) -> "sqlite3.Row | None":
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def insert_ticket_and_decision(
    conn: "sqlite3.Connection",
    user_id: int,
    message: str,
    facts: dict,
    decision: dict,
) -> int:
    """Persist a ticket and its decision in one transaction; returns the ticket id."""
    try:
        cur = conn.execute(
            "INSERT INTO tickets (user_id, message, facts_json) VALUES (?, ?, ?)",
            (user_id, message, json.dumps(facts, ensure_ascii=False)),
        )
        ticket_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO decisions (ticket_id, action, reason, confidence, sources)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                ticket_id,
                decision["action"],
                decision["reason"],
                float(decision["confidence"]),
                json.dumps(decision["sources"], ensure_ascii=False),
            ),
        )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return ticket_id


def list_tickets_for_user(
    conn: "sqlite3.Connection", user_id: int, limit: int, offset: int
) -> list["sqlite3.Row"]:
    return conn.execute(
        "SELECT t.id, t.user_id, t.message, t.facts_json, t.created_at,"
        " d.action, d.reason, d.confidence, d.sources, d.created_at AS decision_created_at"
        " FROM tickets t LEFT JOIN decisions d ON d.ticket_id = t.id"
        " WHERE t.user_id = ? ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()


def get_ticket_for_user(
    conn: "sqlite3.Connection", ticket_id: int, user_id: int
) -> "sqlite3.Row | None":
    return conn.execute(
        "SELECT t.id, t.user_id, t.message, t.facts_json, t.created_at,"
        " d.action, d.reason, d.confidence, d.sources, d.created_at AS decision_created_at"
        " FROM tickets t LEFT JOIN decisions d ON d.ticket_id = t.id"
        " WHERE t.id = ? AND t.user_id = ?",
        (ticket_id, user_id),
    ).fetchone()

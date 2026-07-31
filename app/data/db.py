"""SQLite data layer: students, face embeddings, sessions, attendance, presence events."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).resolve().parents[2] / "attendance.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_no  TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    time_in     TEXT NOT NULL,
    time_out    TEXT,
    status      TEXT NOT NULL DEFAULT 'Present',
    UNIQUE (session_id, student_id)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id  INTEGER REFERENCES students(id) ON DELETE SET NULL,
    event_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------- students

def add_student(student_no: str, name: str, embedding: np.ndarray) -> int:
    blob = embedding.astype(np.float32).tobytes()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO students (student_no, name, embedding, created_at) VALUES (?, ?, ?, ?)",
            (student_no, name, blob, _now()),
        )
        return cur.lastrowid


def list_students() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, student_no, name, created_at FROM students ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_student_embedding(student_id: int) -> np.ndarray | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT embedding FROM students WHERE id = ?", (student_id,)
        ).fetchone()
    if row is None:
        return None
    return np.frombuffer(row["embedding"], dtype=np.float32)


def all_embeddings() -> list[tuple[int, np.ndarray]]:
    with _connect() as conn:
        rows = conn.execute("SELECT id, embedding FROM students").fetchall()
    return [(r["id"], np.frombuffer(r["embedding"], dtype=np.float32)) for r in rows]


def delete_student(student_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))


# ---------------------------------------------------------------- sessions

def create_session(name: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (name, started_at) VALUES (?, ?)", (name, _now())
        )
        return cur.lastrowid


def end_session(session_id: int) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (now, session_id),
        )
        conn.execute(
            "UPDATE attendance SET time_out = ? WHERE session_id = ? AND time_out IS NULL",
            (now, session_id),
        )


def list_sessions() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, started_at, ended_at FROM sessions ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# -------------------------------------------------------------- attendance

def record_time_in(session_id: int, student_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO attendance (session_id, student_id, time_in) VALUES (?, ?, ?)",
            (session_id, student_id, _now()),
        )


def record_time_out(session_id: int, student_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE attendance SET time_out = ? WHERE session_id = ? AND student_id = ? "
            "AND time_out IS NULL",
            (_now(), session_id, student_id),
        )


def set_status(attendance_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE attendance SET status = ? WHERE id = ?", (status, attendance_id))


def session_report(session_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT a.id AS attendance_id, s.student_no, s.name,
                   a.time_in, a.time_out, a.status,
                   (SELECT COUNT(*) FROM events e
                     WHERE e.session_id = a.session_id AND e.student_id = a.student_id
                       AND e.event_type IN ('out_of_frame', 'camera_off', 'identity_mismatch')
                   ) AS alert_count
            FROM attendance a
            JOIN students s ON s.id = a.student_id
            WHERE a.session_id = ?
            ORDER BY a.time_in
            """,
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ events

def log_event(session_id: int, student_id: int | None, event_type: str, message: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO events (session_id, student_id, event_type, message, occurred_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, student_id, event_type, message, _now()),
        )


def session_events(session_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT e.occurred_at, e.event_type, e.message, s.name AS student_name
            FROM events e
            LEFT JOIN students s ON s.id = e.student_id
            WHERE e.session_id = ?
            ORDER BY e.id
            """,
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]

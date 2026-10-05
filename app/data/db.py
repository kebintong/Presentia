"""SQLite data layer: classes, students, face embeddings, sessions, attendance,
presence events.

A *class* is what the instructor picks when the app starts (like a Google
Classroom class). It owns a roster of students and the sessions (meetings)
held for it. A student is stored once with their face data and can belong to
several classes, so the same face never has to be registered twice.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


def _resolve_db_path() -> Path:
    """Pick a writable location for the SQLite file.

    Dev mode: keep it in the project root, next to app/, like before.
    Frozen/installed mode (PyInstaller, or PRESENTIA_DATA_DIR set by the Go
    shell): use a per-user, always-writable app-data folder instead, since
    Program Files / /Applications are read-only for normal users.
    """
    override = os.environ.get("PRESENTIA_DATA_DIR")
    if override:
        data_dir = Path(override)
    elif getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = os.environ.get("APPDATA", str(Path.home()))
        elif sys.platform == "darwin":
            base = str(Path.home() / "Library" / "Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        data_dir = Path(base) / "Presentia"
    else:
        # Dev mode: project root (two levels up from this file).
        data_dir = Path(__file__).resolve().parents[2]

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "attendance.db"


DB_PATH = _resolve_db_path()

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
# _SCHEMA above is the original (version 0) layout and must stay exactly as it
# is: every installed copy already has it. Changes go in _MIGRATIONS below, so
# an auto-update can bring any older database forward without losing data.

# Bump this together with a new entry in _MIGRATIONS.
SCHEMA_VERSION = 2

# Name of the class that existing students and sessions are moved into when a
# database from before classes existed (v1.4.0 and older) is upgraded.
DEFAULT_CLASS_NAME = "My Class"

# Join codes avoid look-alike characters (0/O, 1/I/L) so they can be read
# aloud or copied from a screen without mistakes.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 6


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _new_join_code(conn: sqlite3.Connection) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        taken = conn.execute(
            "SELECT 1 FROM classes WHERE join_code = ?", (code,)
        ).fetchone()
        if not taken:
            return code


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    """Add classes, class membership and sessions.class_id.

    Anything recorded before classes existed is kept and moved into one
    default class, so the instructor finds all of it under "My Class".
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS classes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            section        TEXT NOT NULL DEFAULT '',
            join_code      TEXT NOT NULL UNIQUE,
            created_at     TEXT NOT NULL,
            last_opened_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS class_students (
            class_id   INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            added_at   TEXT NOT NULL,
            PRIMARY KEY (class_id, student_id)
        )
        """
    )
    if "class_id" not in _column_names(conn, "sessions"):
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN class_id INTEGER "
            "REFERENCES classes(id) ON DELETE CASCADE"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_class ON sessions(class_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_class_students_student "
        "ON class_students(student_id)"
    )

    has_students = conn.execute("SELECT 1 FROM students LIMIT 1").fetchone()
    has_sessions = conn.execute(
        "SELECT 1 FROM sessions WHERE class_id IS NULL LIMIT 1"
    ).fetchone()
    if not (has_students or has_sessions):
        return

    first = conn.execute(
        "SELECT MIN(t) AS t FROM ("
        " SELECT MIN(created_at) AS t FROM students"
        " UNION ALL SELECT MIN(started_at) FROM sessions)"
    ).fetchone()["t"] or _now()
    last = conn.execute("SELECT MAX(started_at) AS t FROM sessions").fetchone()["t"]
    cur = conn.execute(
        "INSERT INTO classes (name, join_code, created_at, last_opened_at) "
        "VALUES (?, ?, ?, ?)",
        (DEFAULT_CLASS_NAME, _new_join_code(conn), first, last),
    )
    class_id = cur.lastrowid
    # added_at = when the student was registered, so old reports keep listing
    # exactly the students who existed at the time.
    conn.execute(
        "INSERT OR IGNORE INTO class_students (class_id, student_id, added_at) "
        "SELECT ?, id, created_at FROM students",
        (class_id,),
    )
    conn.execute("UPDATE sessions SET class_id = ? WHERE class_id IS NULL", (class_id,))


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Online registration: app settings, a per-class on/off switch, and
    registrations downloaded from the website waiting for the instructor."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    if "online" not in _column_names(conn, "classes"):
        conn.execute("ALTER TABLE classes ADD COLUMN online INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_students (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id     INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            remote_id    TEXT NOT NULL UNIQUE,
            student_no   TEXT NOT NULL,
            name         TEXT NOT NULL,
            embedding    BLOB,                  -- NULL when no usable face was found
            photo_jpeg   BLOB,                  -- small picture for the instructor to compare
            samples      INTEGER NOT NULL DEFAULT 0,
            problem      TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL,
            received_at  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_class ON pending_students(class_id)"
    )


_MIGRATIONS = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION, one version at a time.

    The version lives in SQLite's own `PRAGMA user_version`. Each step runs
    in a transaction with the version bump, so a crash halfway through leaves
    the database exactly as it was and the step simply runs again next start.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"attendance.db was created by a newer Presentia (schema {version}); "
            "update the app before opening it."
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            _MIGRATIONS[target](conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def init_db() -> None:
    conn = _connect()
    # Manual transaction control for the migrations (see _migrate).
    conn.isolation_level = None
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


# ----------------------------------------------------------------- classes

_CLASS_SELECT = """
    SELECT c.id, c.name, c.section, c.join_code, c.created_at, c.last_opened_at,
           (SELECT COUNT(*) FROM class_students cs WHERE cs.class_id = c.id)
               AS student_count,
           (SELECT COUNT(*) FROM sessions se WHERE se.class_id = c.id)
               AS session_count,
           (SELECT MAX(se.started_at) FROM sessions se WHERE se.class_id = c.id)
               AS last_session_at,
           c.online,
           (SELECT COUNT(*) FROM pending_students p WHERE p.class_id = c.id)
               AS pending_count
    FROM classes c
"""


def create_class(name: str, section: str = "") -> dict:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO classes (name, section, join_code, created_at, last_opened_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, section, _new_join_code(conn), _now(), _now()),
        )
        class_id = cur.lastrowid
    return get_class(class_id)  # type: ignore[return-value]


def list_classes() -> list[dict]:
    """Most recently used first, like the Classroom home screen."""
    with _connect() as conn:
        rows = conn.execute(
            _CLASS_SELECT
            + " ORDER BY COALESCE(c.last_opened_at, c.created_at) DESC, c.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_class(class_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(_CLASS_SELECT + " WHERE c.id = ?", (class_id,)).fetchone()
    return dict(row) if row else None


def update_class(class_id: int, name: str | None = None, section: str | None = None) -> None:
    with _connect() as conn:
        if name is not None:
            conn.execute("UPDATE classes SET name = ? WHERE id = ?", (name, class_id))
        if section is not None:
            conn.execute("UPDATE classes SET section = ? WHERE id = ?", (section, class_id))


def touch_class(class_id: int) -> None:
    """Remember that the class was opened, for the start screen's ordering."""
    with _connect() as conn:
        conn.execute(
            "UPDATE classes SET last_opened_at = ? WHERE id = ?", (_now(), class_id)
        )


def regenerate_join_code(class_id: int) -> str:
    with _connect() as conn:
        code = _new_join_code(conn)
        conn.execute("UPDATE classes SET join_code = ? WHERE id = ?", (code, class_id))
    return code


def delete_class(class_id: int) -> int:
    """Delete a class with its sessions and attendance.

    Students who belong to no other class are deleted too, face data
    included — nothing is kept for a class that no longer exists. Returns how
    many students were deleted that way.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            DELETE FROM students
            WHERE id IN (SELECT student_id FROM class_students WHERE class_id = ?)
              AND id NOT IN (SELECT student_id FROM class_students WHERE class_id <> ?)
            """,
            (class_id, class_id),
        )
        removed = cur.rowcount
        # Cascades to sessions (→ attendance, events) and class_students.
        conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
    return removed


# ---------------------------------------------------------------- students

def add_student(
    student_no: str, name: str, embedding: np.ndarray, class_id: int | None = None
) -> int:
    blob = embedding.astype(np.float32).tobytes()
    with _connect() as conn:
        now = _now()
        cur = conn.execute(
            "INSERT INTO students (student_no, name, embedding, created_at) VALUES (?, ?, ?, ?)",
            (student_no, name, blob, now),
        )
        student_id = cur.lastrowid
        if class_id is not None:
            conn.execute(
                "INSERT INTO class_students (class_id, student_id, added_at) VALUES (?, ?, ?)",
                (class_id, student_id, now),
            )
        return student_id


def find_student_by_no(student_no: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, student_no, name, created_at FROM students WHERE student_no = ?",
            (student_no,),
        ).fetchone()
    return dict(row) if row else None


def list_students(class_id: int | None = None) -> list[dict]:
    """All students, or only the roster of one class."""
    with _connect() as conn:
        if class_id is None:
            rows = conn.execute(
                "SELECT id, student_no, name, created_at FROM students ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT s.id, s.student_no, s.name, s.created_at
                FROM students s
                JOIN class_students cs ON cs.student_id = s.id
                WHERE cs.class_id = ?
                ORDER BY s.name
                """,
                (class_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def list_students_outside(class_id: int) -> list[dict]:
    """Students already registered in other classes but not in this one.

    `classes` names the classes they are in, so the instructor can tell two
    students with similar names apart before adding one.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.student_no, s.name, s.created_at,
                   COALESCE((SELECT GROUP_CONCAT(c.name, ', ')
                             FROM class_students cs JOIN classes c ON c.id = cs.class_id
                             WHERE cs.student_id = s.id), '') AS classes
            FROM students s
            WHERE s.id NOT IN (SELECT student_id FROM class_students WHERE class_id = ?)
            ORDER BY s.name
            """,
            (class_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_student(
    student_id: int, student_no: str | None = None, name: str | None = None
) -> None:
    """Correct a student's number or name. Raises sqlite3.IntegrityError if
    the number belongs to someone else."""
    with _connect() as conn:
        if student_no is not None:
            conn.execute(
                "UPDATE students SET student_no = ? WHERE id = ?", (student_no, student_id)
            )
        if name is not None:
            conn.execute("UPDATE students SET name = ? WHERE id = ?", (name, student_id))


# ------------------------------------------------- per-student attendance
#
# Which sessions count for a student: every *finished* session of the class
# held after they joined it, plus any session where they actually have a
# record (seen, or set by hand). A student who joins in week 6 is therefore
# not marked absent for weeks 1-5, and a session still running does not
# count anyone absent yet.

_CELLS_CTE = """
WITH roster AS (
    SELECT student_id, added_at FROM class_students WHERE class_id = :cid
),
sess AS (
    SELECT id, name, started_at, ended_at FROM sessions WHERE class_id = :cid
),
cells AS (
    SELECT r.student_id, se.id AS session_id,
           COALESCE(a.status, 'Absent') AS status,
           NULLIF(a.time_in, '') AS time_in,
           NULLIF(a.time_out, '') AS time_out
    FROM roster r
    CROSS JOIN sess se
    LEFT JOIN attendance a ON a.session_id = se.id AND a.student_id = r.student_id
    WHERE a.id IS NOT NULL
       OR (se.ended_at IS NOT NULL AND r.added_at <= se.ended_at)
)
"""

# Events that count as an alert in reports and the Students page.
ALERT_EVENT_TYPES = (
    "out_of_frame", "camera_off", "identity_mismatch", "liveness_failed", "suspected_still",
)
_ALERT_TYPES = "(" + ", ".join(f"'{t}'" for t in ALERT_EVENT_TYPES) + ")"


def class_attendance_summary(class_id: int) -> list[dict]:
    """One row per student on the roster with their attendance totals.

    `rate` is the share of counted sessions attended (Present or Late), or
    None when no session has counted for them yet.
    """
    with _connect() as conn:
        rows = conn.execute(
            _CELLS_CTE
            + f"""
            SELECT s.id, s.student_no, s.name,
                   r.added_at AS joined_at,
                   s.created_at AS registered_at,
                   COALESCE(SUM(c.status = 'Present'), 0) AS present,
                   COALESCE(SUM(c.status = 'Late'), 0)    AS late,
                   COALESCE(SUM(c.status = 'Absent'), 0)  AS absent,
                   COUNT(c.session_id)                    AS sessions,
                   MAX(c.time_in)                         AS last_seen,
                   (SELECT COUNT(*) FROM events e
                     WHERE e.student_id = s.id
                       AND e.session_id IN (SELECT id FROM sess)
                       AND e.event_type IN {_ALERT_TYPES}) AS alerts
            FROM roster r
            JOIN students s ON s.id = r.student_id
            LEFT JOIN cells c ON c.student_id = s.id
            GROUP BY s.id
            ORDER BY s.name
            """,
            {"cid": class_id},
        ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        attended = row["present"] + row["late"]
        row["rate"] = attended / row["sessions"] if row["sessions"] else None
        out.append(row)
    return out


def student_history(class_id: int, student_id: int) -> list[dict]:
    """Every session of the class for one student, newest first.

    `counted` is False for sessions held before the student joined (shown
    as "not enrolled yet", never as absent) and for one still running.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT se.id AS session_id, se.name, se.started_at, se.ended_at,
                   a.id AS attendance_id,
                   NULLIF(a.time_in, '') AS time_in,
                   NULLIF(a.time_out, '') AS time_out,
                   a.status AS recorded_status,
                   cs.added_at
            FROM sessions se
            JOIN class_students cs ON cs.class_id = se.class_id AND cs.student_id = :stid
            LEFT JOIN attendance a ON a.session_id = se.id AND a.student_id = :stid
            WHERE se.class_id = :cid
            ORDER BY se.started_at DESC, se.id DESC
            """,
            {"cid": class_id, "stid": student_id},
        ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        counted = row["attendance_id"] is not None or (
            row["ended_at"] is not None and row["added_at"] <= row["ended_at"]
        )
        row["counted"] = counted
        row["status"] = (row["recorded_status"] or "Absent") if counted else None
        del row["recorded_status"], row["added_at"]
        out.append(row)
    return out


def class_attendance_matrix(class_id: int) -> tuple[list[dict], dict[int, dict[int, str]]]:
    """Sessions in date order and, per student, their status in each.

    Returns (sessions, cells) where cells[student_id][session_id] is
    'Present' / 'Late' / 'Absent'. Sessions that do not count for a student
    are simply missing from their dict.
    """
    with _connect() as conn:
        sessions = [
            dict(r) for r in conn.execute(
                "SELECT id, name, started_at, ended_at FROM sessions "
                "WHERE class_id = ? ORDER BY started_at, id",
                (class_id,),
            )
        ]
        cells: dict[int, dict[int, str]] = {}
        for r in conn.execute(
            _CELLS_CTE + " SELECT student_id, session_id, status FROM cells",
            {"cid": class_id},
        ):
            cells.setdefault(r["student_id"], {})[r["session_id"]] = r["status"]
    return sessions, cells


def add_student_to_class(class_id: int, student_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO class_students (class_id, student_id, added_at) "
            "VALUES (?, ?, ?)",
            (class_id, student_id, _now()),
        )


def remove_student_from_class(class_id: int, student_id: int) -> bool:
    """Take a student off a class roster.

    If that was their last class the student and their face data are deleted
    entirely. Returns True when that happened.
    """
    with _connect() as conn:
        conn.execute(
            "DELETE FROM class_students WHERE class_id = ? AND student_id = ?",
            (class_id, student_id),
        )
        others = conn.execute(
            "SELECT 1 FROM class_students WHERE student_id = ? LIMIT 1", (student_id,)
        ).fetchone()
        if others:
            return False
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
        return True


def get_student_embedding(student_id: int) -> np.ndarray | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT embedding FROM students WHERE id = ?", (student_id,)
        ).fetchone()
    if row is None:
        return None
    return np.frombuffer(row["embedding"], dtype=np.float32)


def all_embeddings(class_id: int | None = None) -> list[tuple[int, np.ndarray]]:
    """Face embeddings to match against — only one class's roster if given,
    so a face is never recognised as a student from a different class."""
    with _connect() as conn:
        if class_id is None:
            rows = conn.execute("SELECT id, embedding FROM students").fetchall()
        else:
            rows = conn.execute(
                "SELECT s.id, s.embedding FROM students s "
                "JOIN class_students cs ON cs.student_id = s.id WHERE cs.class_id = ?",
                (class_id,),
            ).fetchall()
    return [(r["id"], np.frombuffer(r["embedding"], dtype=np.float32)) for r in rows]


def delete_student(student_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))


# ---------------------------------------------------------------- sessions

def create_session(name: str, class_id: int | None = None) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (name, started_at, class_id) VALUES (?, ?, ?)",
            (name, _now(), class_id),
        )
        if class_id is not None:
            conn.execute(
                "UPDATE classes SET last_opened_at = ? WHERE id = ?", (_now(), class_id)
            )
        return cur.lastrowid


def end_session(session_id: int) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (now, session_id),
        )
        # Only close out rows the system actually observed; rows created by a
        # manual status override carry an empty time_in and no timestamps.
        conn.execute(
            "UPDATE attendance SET time_out = ? "
            "WHERE session_id = ? AND time_out IS NULL AND time_in <> ''",
            (now, session_id),
        )


def list_sessions(class_id: int | None = None) -> list[dict]:
    with _connect() as conn:
        if class_id is None:
            rows = conn.execute(
                "SELECT id, name, started_at, ended_at, class_id FROM sessions "
                "ORDER BY id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, started_at, ended_at, class_id FROM sessions "
                "WHERE class_id = ? ORDER BY id DESC",
                (class_id,),
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


def clear_time_out(session_id: int, student_id: int) -> None:
    """Undo a provisional departure when the student comes back on screen."""
    with _connect() as conn:
        conn.execute(
            "UPDATE attendance SET time_out = NULL "
            "WHERE session_id = ? AND student_id = ?",
            (session_id, student_id),
        )


def set_status(attendance_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE attendance SET status = ? WHERE id = ?", (status, attendance_id))


def set_student_status(session_id: int, student_id: int, status: str) -> int:
    """Set a student's status for a session, creating the row if needed.

    Students who were never recognized have no attendance row at all, so
    marking them Present/Late by hand used to be impossible. An empty
    `time_in` marks a row the system did not observe — the UI renders it
    as a dash.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO attendance (session_id, student_id, time_in, status) "
            "VALUES (?, ?, '', ?)",
            (session_id, student_id, status),
        )
        conn.execute(
            "UPDATE attendance SET status = ? WHERE session_id = ? AND student_id = ?",
            (status, session_id, student_id),
        )
        row = conn.execute(
            "SELECT id FROM attendance WHERE session_id = ? AND student_id = ?",
            (session_id, student_id),
        ).fetchone()
    return int(row["id"]) if row else 0


def session_report(session_id: int) -> list[dict]:
    """Attendance for every student on the class roster, seen or not.

    Students the system never recognized come back with no timestamps and
    status 'Absent' so instructors can review and override the full class
    list rather than only the students who happened to be detected.

    The roster is the session's class as it was during that session: a
    student who joined the class later is not listed as absent from earlier
    meetings, and one who left it still shows up where they have a record.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT a.id AS attendance_id, s.id AS student_id, s.student_no, s.name,
                   a.time_in, a.time_out,
                   COALESCE(a.status, 'Absent') AS status,
                   (SELECT COUNT(*) FROM events e
                     WHERE e.session_id = :sid AND e.student_id = s.id
                       AND e.event_type IN {_ALERT_TYPES}
                   ) AS alert_count
            FROM students s
            LEFT JOIN attendance a
                   ON a.student_id = s.id AND a.session_id = :sid
            WHERE a.id IS NOT NULL
               OR (SELECT class_id FROM sessions WHERE id = :sid) IS NULL
               OR EXISTS (
                    SELECT 1 FROM class_students cs
                    JOIN sessions se ON se.id = :sid
                    WHERE cs.student_id = s.id
                      AND cs.class_id = se.class_id
                      AND (se.ended_at IS NULL OR cs.added_at <= se.ended_at)
               )
            ORDER BY (a.time_in IS NULL OR a.time_in = ''), a.time_in, s.name
            """,
            {"sid": session_id},
        ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        # An empty string time_in means "row created by a manual override",
        # not an observed sighting.
        if not row["time_in"]:
            row["time_in"] = None
        if not row["time_out"]:
            row["time_out"] = None
        out.append(row)
    return out


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


# ------------------------------------------------------------ app settings

def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str | None) -> None:
    with _connect() as conn:
        if value is None:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


# ------------------------------------------------ online registration

def set_class_online(class_id: int, online: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE classes SET online = ? WHERE id = ?", (1 if online else 0, class_id))


def set_join_code(class_id: int, code: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE classes SET join_code = ? WHERE id = ?", (code, class_id))


def new_join_code() -> str:
    with _connect() as conn:
        return _new_join_code(conn)


def add_pending(
    class_id: int, remote_id: str, student_no: str, name: str,
    embedding: np.ndarray | None, photo_jpeg: bytes | None, samples: int,
    problem: str, submitted_at: str,
) -> bool:
    """Store a downloaded registration. False if it was already stored."""
    blob = embedding.astype(np.float32).tobytes() if embedding is not None else None
    with _connect() as conn:
        # A newer submission from the same student replaces the waiting one.
        conn.execute(
            "DELETE FROM pending_students WHERE class_id = ? AND student_no = ?",
            (class_id, student_no),
        )
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO pending_students
                (class_id, remote_id, student_no, name, embedding, photo_jpeg,
                 samples, problem, submitted_at, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (class_id, remote_id, student_no, name, blob, photo_jpeg,
             samples, problem, submitted_at, _now()),
        )
        return cur.rowcount > 0


def list_pending(class_id: int) -> list[dict]:
    """Waiting registrations, with whether the student number is already
    known (and in this class) so the instructor sees what approving does."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.student_no, p.name, p.photo_jpeg, p.samples, p.problem,
                   p.submitted_at, p.received_at, p.embedding IS NOT NULL AS has_face,
                   s.id AS existing_id, s.name AS existing_name,
                   EXISTS (SELECT 1 FROM class_students cs
                           WHERE cs.class_id = p.class_id AND cs.student_id = s.id)
                       AS existing_in_class
            FROM pending_students p
            LEFT JOIN students s ON s.student_no = p.student_no
            WHERE p.class_id = ?
            ORDER BY p.submitted_at
            """,
            (class_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending(pending_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pending_students WHERE id = ?", (pending_id,)).fetchone()
    return dict(row) if row else None


def delete_pending(pending_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM pending_students WHERE id = ?", (pending_id,))

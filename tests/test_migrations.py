"""Auto-updates upgrade the database in place, so old data must survive."""

import sqlite3

import pytest

from conftest import embedding


def _make_v0_database(path):
    """A database as v1.4.0 and older left it."""
    from app.data import db

    conn = sqlite3.connect(path)
    conn.executescript(db._SCHEMA)
    conn.execute(
        "INSERT INTO students (student_no, name, embedding, created_at) VALUES (?,?,?,?)",
        ("2021-0001", "Old Student", embedding(5).tobytes(), "2026-06-01 08:00:00"),
    )
    conn.execute(
        "INSERT INTO sessions (name, started_at, ended_at) VALUES (?,?,?)",
        ("Old session", "2026-06-02 08:00:00", "2026-06-02 09:00:00"),
    )
    conn.execute(
        "INSERT INTO attendance (session_id, student_id, time_in, status) VALUES (1, 1, ?, 'Present')",
        ("2026-06-02 08:01:00",),
    )
    conn.commit()
    conn.close()


def test_v0_data_moves_into_my_class(tmp_path, monkeypatch):
    from app.data import db

    path = tmp_path / "attendance.db"
    _make_v0_database(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    classes = db.list_classes()
    assert [c["name"] for c in classes] == [db.DEFAULT_CLASS_NAME]
    cid = classes[0]["id"]
    assert [s["student_no"] for s in db.list_students(cid)] == ["2021-0001"]
    assert [s["name"] for s in db.list_sessions(cid)] == ["Old session"]
    summary = db.class_attendance_summary(cid)
    assert summary[0]["present"] == 1

    with db._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_empty_v0_database_gets_no_default_class(tmp_path, monkeypatch):
    from app.data import db

    path = tmp_path / "attendance.db"
    conn = sqlite3.connect(path)
    conn.executescript(db._SCHEMA)
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    assert db.list_classes() == []


def test_newer_database_is_refused(tmp_path, monkeypatch):
    from app.data import db

    path = tmp_path / "attendance.db"
    conn = sqlite3.connect(path)
    conn.executescript(db._SCHEMA)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    with pytest.raises(RuntimeError):
        db.init_db()

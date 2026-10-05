"""Classes, rosters and attendance totals."""

from conftest import embedding


def test_fresh_database_is_at_current_schema(fresh_db):
    db = fresh_db
    with db._connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    db.init_db()  # running again on every start must be harmless
    assert db.list_classes() == []


def test_class_crud(fresh_db):
    db = fresh_db
    cls = db.create_class("IT 301", "BSIT 3A")
    assert cls["name"] == "IT 301" and cls["section"] == "BSIT 3A"
    code = cls["join_code"]
    assert len(code) == 6 and set(code) <= set(db._CODE_ALPHABET)

    db.update_class(cls["id"], name="IT 302")
    assert db.get_class(cls["id"])["name"] == "IT 302"

    new_code = db.regenerate_join_code(cls["id"])
    assert new_code != code and db.get_class(cls["id"])["join_code"] == new_code

    db.delete_class(cls["id"])
    assert db.get_class(cls["id"]) is None


def test_join_codes_are_unique(fresh_db):
    codes = {fresh_db.create_class(f"Class {i}")["join_code"] for i in range(40)}
    assert len(codes) == 40


def test_student_on_two_rosters_is_stored_once(fresh_db):
    db = fresh_db
    a = db.create_class("A")
    b = db.create_class("B")
    sid = db.add_student("2023-0001", "Ana Cruz", embedding(1), a["id"])
    db.add_student_to_class(b["id"], sid)

    assert [s["id"] for s in db.list_students(a["id"])] == [sid]
    assert [s["id"] for s in db.list_students(b["id"])] == [sid]
    assert db.find_student_by_no("2023-0001")["id"] == sid
    assert db.list_students_outside(a["id"]) == []

    db.remove_student_from_class(a["id"], sid)
    assert db.list_students(a["id"]) == []
    assert [s["id"] for s in db.list_students(b["id"])] == [sid]


def test_attendance_summary_counts(fresh_db):
    db = fresh_db
    cls = db.create_class("A")
    ana = db.add_student("1", "Ana", embedding(1), cls["id"])
    ben = db.add_student("2", "Ben", embedding(2), cls["id"])

    statuses = [("Present", "Absent"), ("Late", "Present"), ("Present", "Absent")]
    for i, (sa, sb) in enumerate(statuses):
        sess = db.create_session(f"Week {i + 1}", cls["id"])
        db.set_student_status(sess, ana, sa)
        db.set_student_status(sess, ben, sb)
        db.end_session(sess)

    rows = {r["id"]: r for r in db.class_attendance_summary(cls["id"])}
    assert (rows[ana]["present"], rows[ana]["late"], rows[ana]["absent"]) == (2, 1, 0)
    assert rows[ana]["rate"] == 1.0  # late still counts as attended
    assert (rows[ben]["present"], rows[ben]["absent"]) == (1, 2)
    assert abs(rows[ben]["rate"] - 1 / 3) < 1e-9

    history = db.student_history(cls["id"], ben)
    assert len(history) == 3


def test_settings_roundtrip(fresh_db):
    db = fresh_db
    assert db.get_setting("x") is None
    db.set_setting("x", "1")
    assert db.get_setting("x") == "1"
    db.set_setting("x", None)
    assert db.get_setting("x") is None

"""Phone app sync (app/data/sync.py) against a stand-in for Supabase that
follows the same rules as supabase/migrations/0001_presentia.sql: the
newest change wins, deleting wipes the data, and only an undecided
registration can be decided."""

import base64
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from conftest import embedding, engine_client


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, dict[str, dict]] = {}
        self.clock = datetime(2026, 10, 29, tzinfo=timezone.utc)
        self.writes = 0

    def _stamp(self, row):
        self.clock += timedelta(milliseconds=1)
        row["server_at"] = self.clock.isoformat()

    def _store(self, table, row):
        rows = self.tables.setdefault(table, {})
        old = rows.get(row["id"])
        if old and (old["deleted"] or (row.get("updated_at", old["updated_at"]) < old["updated_at"]
                                       and not row.get("deleted"))):
            return None   # deleted stays deleted; an older change arriving late is ignored
        new = {**(old or {"deleted": False, "decision": None, "decided_by": None}), **row}
        if new["deleted"]:
            new.update({k: None for k in ("embedding", "photo_jpeg") if k in new}, name="")
        self._stamp(new)
        rows[row["id"]] = new
        return new

    def phone_writes(self, table, row):
        """A change made on the phone (its clock is a little ahead)."""
        ts = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return self._store(table, {"updated_at": ts, **row})

    def phone_decides(self, uid, decision):
        """The phone's Accept / Decline: only while undecided, clock not involved."""
        row = self.tables["pending_students"][uid]
        if row["decision"] is None and not row["deleted"]:
            self._store("pending_students", {"id": uid, "decision": decision, "decided_by": "phone"})

    def __call__(self, method, path, body=None, auth=True, prefer=""):
        url = urllib.parse.urlparse(path)
        table = url.path.rsplit("/", 1)[1]
        query = dict(urllib.parse.parse_qsl(url.query))
        if table == "presentia_changes":
            since = body["since"]
            after = datetime.min.replace(tzinfo=timezone.utc) if since == "-infinity" else datetime.fromisoformat(since)
            return {t: [dict(r) for r in rows.values() if datetime.fromisoformat(r["server_at"]) > after]
                    for t, rows in self.tables.items()}
        if method == "POST":
            self.writes += 1
            for row in body:
                self._store(table, row)
            return None
        ids = re.findall(r"[0-9a-f-]{36}", query.get("id", ""))
        rows = [r for i, r in self.tables.get(table, {}).items() if i in ids]
        if query.get("decision") == "is.null":
            rows = [r for r in rows if r["decision"] is None and not r["deleted"]]
        if method == "GET":
            return [dict(r) for r in rows]
        self.writes += 1
        out = [self._store(table, {**body, "id": r["id"]}) for r in rows]
        return [r for r in out if r] if "representation" in prefer else None


@pytest.fixture
def cloud(fresh_db, monkeypatch):
    from app.data import sync

    fake = FakeSupabase()
    monkeypatch.setenv("PRESENTIA_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(sync, "_http", fake)
    fresh_db.set_setting(sync._K_REFRESH, "refresh")   # signed in
    return fake


@pytest.fixture
def api(cloud):
    from app import sidecar

    return engine_client(), sidecar._sync_now, cloud


def test_local_data_is_uploaded_once(api, fresh_db):
    client, sync_now, cloud = api
    cls = fresh_db.create_class("IT 301")
    sid = fresh_db.add_student("2024-001", "Juan", embedding(1), cls["id"])
    sync_now()
    student = next(iter(cloud.tables["students"].values()))
    link = next(iter(cloud.tables["class_students"].values()))
    assert student["name"] == "Juan" and len(base64.b64decode(student["embedding"])) == 512 * 4
    assert link["class_id"] == next(iter(cloud.tables["classes"]))   # local ids became UUIDs
    writes = cloud.writes
    sync_now()
    assert cloud.writes == writes   # nothing changed, nothing sent

    fresh_db.update_student(sid, name="Juan Dela Cruz")
    sync_now()
    assert cloud.tables["students"][student["id"]]["name"] == "Juan Dela Cruz"


def test_phone_attendance_comes_down(api, fresh_db):
    client, sync_now, cloud = api
    cls = fresh_db.create_class("IT 301")
    sid = fresh_db.add_student("2024-001", "Juan", embedding(1), cls["id"])
    sync_now()
    class_uid = next(iter(cloud.tables["classes"]))
    student_uid = next(iter(cloud.tables["students"]))
    session = "5e55e55e-0000-4000-8000-000000000001"
    cloud.phone_writes("sessions", {"id": session, "class_id": class_uid, "name": "In person",
                                    "started_at": "2026-10-29 08:00:00", "ended_at": None, "deleted": False})
    cloud.phone_writes("attendance", {"id": "a77e0da0-0000-4000-8000-000000000001", "session_id": session,
                                      "student_id": student_uid, "time_in": "2026-10-29 08:01:00",
                                      "time_out": None, "status": "Late", "deleted": False})
    sync_now()
    [s] = fresh_db.list_sessions(cls["id"])
    report = fresh_db.session_report(s["id"])
    assert [(r["student_id"], r["status"]) for r in report if r["status"]] == [(sid, "Late")]
    writes = cloud.writes
    sync_now()
    assert cloud.writes == writes   # what came down is not sent back up


def test_deletes_go_both_ways(api, fresh_db):
    client, sync_now, cloud = api
    a = fresh_db.create_class("A")
    b = fresh_db.create_class("B")
    juan = fresh_db.add_student("2024-001", "Juan", embedding(1), a["id"])
    fresh_db.add_student("2024-002", "Ana", embedding(2), b["id"])
    sync_now()
    fresh_db.delete_class(a["id"])   # also deletes Juan, who is in no other class
    sync_now()
    gone = [r for r in cloud.tables["students"].values() if r["deleted"]]
    assert len(gone) == 1 and gone[0]["embedding"] is None and gone[0]["name"] == ""
    assert fresh_db.get_student(juan) is None

    ana_uid = next(i for i, r in cloud.tables["students"].items() if not r["deleted"])
    cloud.phone_writes("students", {"id": ana_uid, "deleted": True})
    sync_now()
    assert fresh_db.list_students(b["id"]) == []


def _pending(db, class_id, no="2024-009", seed=9):
    db.add_pending(class_id, f"remote-{no}", no, "Maria", embedding(seed), b"jpg", 3, "", "2026-10-29 08:00:00")
    return next(p for p in db.list_pending(class_id) if p["student_no"] == no)


def test_registration_accepted_on_the_phone_is_added_here(api, fresh_db):
    client, sync_now, cloud = api
    cls = fresh_db.create_class("IT 301")
    p = _pending(fresh_db, cls["id"])
    sync_now()
    [remote] = cloud.tables["pending_students"].values()
    assert remote["action"] == "new" and base64.b64decode(remote["photo_jpeg"]) == b"jpg"

    cloud.phone_decides(remote["id"], "accepted")
    sync_now()
    assert [s["name"] for s in fresh_db.list_students(cls["id"])] == ["Maria"]
    assert fresh_db.get_pending(p["id"]) is None
    sync_now()
    assert cloud.tables["pending_students"][remote["id"]]["deleted"]   # gone from the phone too


def test_first_decision_wins(api, fresh_db):
    client, sync_now, cloud = api
    cls = fresh_db.create_class("IT 301")
    p = _pending(fresh_db, cls["id"])
    sync_now()
    [remote] = cloud.tables["pending_students"].values()
    cloud.phone_decides(remote["id"], "declined")

    res = client.post(f"/api/pending/{p['id']}/approve")   # the desktop, a moment later
    assert res.status_code == 409 and "declined on your phone" in res.json()["detail"]["message"]
    assert fresh_db.list_students(cls["id"]) == []

    q = _pending(fresh_db, cls["id"], "2024-010", seed=10)
    sync_now()
    assert client.post(f"/api/pending/{q['id']}/approve").status_code == 200
    remote_q = next(r for r in cloud.tables["pending_students"].values() if r["student_no"] == "2024-010")
    assert remote_q["decision"] == "accepted" and remote_q["decided_by"] == "desktop"


def test_phone_cannot_accept_a_taken_student_number(api, fresh_db):
    client, sync_now, cloud = api
    cls = fresh_db.create_class("IT 301")
    fresh_db.add_student("2024-009", "Ana", embedding(1), cls["id"])
    p = _pending(fresh_db, cls["id"], "2024-009", seed=9)   # Ana's number, someone else's face
    sync_now()
    [remote] = cloud.tables["pending_students"].values()
    assert remote["action"] == "number_taken"
    cloud.phone_decides(remote["id"], "accepted")
    sync_now()
    remote = cloud.tables["pending_students"][remote["id"]]
    assert remote["decision"] is None and "belongs to Ana" in remote["action_note"]
    assert fresh_db.get_pending(p["id"]) is not None


def test_signed_out_nothing_is_claimed(fresh_db):
    from app.data import sync

    assert not sync.signed_in()
    client = engine_client()
    cls = fresh_db.create_class("IT 301")
    p = _pending(fresh_db, cls["id"])
    assert client.post(f"/api/pending/{p['id']}/approve").status_code == 200


def test_account_sign_up_sign_in_and_reset(fresh_db, monkeypatch):
    from app.data import sync

    sent = []
    session = {"access_token": "a", "refresh_token": "r", "expires_in": 3600,
               "user": {"id": "u1", "email": "t@school.edu"}}

    def fake(method, path, body=None, **kw):
        sent.append((method, path.split("?")[0], body))
        return {"id": "u1"} if path == "/auth/v1/signup" else session   # sign-up: confirm by code

    monkeypatch.setenv("PRESENTIA_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(sync, "_http", fake)

    assert sync.sign_up("t@school.edu", "secret-pass")["needs_code"] and not sync.signed_in()
    sync.confirm("t@school.edu", " 123456 ")
    assert sync.signed_in() and sync.status()["email"] == "t@school.edu"
    sync.sign_out()
    # The emailed link pasted whole works like the code (template still sends a link).
    sync.confirm("t@school.edu", "https://example.supabase.co/auth/v1/verify?token=ab12cd&type=signup"
                                 "&redirect_to=https://presentia.example")
    sync.sign_in("t@school.edu", "secret-pass")
    sync.reset_password("t@school.edu", "654321", "new-secret")
    assert [(m, p, b) for m, p, b in sent if p != "/auth/v1/logout"] == [
        ("POST", "/auth/v1/signup", {"email": "t@school.edu", "password": "secret-pass"}),
        ("POST", "/auth/v1/verify", {"type": "signup", "email": "t@school.edu", "token": "123456"}),
        ("POST", "/auth/v1/verify", {"type": "signup", "token_hash": "ab12cd"}),
        ("POST", "/auth/v1/token", {"email": "t@school.edu", "password": "secret-pass"}),
        ("POST", "/auth/v1/verify", {"type": "recovery", "email": "t@school.edu", "token": "654321"}),
        ("PUT", "/auth/v1/user", {"password": "new-secret"}),
    ]
    assert sync.signed_in()


def test_a_failed_password_change_leaves_you_signed_out(fresh_db, monkeypatch):
    from app.data import sync

    session = {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "user": {"id": "u1"}}

    def fake(method, path, body=None, **kw):
        if method == "PUT":
            raise sync.SyncError("Password is too weak.", 422)
        return session

    monkeypatch.setenv("PRESENTIA_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(sync, "_http", fake)
    with pytest.raises(sync.SyncError):
        sync.reset_password("t@school.edu", "654321", "x")
    assert not sync.signed_in()


def test_account_endpoints_check_input(fresh_db):
    client = engine_client()
    assert client.post("/api/account/sign-up", json={"email": "t@school.edu", "password": "short"}).status_code == 400
    assert client.post("/api/account/sign-in", json={"email": "nope", "password": "x" * 8}).status_code == 400
    assert client.post("/api/account/reset", json={"email": "t@school.edu", "password": "x" * 8}).status_code == 400


def test_terms_acceptance_is_recorded(fresh_db):
    client = engine_client()
    first = client.get("/api/setup").json()
    assert first["terms_version"] is None and first["has_data"] is False
    fresh_db.create_class("IT 301")
    out = client.post("/api/setup/accept", json={"version": "2026-10-30"}).json()
    assert out["terms_version"] == "2026-10-30" and out["terms_accepted_at"] and out["has_data"]
    fresh_db.delete_all_data()
    assert client.get("/api/setup").json()["terms_version"] == "2026-10-30"   # consent record kept

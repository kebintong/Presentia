"""Settings → Data → Delete all data."""

import base64

from fastapi.testclient import TestClient

from conftest import embedding


def test_delete_all_data(fresh_db, monkeypatch):
    from app import sidecar
    from app.data import cloud

    db = fresh_db
    c = TestClient(sidecar.app)
    cls = c.post("/api/classes", json={"name": "IT 301"}).json()
    face = embedding(7)
    c.post("/api/students", json={"student_no": "2023-0001", "name": "Ana",
                                  "embedding_b64": base64.b64encode(face.tobytes()).decode(),
                                  "class_id": cls["id"]})
    sid = db.find_student_by_no("2023-0001")["id"]
    sess = db.create_session("Week 1", cls["id"])
    db.set_student_status(sess, sid, "Present")
    db.log_event(sess, sid, "out_of_frame", "left")
    db.add_pending(cls["id"], "r1", "2023-0002", "Ben", embedding(8), b"jpg", 3, "", "2026-10-05 10:00:00")
    db.set_class_online(cls["id"], True)
    db.set_setting("cloud_url", "https://example.workers.dev")

    unpublished = []
    monkeypatch.setattr(cloud, "unpublish", lambda code: unpublished.append(code))

    summary = c.get("/api/data/summary").json()
    assert (summary["classes"], summary["students"], summary["sessions"], summary["pending"]) == (1, 1, 1, 1)

    assert c.post("/api/data/delete-all", json={"confirm": "yes"}).status_code == 400
    assert db.list_classes() != []

    out = c.post("/api/data/delete-all", json={"confirm": "DELETE"}).json()
    assert out["deleted"]["students"] == 1 and out["website_problems"] == []
    assert unpublished == [cls["join_code"]]

    after = c.get("/api/data/summary").json()
    assert all(after[k] == 0 for k in ("classes", "students", "sessions", "attendance", "pending"))
    assert db.get_setting("cloud_url") == "https://example.workers.dev"   # settings stay

    # The face template is gone from the file itself, not just unlinked.
    raw = db.DB_PATH.read_bytes()
    assert face.tobytes()[:64] not in raw

    # The app still works afterwards.
    assert c.post("/api/classes", json={"name": "New"}).status_code == 201

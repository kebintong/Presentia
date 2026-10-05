"""One face, one student: the same person cannot register again under a
different student number, in person or through the website."""

import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import embedding


def b64(v: np.ndarray) -> str:
    return base64.b64encode(v.astype(np.float32).tobytes()).decode()


def near(v: np.ndarray, seed: int, similarity: float = 0.7) -> np.ndarray:
    """Another face template with the given cosine similarity to `v`.
    Two photos of one person typically score 0.6-0.8."""
    n = np.random.default_rng(seed).standard_normal(v.size).astype(np.float32)
    n -= (n @ v) * v                 # orthogonal to v
    n /= np.linalg.norm(n)
    w = similarity * v + np.sqrt(1 - similarity**2) * n
    return (w / np.linalg.norm(w)).astype(np.float32)


@pytest.fixture
def api(fresh_db):
    from app import sidecar

    client = TestClient(sidecar.app)
    cls = client.post("/api/classes", json={"name": "IT 301"}).json()
    return client, cls, fresh_db


def test_same_face_new_number_is_refused(api):
    client, cls, _ = api
    ana = embedding(1)
    assert client.post("/api/students", json={
        "student_no": "2023-0001", "name": "Ana", "embedding_b64": b64(ana), "class_id": cls["id"],
    }).status_code == 201

    res = client.post("/api/students", json={
        "student_no": "2023-9999", "name": "Ana Again", "embedding_b64": b64(near(ana, 7)),
        "class_id": cls["id"],
    })
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "face_exists" and detail["student"]["student_no"] == "2023-0001"


def test_different_faces_are_fine(api):
    client, cls, _ = api
    for i in range(5):
        res = client.post("/api/students", json={
            "student_no": f"N{i}", "name": f"S{i}", "embedding_b64": b64(embedding(100 + i)),
            "class_id": cls["id"],
        })
        assert res.status_code == 201, res.text


def test_lookalike_below_threshold_is_allowed(api):
    client, cls, _ = api
    from app.sidecar import DUPLICATE_FACE

    ana = embedding(1)
    client.post("/api/students", json={
        "student_no": "1", "name": "Ana", "embedding_b64": b64(ana), "class_id": cls["id"],
    })
    res = client.post("/api/students", json={
        "student_no": "2", "name": "Sister", "class_id": cls["id"],
        "embedding_b64": b64(near(ana, 5, DUPLICATE_FACE - 0.05)),
    })
    assert res.status_code == 201
    res = client.post("/api/students", json={
        "student_no": "3", "name": "Ana Again", "class_id": cls["id"],
        "embedding_b64": b64(near(ana, 6, DUPLICATE_FACE + 0.05)),
    })
    assert res.status_code == 409


def test_same_face_already_in_class_cannot_be_added_again(api):
    client, cls, db = api
    ana = embedding(1)
    client.post("/api/students", json={
        "student_no": "2023-0001", "name": "Ana", "embedding_b64": b64(ana), "class_id": cls["id"],
    })
    db.add_pending(cls["id"], "r1", "2023-5555", "Fake Ben", near(ana, 3), None, 3, "", "2026-10-05 10:00:00")

    [p] = client.get(f"/api/classes/{cls['id']}/pending").json()
    assert p["action"] == "in_class" and p["match"]["student_no"] == "2023-0001" and p["via"] == "face"
    assert "embedding" not in p

    res = client.post(f"/api/pending/{p['id']}/approve")
    assert res.status_code == 409 and res.json()["detail"]["code"] == "already_in_class"
    assert len(client.get(f"/api/classes/{cls['id']}/students/summary").json()) == 1


def test_student_joins_another_class_with_saved_data(api):
    """Google Classroom style: Clark is in Class 1 and registers for Class 2.
    He is added with his saved face, never stored twice — even if he types
    a different student number."""
    client, cls1, db = api
    cls2 = client.post("/api/classes", json={"name": "Class 2"}).json()
    cls3 = client.post("/api/classes", json={"name": "Class 3"}).json()
    clark = embedding(42)
    client.post("/api/students", json={
        "student_no": "2023-0042", "name": "Clark", "embedding_b64": b64(clark), "class_id": cls1["id"],
    })

    # Same number
    db.add_pending(cls2["id"], "r1", "2023-0042", "Clark", near(clark, 1), None, 3, "", "2026-10-05 10:00:00")
    [p] = client.get(f"/api/classes/{cls2['id']}/pending").json()
    assert p["action"] == "join" and p["via"] == "number"
    assert client.post(f"/api/pending/{p['id']}/approve").json()["result"] == "linked"

    # Different number (typo): recognised by face, saved number kept
    db.add_pending(cls3["id"], "r2", "2023-0024", "Clark", near(clark, 2), None, 3, "", "2026-10-05 10:00:00")
    [p] = client.get(f"/api/classes/{cls3['id']}/pending").json()
    assert p["action"] == "join" and p["via"] == "face" and p["match"]["student_no"] == "2023-0042"
    out = client.post(f"/api/pending/{p['id']}/approve").json()
    assert out["result"] == "linked" and out["student_id"] == p["match"]["id"]

    assert len(db.list_students()) == 1  # one Clark ...
    for c in (cls1, cls2, cls3):         # ... in three classes
        assert [s["name"] for s in db.list_students(c["id"])] == ["Clark"]

    # Registering again for a class he is in: refused, still one Clark there.
    db.add_pending(cls2["id"], "r3", "2023-0042", "Clark", near(clark, 5), None, 3, "", "2026-10-05 11:00:00")
    [p] = client.get(f"/api/classes/{cls2['id']}/pending").json()
    assert p["action"] == "in_class"
    assert client.post(f"/api/pending/{p['id']}/approve").status_code == 409


def test_number_of_one_student_face_of_another_is_a_conflict(api):
    client, cls, db = api
    ana, ben = embedding(1), embedding(2)
    other = client.post("/api/classes", json={"name": "Other"}).json()
    for no, name, e in (("A-1", "Ana", ana), ("B-1", "Ben", ben)):
        client.post("/api/students", json={"student_no": no, "name": name, "embedding_b64": b64(e),
                                           "class_id": other["id"]})
    db.add_pending(cls["id"], "r1", "A-1", "Ana", near(ben, 3), None, 3, "", "2026-10-05 10:00:00")
    [p] = client.get(f"/api/classes/{cls['id']}/pending").json()
    assert p["action"] == "conflict" and p["conflict_with"]["name"] == "Ben"
    res = client.post(f"/api/pending/{p['id']}/approve")
    assert res.status_code == 409 and res.json()["detail"]["code"] == "conflict"


def test_two_waiting_registrations_with_one_face(api):
    client, cls, db = api
    face = embedding(2)
    db.add_pending(cls["id"], "r1", "A-1", "Carl", face, None, 3, "", "2026-10-05 10:00:00")
    db.add_pending(cls["id"], "r2", "A-2", "Carl 2", near(face, 4), None, 3, "", "2026-10-05 10:01:00")
    db.add_pending(cls["id"], "r3", "A-3", "Dana", embedding(3), None, 3, "", "2026-10-05 10:02:00")

    rows = {p["student_no"]: p for p in client.get(f"/api/classes/{cls['id']}/pending").json()}
    assert rows["A-1"]["pending_match"]["student_no"] == "A-2"
    assert rows["A-2"]["pending_match"]["student_no"] == "A-1"
    assert rows["A-3"]["pending_match"] is None and rows["A-3"]["action"] == "new"

    # Accepting one makes the other "already in this class".
    assert client.post(f"/api/pending/{rows['A-1']['id']}/approve").status_code == 200
    left = {p["student_no"]: p for p in client.get(f"/api/classes/{cls['id']}/pending").json()}
    assert left["A-2"]["action"] == "in_class" and left["A-2"]["match"]["student_no"] == "A-1"
    assert client.post(f"/api/pending/{left['A-2']['id']}/approve").status_code == 409


def test_known_number_with_a_different_face_is_blocked(api):
    """Ben uses Ana's student number. Ana is in another class, so this used
    to look like Ana joining; now it is refused."""
    client, cls, db = api
    other = client.post("/api/classes", json={"name": "Other"}).json()
    client.post("/api/students", json={
        "student_no": "2023-0001", "name": "Ana", "embedding_b64": b64(embedding(1)), "class_id": other["id"],
    })
    db.add_pending(cls["id"], "r1", "2023-0001", "Ben", embedding(9), None, 3, "", "2026-10-05 10:00:00")
    [p] = client.get(f"/api/classes/{cls['id']}/pending").json()
    assert p["action"] == "number_taken" and p["match"]["name"] == "Ana" and p["face_differs"] is True
    res = client.post(f"/api/pending/{p['id']}/approve")
    assert res.status_code == 409 and res.json()["detail"]["code"] == "number_taken"
    assert db.list_students(cls["id"]) == []


def test_retake_replaces_but_another_person_does_not(api):
    """Same number twice in the app's waiting list: a retake (same face)
    replaces; a different face is kept and both are flagged."""
    client, cls, db = api
    ana, ben = embedding(1), embedding(2)
    db.add_pending(cls["id"], "r1", "2023-0007", "Ana", ana, None, 3, "", "2026-10-05 10:00:00")
    db.add_pending(cls["id"], "r2", "2023-0007", "Ana (retake)", near(ana, 3), None, 3, "", "2026-10-05 10:01:00")
    rows = client.get(f"/api/classes/{cls['id']}/pending").json()
    assert [r["name"] for r in rows] == ["Ana (retake)"] and rows[0]["number_clash"] is None

    db.add_pending(cls["id"], "r3", "2023-0007", "Ben", ben, None, 3, "", "2026-10-05 10:02:00")
    rows = {r["name"]: r for r in client.get(f"/api/classes/{cls['id']}/pending").json()}
    assert set(rows) == {"Ana (retake)", "Ben"}
    assert rows["Ben"]["number_clash"]["name"] == "Ana (retake)"
    assert rows["Ana (retake)"]["number_clash"]["name"] == "Ben"

    # The teacher accepts the right one; the other can no longer be accepted.
    assert client.post(f"/api/pending/{rows['Ana (retake)']['id']}/approve").status_code == 200
    [left] = client.get(f"/api/classes/{cls['id']}/pending").json()
    assert left["name"] == "Ben" and left["action"] == "number_taken"
    assert client.post(f"/api/pending/{left['id']}/approve").status_code == 409

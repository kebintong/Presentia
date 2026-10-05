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


def test_website_duplicate_is_flagged_and_cannot_be_accepted(api):
    client, cls, db = api
    ana = embedding(1)
    client.post("/api/students", json={
        "student_no": "2023-0001", "name": "Ana", "embedding_b64": b64(ana), "class_id": cls["id"],
    })
    db.add_pending(cls["id"], "r1", "2023-5555", "Fake Ben", near(ana, 3), None, 3, "", "2026-10-05 10:00:00")

    [p] = client.get(f"/api/classes/{cls['id']}/pending").json()
    assert p["face_match"]["student_no"] == "2023-0001" and p["face_match"]["in_class"] is True
    assert "embedding" not in p

    res = client.post(f"/api/pending/{p['id']}/approve")
    assert res.status_code == 409 and res.json()["detail"]["code"] == "face_exists"
    assert len(client.get(f"/api/classes/{cls['id']}/students/summary").json()) == 1


def test_two_waiting_registrations_with_one_face(api):
    client, cls, db = api
    face = embedding(2)
    db.add_pending(cls["id"], "r1", "A-1", "Carl", face, None, 3, "", "2026-10-05 10:00:00")
    db.add_pending(cls["id"], "r2", "A-2", "Carl 2", near(face, 4), None, 3, "", "2026-10-05 10:01:00")
    db.add_pending(cls["id"], "r3", "A-3", "Dana", embedding(3), None, 3, "", "2026-10-05 10:02:00")

    rows = {p["student_no"]: p for p in client.get(f"/api/classes/{cls['id']}/pending").json()}
    assert rows["A-1"]["pending_match"]["student_no"] == "A-2"
    assert rows["A-2"]["pending_match"]["student_no"] == "A-1"
    assert rows["A-3"]["pending_match"] is None

    # Accepting one makes the other a duplicate of an enrolled student.
    assert client.post(f"/api/pending/{rows['A-1']['id']}/approve").status_code == 200
    left = {p["student_no"]: p for p in client.get(f"/api/classes/{cls['id']}/pending").json()}
    assert left["A-2"]["face_match"]["student_no"] == "A-1"
    assert client.post(f"/api/pending/{left['A-2']['id']}/approve").status_code == 409


def test_known_number_with_a_different_face_is_flagged(api):
    client, cls, db = api
    client.post("/api/students", json={
        "student_no": "2023-0001", "name": "Ana", "embedding_b64": b64(embedding(1)),
    })
    db.add_pending(cls["id"], "r1", "2023-0001", "Ana", embedding(9), None, 3, "", "2026-10-05 10:00:00")
    [p] = client.get(f"/api/classes/{cls['id']}/pending").json()
    assert p["existing_id"] is not None and p["face_differs"] is True and p["face_match"] is None

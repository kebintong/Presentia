"""The sidecar's HTTP API, called the way the desktop frontend calls it."""

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import embedding


@pytest.fixture
def client(fresh_db):
    from app import sidecar

    return TestClient(sidecar.app)


def test_engine_status_answers(client):
    res = client.get("/api/engine/status")
    assert res.status_code == 200 and "ready" in res.json()


def test_class_student_and_export_flow(client):
    res = client.post("/api/classes", json={"name": "IT 301", "section": "BSIT 3A"})
    assert res.status_code == 201
    cls = res.json()

    assert [c["id"] for c in client.get("/api/classes").json()] == [cls["id"]]

    emb = base64.b64encode(embedding(3).tobytes()).decode()
    res = client.post("/api/students", json={
        "student_no": "2023-0001", "name": "Ana Cruz", "embedding_b64": emb, "class_id": cls["id"],
    })
    assert res.status_code == 201, res.text

    # Same student number again is refused, not duplicated.
    res = client.post("/api/students", json={
        "student_no": "2023-0001", "name": "Someone Else", "embedding_b64": emb, "class_id": cls["id"],
    })
    assert res.status_code >= 400

    summary = client.get(f"/api/classes/{cls['id']}/students/summary").json()
    assert [s["name"] for s in summary] == ["Ana Cruz"]

    res = client.get(f"/api/classes/{cls['id']}/export.xlsx")
    assert res.status_code == 200
    assert "filename" in res.headers["content-disposition"]
    assert zipfile.ZipFile(io.BytesIO(res.content)).testzip() is None


def test_unknown_class_is_404(client):
    assert client.get("/api/classes/999").status_code == 404
    assert client.get("/api/classes/999/export.xlsx").status_code == 404

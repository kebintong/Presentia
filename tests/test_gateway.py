"""Server mode: teacher sign-in, what a teacher can reach, and the engine's own guard."""

from __future__ import annotations

import concurrent.futures
import contextlib
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import embedding

PASSWORD = "Correct-Horse-7"
ORIGIN = {"origin": "https://testserver"}


class FakeEngine:
    def detect_faces(self, frame, source="meeting", min_score=None):
        return []

    def embed_face(self, frame, bbox, kps):
        raise AssertionError("no faces")


@pytest.fixture
def setup(fresh_db, monkeypatch):
    from app import gateway, sidecar  # noqa: F401  (sidecar: run_monitor)
    from app.core.face_engine import FaceEngine
    from app.core.passwords import hash_password
    from app.data import db

    gateway.reset_state()
    monkeypatch.setattr(FaceEngine, "is_ready", classmethod(lambda cls: True))
    monkeypatch.setattr(FaceEngine, "instance", classmethod(lambda cls: FakeEngine()))
    mine = db.create_class("IT 301")["id"]
    other = db.create_class("CS 101")["id"]
    db.add_student("1", "Kian", embedding(1), mine)
    tid = db.create_teacher("ana", "Ana Reyes", hash_password(PASSWORD), [mine])
    client = TestClient(gateway.gateway, base_url="https://testserver")
    yield client, {"mine": mine, "other": other, "teacher": tid}
    gateway.reset_state()


def sign_in(client, password=PASSWORD, username="ana"):
    return client.post("/login", data={"username": username, "password": password},
                       headers=ORIGIN, follow_redirects=False)


def ws_headers(client, origin=ORIGIN):
    # The test client does not send Secure cookies on wss://; a browser does.
    token = client.cookies.get("presentia_session")
    return {**origin, "cookie": f"presentia_session={token}"} if token else dict(origin)


def read_until(ws, pred, limit=3000):
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("text"):
            data = json.loads(msg["text"])
            if pred(data):
                return data
    raise AssertionError("message not received")


def test_pages_need_a_signed_in_teacher(setup):
    client, _ = setup
    assert client.get("/app", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/api/me").status_code == 401
    page = client.get("/login")
    assert page.status_code == 200 and "Sign in" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    # Nothing of the engine is reachable through the gateway.
    for path in ("/api/students", "/api/classes", "/api/data/delete-all", "/ws/screen", "/share"):
        assert client.get(path).status_code == 404


def test_sign_in_and_only_my_classes(setup):
    client, ids = setup
    res = sign_in(client)
    assert res.status_code == 303 and res.headers["location"] == "/app"
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
    me = client.get("/api/me").json()
    assert me["name"] == "Ana Reyes" and [c["id"] for c in me["classes"]] == [ids["mine"]]
    assert client.get("/app").status_code == 200


def test_wrong_passwords_are_rate_limited(setup):
    client, _ = setup
    for _ in range(5):
        assert sign_in(client, "nope-nope-1").status_code == 401
    assert sign_in(client).status_code == 429          # even the right one, for a while
    assert sign_in(client, username="nobody").status_code == 429


def test_disabled_teacher_is_signed_out(setup):
    client, ids = setup
    from app.data import db

    sign_in(client)
    db.update_teacher(ids["teacher"], disabled=True)
    assert client.get("/api/me").status_code == 401


def test_websockets_need_the_cookie_and_the_gateways_own_page(setup):
    client, _ = setup
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/monitor", headers=ORIGIN) as ws:
            ws.receive_text()
    sign_in(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/monitor", headers=ws_headers(client, {"origin": "https://evil.example"})) as ws:
            ws.receive_text()


def test_teacher_monitors_their_class_from_their_shared_tab(setup):
    client, ids = setup
    sign_in(client)
    img = np.full((540, 960, 3), (36, 33, 32), np.uint8)
    jpeg = cv2.imencode(".jpg", img)[1].tobytes()
    with client.websocket_connect("/ws/feed", headers=ws_headers(client)) as feed:
        feed.send_text(json.dumps({"type": "hello", "label": "", "surface": "browser"}))
        feed.send_bytes(jpeg)
        # The test client cancels the server task as soon as it leaves the
        # block; if the engine is still writing the session's end it reports
        # that as CancelledError (a test-client race, not an app error).
        with contextlib.suppress(concurrent.futures.CancelledError), \
                client.websocket_connect("/ws/monitor", headers=ws_headers(client)) as ws:
            # Not their class.
            ws.send_text(json.dumps({"action": "start", "region": {"tab": True}, "class_id": ids["other"]}))
            assert "can't monitor" in read_until(ws, lambda d: d.get("type") == "error")["message"]
            # Their class; a screen area asked for is ignored — only their own tab.
            ws.send_text(json.dumps({"action": "start", "class_id": ids["mine"],
                                     "region": {"left": 0, "top": 0, "width": 100, "height": 100}}))
            read_until(ws, lambda d: d.get("type") == "started")
            read_until(ws, lambda d: d.get("type") == "capture" and d["state"] == "ok" and d["method"] == "tab")
            roster = read_until(ws, lambda d: d.get("type") == "analysis")["roster"]
            assert [r["name"] for r in roster] == ["Kian"]
            ws.send_text(json.dumps({"action": "stop"}))
            read_until(ws, lambda d: d.get("type") == "stopped")
        feed.close()


def test_engine_refuses_other_websites(fresh_db):
    from app import sidecar

    client = TestClient(sidecar.app)
    assert client.get("/api/classes", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/teachers", json={}, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/classes", headers={"origin": "http://wails.localhost"}).status_code == 200
    assert client.get("/api/classes").status_code == 200        # the desktop shell sends no Origin
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/screen", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()


def test_teacher_accounts_api(fresh_db):
    from app import sidecar
    from app.data import db

    client = TestClient(sidecar.app)
    cid = db.create_class("IT 301")["id"]
    weak = client.post("/api/teachers", json={"username": "ana", "name": "Ana", "password": "short"})
    assert weak.status_code == 400
    bad = client.post("/api/teachers", json={"username": "Ana Reyes!", "name": "Ana", "password": PASSWORD})
    assert bad.status_code == 400
    ok = client.post("/api/teachers", json={"username": "Ana", "name": "Ana", "password": PASSWORD,
                                            "class_ids": [cid]})
    assert ok.status_code == 201 and ok.json()["username"] == "ana" and ok.json()["class_ids"] == [cid]
    assert "pw_hash" not in ok.json()
    dup = client.post("/api/teachers", json={"username": "ana", "name": "Other", "password": PASSWORD})
    assert dup.status_code == 409
    tid = ok.json()["id"]
    upd = client.patch(f"/api/teachers/{tid}", json={"class_ids": [], "disabled": True})
    assert upd.json()["class_ids"] == [] and upd.json()["disabled"] is True
    listing = client.get("/api/server-mode").json()
    assert listing["enabled"] is False and listing["running"] is False and len(listing["teachers"]) == 1
    assert client.delete(f"/api/teachers/{tid}").status_code == 204
    assert db.list_teachers() == []


def test_gateway_starts_and_stops():
    from app.gateway import SERVER

    SERVER.start()
    try:
        assert SERVER.running
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:7790/login", timeout=5) as r:
            assert r.status == 200
    finally:
        SERVER.stop()
    assert not SERVER.running


def test_server_busy_limit(setup, monkeypatch):
    client, ids = setup
    from app import gateway

    monkeypatch.setattr(gateway, "MAX_RUNS", 0)
    sign_in(client)
    with client.websocket_connect("/ws/monitor", headers=ws_headers(client)) as ws:
        assert "already monitoring" in json.loads(ws.receive_text())["message"]

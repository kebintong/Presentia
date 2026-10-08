"""Sharing a browser tab with Presentia: the link, who may connect, frames."""

from __future__ import annotations

import base64
import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import embedding


@pytest.fixture
def client(fresh_db):
    from app import sidecar
    from app.core import tab_feed

    feed = tab_feed.TabFeed()
    sidecar._TAB_FEED = feed
    return TestClient(sidecar.app), feed


def jpeg(colour=(30, 120, 200)):
    img = np.full((360, 640, 3), colour, np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


def test_share_page_is_served_locked_down(client):
    c, _ = client
    res = c.get("/share?t=abc")
    assert res.status_code == 200 and "getDisplayMedia" in res.text
    assert "connect-src ws://127.0.0.1:7788" in res.headers["content-security-policy"]


def test_only_presentias_page_with_the_token_may_send(client):
    c, feed = client
    url = c.post("/api/tabshare/start").json()["url"]
    token = url.split("t=")[1]
    # Wrong token, or another website: refused.
    for path, origin in (("/ws/tabfeed?t=nope", "http://127.0.0.1:7788"),
                         (f"/ws/tabfeed?t={token}", "https://evil.example")):
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(path, headers={"origin": origin}) as ws:
                ws.receive_text()
    assert feed.latest()[0] == "waiting"


def test_frames_arrive_and_stopping_is_reported(client):
    c, feed = client
    token = c.post("/api/tabshare/start").json()["url"].split("t=")[1]
    with c.websocket_connect(f"/ws/tabfeed?t={token}", headers={"origin": "http://127.0.0.1:7788"}) as ws:
        ws.send_text(json.dumps({"type": "hello", "label": "Meet – abc-defg-hij", "surface": "browser"}))
        ws.send_bytes(jpeg())
        # The server handles messages in order; a status call after a round trip sees them.
        ws.send_bytes(jpeg((10, 10, 10)))
        for _ in range(50):
            if feed.latest()[2] >= 2:
                break
            time.sleep(0.02)
        state, frame, seq = feed.latest()
        assert state == "ok" and frame.shape == (360, 640, 3) and seq == 2
        st = c.get("/api/tabshare/status").json()
        assert st["sharing"] and st["label"].startswith("Meet") and st["width"] == 640
        # A frame that did not change is still current while the page is connected.
        assert feed.latest()[0] == "ok"
        ws.send_text(json.dumps({"type": "ended"}))
        for _ in range(50):
            if feed.latest()[0] == "stopped":
                break
            time.sleep(0.02)
        assert feed.latest()[0] == "stopped"


def test_presentia_can_stop_the_share_page(client):
    c, feed = client
    token = c.post("/api/tabshare/start").json()["url"].split("t=")[1]
    with c.websocket_connect(f"/ws/tabfeed?t={token}", headers={"origin": "http://localhost:7788"}) as ws:
        ws.send_bytes(jpeg())
        c.post("/api/tabshare/stop")
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "stop"
    assert feed.latest()[0] == "stopped"


def test_registration_angles_are_kept(client):
    c, _ = client
    cls = c.post("/api/classes", json={"name": "IT 301"}).json()
    main = embedding(3)
    near = main + 0.3 * embedding(4)
    near /= np.linalg.norm(near)
    stranger = embedding(9)
    b64 = lambda v: base64.b64encode(np.asarray(v, np.float32).tobytes()).decode()  # noqa: E731
    res = c.post("/api/students", json={"student_no": "1", "name": "Kian", "embedding_b64": b64(main),
                                         "class_id": cls["id"], "samples_b64": [b64(near), b64(stranger)]})
    assert res.status_code == 201, res.text
    from app.data import db

    faces = db.list_faces(res.json()["id"])
    assert len(faces) == 1 and faces[0][2] == "enrol"     # the stranger's picture is not kept


def test_learn_faces_setting(client):
    c, _ = client
    assert c.get("/api/checks").json()["learn_faces"] is True
    assert c.put("/api/checks", json={"learn_faces": False}).json()["learn_faces"] is False
    c.put("/api/checks", json={"learn_faces": True})


def test_a_tab_that_stops_updating_pauses_monitoring(monkeypatch):
    from app.core import tab_feed

    clock = [100.0]
    monkeypatch.setattr(tab_feed.time, "monotonic", lambda: clock[0])
    feed = tab_feed.TabFeed()
    cid = feed.connect()
    feed.hello(cid, "web-contents-media-stream://6:5", "browser")
    assert feed.push(cid, jpeg())
    assert feed.latest()[0] == "ok" and feed.status()["label"] == ""
    clock[0] += 10
    assert feed.latest()[0] == "ok"            # unchanged tab: still current
    clock[0] += 10
    assert feed.latest()[0] == "stale"         # the browser stopped sending it
    feed.push(cid, jpeg())
    assert feed.latest()[0] == "ok"

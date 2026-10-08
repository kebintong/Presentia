"""The Meeting Monitor watching a shared browser tab, end to end (with a
stand-in face engine): capture states, roster updates, switching source."""

from __future__ import annotations

import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import embedding


class FakeEngine:
    def detect_faces(self, frame, source="meeting", min_score=None):
        return []

    def embed_face(self, frame, bbox, kps):
        raise AssertionError("no faces")


@pytest.fixture
def setup(fresh_db, monkeypatch):
    from app import sidecar
    from app.core import tab_feed
    from app.core.face_engine import FaceEngine
    from app.data import db

    feed = tab_feed.TabFeed()
    monkeypatch.setattr(sidecar, "_TAB_FEED", feed)
    monkeypatch.setattr(FaceEngine, "is_ready", classmethod(lambda cls: True))
    monkeypatch.setattr(FaceEngine, "instance", classmethod(lambda cls: FakeEngine()))
    cls = db.create_class("IT 301")
    db.add_student("1", "Kian", embedding(1), cls["id"])
    return TestClient(sidecar.app), feed, cls["id"]


def push_frame(feed):
    cid = feed.connect()
    img = np.full((540, 960, 3), (36, 33, 32), np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert feed.push(cid, buf.tobytes())
    return cid


def read_until(ws, pred, limit=3000):
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("text"):
            data = json.loads(msg["text"])
            if pred(data):
                return data
    raise AssertionError("message not received")


def test_monitoring_a_shared_tab(setup):
    c, feed, cid = setup
    with c.websocket_connect("/ws/screen") as ws:
        ws.send_text(json.dumps({"action": "start", "region": {"tab": True, "title": "Browser tab"},
                                 "class_id": cid, "name": "Test"}))
        read_until(ws, lambda d: d.get("type") == "started")
        waiting = read_until(ws, lambda d: d.get("type") == "capture")
        assert waiting["state"] == "tab_waiting" and waiting["method"] == "tab"
        conn = push_frame(feed)
        ok = read_until(ws, lambda d: d.get("type") == "capture" and d["state"] == "ok")
        assert ok["method"] == "tab"
        analysis = read_until(ws, lambda d: d.get("type") == "analysis")
        assert analysis["roster"][0]["state"] == "waiting"
        feed.ended(conn)
        read_until(ws, lambda d: d.get("type") == "capture" and d["state"] == "tab_stopped")
        # Switch to another source without ending the session.
        ws.send_text(json.dumps({"action": "switch_source", "region": {"tab": True}}))
        read_until(ws, lambda d: d.get("type") == "alert" and "Now watching" in d["message"])
        ws.send_text(json.dumps({"action": "stop"}))
        read_until(ws, lambda d: d.get("type") == "stopped")


def test_live_stats_group_the_new_states():
    from app import sidecar

    live = sidecar._LiveMonitor()
    live.start("x")
    live.stats([{"id": 1, "name": "A", "state": "present"},
                {"id": 2, "name": "B", "state": "unclear"},
                {"id": 3, "name": "C", "state": "unseen", "away": 40.0},
                {"id": 4, "name": "D", "state": "cam_off", "away": 90.0},
                {"id": 5, "name": "E", "state": "waiting"}], 0)
    snap = live.snapshot()
    assert (snap["present"], snap["missing"], snap["waiting"], snap["unclear"]) == (2, 2, 1, 1)
    assert [(a["name"], a["kind"]) for a in snap["away"]] == [("D", "cam_off"), ("C", "unseen")]


def test_this_is_assigns_an_unknown_face(setup, monkeypatch):
    c, feed, cid = setup
    from app.core.face_engine import FaceEngine
    from app.data import db

    kian = db.list_students(cid)[0]["id"]
    main = embedding(1)
    other = embedding(7)
    face = main * 0.3 + other * 0.954
    face = (face / np.linalg.norm(face)).astype(np.float32)   # scores ~0.3: not recognised

    class OneFace(FakeEngine):
        def detect_faces(self, frame, source="meeting", min_score=None):
            return [((100, 100, 200, 220), 0.9, None)]

        def embed_face(self, frame, bbox, kps):
            return face

    monkeypatch.setattr(FaceEngine, "instance", classmethod(lambda cls: OneFace()))
    with c.websocket_connect("/ws/screen") as ws:
        ws.send_text(json.dumps({"action": "start", "region": {"tab": True}, "class_id": cid, "name": "T"}))
        read_until(ws, lambda d: d.get("type") == "started")
        push_frame(feed)
        unk = read_until(ws, lambda d: d.get("type") == "analysis" and d["unknowns"])["unknowns"][0]
        ws.send_text(json.dumps({"action": "assign_unknown", "uid": unk["uid"], "student_id": kian}))
        done = read_until(ws, lambda d: d.get("type") in ("assigned", "assign_check", "error"))
        assert done["type"] == "assigned" and done["name"] == "Kian"
        roster = read_until(ws, lambda d: d.get("type") == "analysis" and not d["unknowns"])["roster"]
        assert roster[0]["state"] == "present"
        ws.send_text(json.dumps({"action": "stop"}))
        read_until(ws, lambda d: d.get("type") == "stopped")
    assert [s for _, _, s in db.list_faces(kian)] == ["assigned"]

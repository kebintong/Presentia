"""Diagnostic mode: off by default, writes an activity log while on, turns
itself off after a week, and sends a report only when asked."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(fresh_db):
    from app import sidecar
    from app.core import diag

    yield TestClient(sidecar.app), diag
    diag.set_mode(False)
    diag.clear_log()


def test_off_by_default(client):
    c, diag = client
    assert c.get("/api/diagnostics/mode").json()["enabled"] is False
    c.get("/api/classes")
    assert diag.read_log() == ""


def test_on_writes_activity_log(client):
    c, diag = client
    state = c.put("/api/diagnostics/mode", json={"enabled": True}).json()
    assert state["enabled"] is True and state["until"] > state["since"]

    c.get("/api/classes")
    c.get("/api/classes/999")  # 404
    c.post("/api/diagnostics/event", json={
        "title": "Turning on online registration", "message": "timed out",
        "path": "/api/classes/1/online", "status": 502,
    })
    log = c.get("/api/diagnostics/log").json()
    assert "GET /api/classes -> 200" in log["text"]
    assert "GET /api/classes/999 -> 404" in log["text"]
    assert "Turning on online registration: timed out" in log["text"]
    assert log["size"] > 0
    assert any("timed out" in e["message"] for e in diag.recent())

    assert c.delete("/api/diagnostics/log").status_code == 204
    assert "GET /api/classes -> 200" not in diag.read_log()


def test_turns_itself_off_after_a_week(client):
    c, diag = client
    from app.data import db

    c.put("/api/diagnostics/mode", json={"enabled": True})
    db.set_setting(diag._K_MODE, (datetime.now() - timedelta(days=diag.MODE_DAYS, minutes=1)).isoformat())
    state = c.get("/api/diagnostics/mode").json()
    assert state["enabled"] is False and state["expired"] is True
    assert diag.enabled is False


def test_send_report(client, monkeypatch):
    c, _ = client
    from app.data import cloud

    sent = {}

    def fake_send(summary, text, version):
        sent.update(summary=summary, text=text, version=version)
        return {"id": "R-TEST", "github": False}

    monkeypatch.setattr(cloud, "send_report", fake_send)
    res = c.post("/api/diagnostics/send", json={"summary": "x failed", "text": "details", "app_version": "1.5.2"})
    assert res.json()["id"] == "R-TEST" and sent["text"] == "details"
    assert c.post("/api/diagnostics/send", json={"text": "  "}).status_code == 400


def test_quick_report_skips_network(client):
    c, _ = client
    body = c.get("/api/diagnostics", params={"network": False}).json()
    assert body["network"] is None and body["mode"]["enabled"] is False

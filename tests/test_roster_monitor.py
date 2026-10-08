"""RosterMonitor: a face that is not recognised is not "camera off"."""

from __future__ import annotations

from app.core import roster_monitor as rmod
from app.core.roster_monitor import RosterMonitor

KIAN, ANA = 1, 2


def make(monkeypatch, **kw):
    clock = [0.0]
    monkeypatch.setattr(rmod.time, "monotonic", lambda: clock[0])
    events: list[tuple[int, str]] = []
    rm = RosterMonitor([{"id": KIAN, "name": "Kian"}, {"id": ANA, "name": "Ana"}],
                       lambda sid, kind, msg: events.append((sid, kind)), **kw)
    return rm, clock, events


def run(rm, clock, start, end, visible=(), tiles=None, step=1.0):
    t = start
    while t <= end + 1e-9:
        clock[0] = t
        rm.update(set(visible), tiles)
        t += step


def state(rm, sid):
    return next(s for s in rm.status() if s["id"] == sid)["state"]


def test_short_dips_never_show(monkeypatch):
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 3, visible={KIAN})
    run(rm, clock, 4, 20)                       # 17 s without the face, no tile info
    assert state(rm, KIAN) == "present"
    assert [k for _, k in events] == ["time_in"]


def test_face_not_seen_after_30s_is_not_camera_off(monkeypatch):
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 3, visible={KIAN})
    run(rm, clock, 4, 40)
    assert state(rm, KIAN) == "unseen"
    assert (KIAN, "unseen") in events and (KIAN, "camera_off") not in events
    run(rm, clock, 41, 42, visible={KIAN})
    assert state(rm, KIAN) == "present" and events[-1] == (KIAN, "returned")


def test_live_tile_keeps_a_half_face_on_camera(monkeypatch):
    """Kian's face is half out of his tile, but the tile shows moving video."""
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 3, visible={KIAN})
    run(rm, clock, 4, 300, tiles={KIAN: "video"})
    assert state(rm, KIAN) == "unclear"
    assert KIAN in rm.present_ids()
    assert [k for _, k in events] == ["time_in"]   # no warning at all


def test_camera_off_tile_is_camera_off(monkeypatch):
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 3, visible={KIAN})
    run(rm, clock, 4, 12, tiles={KIAN: "avatar"})
    assert state(rm, KIAN) == "cam_off" and (KIAN, "camera_off") in events
    # Camera back on, face not clear yet.
    run(rm, clock, 13, 14, tiles={KIAN: "video"})
    assert state(rm, KIAN) == "unclear" and events[-1] == (KIAN, "returned")


def test_unclear_note_once_after_ten_minutes(monkeypatch):
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 1, visible={KIAN})
    run(rm, clock, 2, 700, tiles={KIAN: "video"}, step=2.0)
    assert [k for _, k in events].count("unclear") == 1


def test_tile_hiccup_does_not_flip_unclear_to_unseen(monkeypatch):
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 1, visible={KIAN})
    run(rm, clock, 2, 120, tiles={KIAN: "video"})
    run(rm, clock, 121, 125)                    # tiles not read for a few seconds
    assert state(rm, KIAN) == "unclear"


def test_paused_capture_time_is_not_counted(monkeypatch):
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 3, visible={KIAN})
    clock[0] = 200.0                            # no passes for 3 minutes (window minimised)
    rm.update(set())
    assert state(rm, KIAN) == "present"
    run(rm, clock, 201, 225)
    assert state(rm, KIAN) == "present"


def test_never_seen_stays_waiting(monkeypatch):
    rm, clock, events = make(monkeypatch)
    run(rm, clock, 0, 60, tiles={ANA: "avatar"})
    assert state(rm, ANA) == "waiting" and not events

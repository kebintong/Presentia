"""Window monitoring: when a picture of the selected window may be read, and
when monitoring must pause instead. The Windows calls are replaced by fakes;
the real capture has to be tried on Windows (see build/BUILD.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("PRESENTIA_SKIP_ENGINE", "1")

from app.core import window_capture as wc  # noqa: E402

SCREEN = [(0, 0, 1920, 1080)]


# ── geometry ──────────────────────────────────────────────────────────────────

def test_uncovered_window_is_fully_visible():
    assert wc.visible_fraction_of((100, 100, 900, 700), SCREEN, []) == 1.0


def test_fully_covered_window():
    assert wc.visible_fraction_of((100, 100, 900, 700), SCREEN, [(0, 0, 1920, 1040)]) == 0.0


def test_half_covered_window():
    f = wc.visible_fraction_of((0, 0, 1000, 1000), [(0, 0, 2000, 2000)], [(0, 0, 500, 1000)])
    assert f == pytest.approx(0.5)


def test_several_windows_cover_it_together():
    target = (0, 0, 1000, 1000)
    above = [(0, 0, 600, 1000), (500, 0, 1000, 600), (400, 500, 1000, 1000)]
    assert wc.visible_fraction_of(target, [(0, 0, 2000, 2000)], above) == 0.0


def test_a_corner_still_showing_counts():
    above = [(0, 0, 990, 1000), (990, 10, 1000, 1000)]
    f = wc.visible_fraction_of((0, 0, 1000, 1000), [(0, 0, 2000, 2000)], above)
    assert 0 < f < 0.001


def test_off_screen_part_is_not_visible():
    f = wc.visible_fraction_of((1520, 0, 2320, 800), SCREEN, [])
    assert f == pytest.approx(0.5)


def test_second_monitor_counts():
    monitors = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
    assert wc.visible_fraction_of((1520, 0, 2320, 800), monitors, []) == 1.0


def test_subtract_keeps_pieces_disjoint():
    pieces = wc.subtract([(0, 0, 10, 10)], (3, 3, 6, 6))
    assert sum(wc.rect_area(p) for p in pieces) == 100 - 9
    for i, a in enumerate(pieces):
        for b in pieces[i + 1:]:
            assert wc.intersect(a, b) is None


def test_off_windows_helpers_are_harmless():
    if sys.platform == "win32":
        pytest.skip("Windows has the real calls")
    assert wc.visible_fraction(1234, SCREEN) == 1.0
    assert wc.restore_behind(1234) is False
    assert wc.wgc_library() == "not used on this system"
    cap = wc.WgcCapture(1234)
    assert cap.start() is False and cap.error


# ── the follower's decisions ─────────────────────────────────────────────────

class _Rect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class FakeWgc:
    def __init__(self):
        self.running = True
        self.frame = None
        self.seq = 0
        self.age = 0.0
        self.closed = False

    def latest(self):
        return None if self.frame is None else (self.frame, self.seq, self.age)

    def close(self):
        self.closed = True


def _follower(monkeypatch, *, wgc=None, window="ok", visible=1.0, printwindow=None, screen=None):
    from app import sidecar

    f = object.__new__(sidecar._WindowFollower)
    f.title = "Meet"
    f._black = 0
    f.screen_only = False
    f._screen_since = 0.0
    f._wgc = wgc
    f._wgc_seq = 0
    f.method = "wgc" if wgc else "printwindow"
    f._bmp = f._dc = f._bits = None
    f._size = (0, 0)
    f.vis = visible
    monkeypatch.setattr(f, "state", lambda: window, raising=False)
    monkeypatch.setattr(f, "_rects", lambda: (_Rect(0, 0, 800, 600), _Rect(0, 0, 800, 600)), raising=False)
    monkeypatch.setattr(f, "visible", lambda monitors: f.vis, raising=False)
    monkeypatch.setattr(f, "_print_window", lambda wr, fr: printwindow, raising=False)
    monkeypatch.setattr(f, "_screen_grab", lambda sct, desktop, fr: screen, raising=False)
    monkeypatch.setattr(f, "close", lambda: None, raising=False)
    return f


def _img(v=128):
    return np.full((60, 80, 3), v, dtype=np.uint8)


def test_new_wgc_picture_is_read(monkeypatch):
    w = FakeWgc()
    w.frame, w.seq = _img(), 1
    f = _follower(monkeypatch, wgc=w, visible=0.0)
    state, frame = f.capture(None, {}, SCREEN)
    assert state == "ok" and frame is w.frame


def test_quiet_but_visible_window_keeps_its_picture(monkeypatch):
    w = FakeWgc()
    w.frame, w.seq = _img(), 1
    f = _follower(monkeypatch, wgc=w, visible=0.4)
    f.capture(None, {}, SCREEN)
    w.age = 30.0  # nothing moved for 30 s, but it is on screen
    state, frame = f.capture(None, {}, SCREEN)
    assert state == "ok" and frame is not None


def test_covered_window_that_stopped_drawing_pauses(monkeypatch):
    w = FakeWgc()
    w.frame, w.seq = _img(), 1
    f = _follower(monkeypatch, wgc=w, visible=0.0)
    assert f.capture(None, {}, SCREEN)[0] == "ok"
    w.age = 1.0  # briefly quiet: still fine
    assert f.capture(None, {}, SCREEN)[0] == "ok"
    w.age = 5.0  # an old picture of a hidden window: never read it
    assert f.capture(None, {}, SCREEN) == ("hidden", None)
    w.seq, w.age = 2, 0.0  # drawing again
    state, frame = f.capture(None, {}, SCREEN)
    assert state == "ok" and frame is not None


def test_covered_window_that_keeps_drawing_is_read(monkeypatch):
    w = FakeWgc()
    f = _follower(monkeypatch, wgc=w, visible=0.0)
    for seq in range(1, 5):
        w.frame, w.seq, w.age = _img(seq * 20), seq, 0.0
        state, frame = f.capture(None, {}, SCREEN)
        assert state == "ok" and frame is w.frame


def test_minimised_and_closed_pause(monkeypatch):
    w = FakeWgc()
    w.frame, w.seq = _img(), 1
    assert _follower(monkeypatch, wgc=w, window="minimized").capture(None, {}, SCREEN) == ("minimized", None)
    assert _follower(monkeypatch, wgc=w, window="closed").capture(None, {}, SCREEN) == ("closed", None)


def test_ended_wgc_falls_back_to_printwindow(monkeypatch):
    w = FakeWgc()
    w.running = False
    f = _follower(monkeypatch, wgc=w, printwindow=_img())
    state, frame = f.capture(None, {}, SCREEN)
    assert state == "ok" and frame is not None
    assert w.closed and f._wgc is None and f.method == "printwindow"


def test_black_printwindow_uses_the_screen_only_while_uncovered(monkeypatch):
    f = _follower(monkeypatch, printwindow=_img(0), screen=_img(), visible=1.0)
    for _ in range(f.BLACK_FRAMES_BEFORE_FALLBACK - 1):
        assert f.capture(None, {}, SCREEN) == ("ok", None)
    state, frame = f.capture(None, {}, SCREEN)
    assert state == "screen_only" and frame is not None
    f.vis = 0.5  # something on top: the screen would show *that*
    assert f.capture(None, {}, SCREEN) == ("covered", None)


def test_screen_mode_retries_the_window_itself(monkeypatch):
    f = _follower(monkeypatch, printwindow=_img(0), screen=_img(), visible=1.0)
    f.screen_only = True
    f._screen_since = 0.0  # long ago
    monkeypatch.setattr(f, "_print_window", lambda wr, fr: _img(), raising=False)
    state, frame = f.capture(None, {}, SCREEN)
    assert state == "ok" and not f.screen_only


def test_messages():
    from app import sidecar

    f = object.__new__(sidecar._WindowFollower)
    f.title = "Meet"
    assert f.describe("ok", "ok") is None
    assert f.describe("hidden", "ok")[1] == "warn"
    assert "Chrome" in f.describe("hidden", "ok")[0]
    assert f.describe("ok", "hidden") == ("Meet is back — monitoring resumed.", "ok")
    assert f.describe("screen_only", "covered")[1] == "ok"
    assert f.describe("screen_only", "ok")[1] == "warn"
    assert f.describe("closed", "ok")[1] == "error"
    assert "Keep monitoring" in f.describe("minimized", "ok")[0]

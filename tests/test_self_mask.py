"""Presentia's own windows are painted out of screen-area grabs."""

import numpy as np

from app.core.self_mask import FILL, SelfMask, paint


def test_paint_covers_only_the_overlap():
    frame = np.full((100, 200, 3), 200, np.uint8)
    # Grab starts at screen (1000, 500); a window spans (1150, 450)-(1300, 540).
    paint(frame, 1000, 500, [(1150, 450, 1300, 540)])
    assert (frame[0:40, 150:200] == FILL).all()
    assert (frame[40:, :] == 200).all()
    assert (frame[:, :150] == 200).all()


def test_window_outside_the_grab_changes_nothing():
    frame = np.full((50, 50, 3), 7, np.uint8)
    paint(frame, 0, 0, [(60, 60, 90, 90), (-40, -40, -1, -1)])
    assert (frame == 7).all()


def test_off_windows_is_a_no_op():
    m = SelfMask(pids={1})
    if not m._ok:  # Linux CI
        frame = np.zeros((10, 10, 3), np.uint8)
        assert m.rects() == []
        assert m.apply(frame, 0, 0) is frame


def test_soften_blurs_dims_and_outlines_only_the_window():
    from app.core.self_mask import soften

    rnd = np.random.default_rng(1)
    view = rnd.integers(0, 255, (200, 300, 3), dtype=np.uint8)
    before = view.copy()
    # A window at screen (1100, 540)-(1300, 640); the grab starts at (1000, 500), shown at half size.
    soften(view, 1000, 500, [(1100, 540, 1300, 640)], 0.5)
    inside = view[25:45, 55:145].astype(int)
    assert inside.std() < before[25:45, 55:145].astype(int).std() / 2  # blurred
    assert inside.mean() < 160                                           # dimmed
    assert (view[:18] == before[:18]).all() and (view[:, :48] == before[:, :48]).all()
    assert (view[20, 50:55] == 205).all()                                 # dashed outline starts at the corner

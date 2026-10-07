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

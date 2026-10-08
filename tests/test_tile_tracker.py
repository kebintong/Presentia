"""TileTracker keeps students identified when only part of the face shows."""

from __future__ import annotations

import numpy as np

from app.core import tile_tracker as tt
from app.core.tile_tracker import TileTracker, rank_top2


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


# Three students with orthogonal "faces".
KIAN, CLARK, ANA = 1, 2, 3
KNOWN = [(KIAN, unit([1, 0, 0, 0])), (CLARK, unit([0, 1, 0, 0])), (ANA, unit([0, 0, 1, 0]))]


def mixed(sid: int, score: float) -> np.ndarray:
    """An embedding scoring `score` against `sid` and ~0 against the others."""
    base = dict(KNOWN)[sid]
    other = unit([0, 0, 0, 1])
    return unit(base * score + other * np.sqrt(max(0.0, 1 - score * score)))


class FakeEngine:
    """Detections and embeddings scripted per pass."""

    def __init__(self):
        self.faces: list[tuple[tuple, np.ndarray]] = []
        self.scores: dict[tuple, float] = {}

    def detect_faces(self, frame, source="meeting", min_score=None):
        cut = 0.5 if min_score is None else min_score
        return [(bbox, self.scores.get(bbox, 0.9), None) for bbox, _ in self.faces
                if self.scores.get(bbox, 0.9) >= cut]

    def embed_face(self, frame, bbox, kps):
        for b, emb in self.faces:
            if b == bbox:
                return emb
        raise AssertionError("unknown box")


FRAME = np.zeros((900, 1600, 3), np.uint8)


def run(tracker, engine, faces, clock, t):
    engine.faces = faces
    clock[0] = t
    return tracker.process(FRAME)


def make(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(tt.time, "monotonic", lambda: clock[0])
    eng = FakeEngine()
    return TileTracker(eng, lambda: KNOWN), eng, clock


def ids(matches):
    return sorted(sid for sid, _, _ in matches)


def test_full_face_is_recognised(monkeypatch):
    tr, eng, clock = make(monkeypatch)
    matches, unknowns = run(tr, eng, [((100, 100, 200, 220), mixed(KIAN, 0.7))], clock, 0)
    assert ids(matches) == [KIAN] and not unknowns


def test_half_face_keeps_its_identity(monkeypatch):
    """Kian moves half behind his laptop: his face now scores only ~0.33."""
    tr, eng, clock = make(monkeypatch)
    box = (100, 100, 200, 220)
    run(tr, eng, [(box, mixed(KIAN, 0.7))], clock, 0)
    matches, unknowns = run(tr, eng, [(box, mixed(KIAN, 0.33))], clock, 11)   # re-check due
    assert ids(matches) == [KIAN] and not unknowns
    # A face that turned into somebody clearly else is not kept.
    matches, _ = run(tr, eng, [(box, mixed(CLARK, 0.7))], clock, 22)
    assert ids(matches) == [CLARK]


def test_one_bad_look_does_not_lose_a_student(monkeypatch):
    tr, eng, clock = make(monkeypatch)
    box = (100, 100, 200, 220)
    run(tr, eng, [(box, mixed(KIAN, 0.7))], clock, 0)
    noise = unit([0.1, 0.1, 0.1, 1])                                   # matches nobody
    for i, t in enumerate([11, 14, 17]):
        matches, unknowns = run(tr, eng, [(box, noise)], clock, t)
        if i < tt.MISSES_TO_DROP - 1:
            assert ids(matches) == [KIAN], f"lost after {i + 1} bad look(s)"
    assert ids(matches) == [] and len(unknowns) == 1                  # dropped after 3 in a row


def test_new_partial_face_near_where_a_student_was_is_theirs(monkeypatch):
    """The tile reflows / the face re-appears lower in the tile, half cut off."""
    tr, eng, clock = make(monkeypatch)
    run(tr, eng, [((100, 100, 200, 220), mixed(KIAN, 0.7))], clock, 0)
    run(tr, eng, [], clock, 5)                                          # gone for a moment
    matches, unknowns = run(tr, eng, [((120, 330, 210, 420), mixed(KIAN, 0.34))], clock, 8)
    assert ids(matches) == [KIAN] and not unknowns


def test_weak_face_far_away_or_ambiguous_stays_unknown(monkeypatch):
    tr, eng, clock = make(monkeypatch)
    run(tr, eng, [((100, 100, 200, 220), mixed(KIAN, 0.7))], clock, 0)
    run(tr, eng, [], clock, 5)
    # Far away on the screen: not claimed.
    matches, unknowns = run(tr, eng, [((1400, 750, 1500, 860), mixed(KIAN, 0.34))], clock, 8)
    assert ids(matches) == [] and len(unknowns) == 1
    # Two students about equally likely: not claimed.
    tr2, eng2, clock2 = make(monkeypatch)
    run(tr2, eng2, [((100, 100, 200, 220), mixed(KIAN, 0.7))], clock2, 0)
    run(tr2, eng2, [], clock2, 5)
    both = unit(dict(KNOWN)[KIAN] * 0.34 + dict(KNOWN)[CLARK] * 0.31 + unit([0, 0, 0, 1]) * 0.88)
    matches, _ = run(tr2, eng2, [((120, 330, 210, 420), both)], clock2, 8)
    assert ids(matches) == []


def test_not_claimed_while_the_student_is_on_screen(monkeypatch):
    tr, eng, clock = make(monkeypatch)
    kian_box = (100, 100, 200, 220)
    run(tr, eng, [(kian_box, mixed(KIAN, 0.7))], clock, 0)
    matches, unknowns = run(tr, eng, [(kian_box, mixed(KIAN, 0.7)),
                                      ((120, 330, 210, 420), mixed(KIAN, 0.34))], clock, 3)
    assert ids(matches) == [KIAN] and len(unknowns) == 1


def test_rank_top2_one_entry_per_student():
    known = KNOWN + [(KIAN, unit([0.9, 0.1, 0, 0]))]
    r = rank_top2(unit([1, 0, 0, 0]), known)
    assert r[0][0] == KIAN and r[1][0] != KIAN


def test_weak_detection_at_a_tile_edge_keeps_its_student(monkeypatch):
    """Kian's face slides to the bottom of his tile: the detector is less sure."""
    tr, eng, clock = make(monkeypatch)
    box = (100, 100, 200, 220)
    run(tr, eng, [(box, mixed(KIAN, 0.7))], clock, 0)
    low = (100, 300, 200, 400)
    eng.scores[low] = 0.4
    eng.faces = [(low, mixed(KIAN, 0.36))]
    clock[0] = 5
    matches, unknowns = tr.process(FRAME)                       # no tile information
    assert ids(matches) == [] and not unknowns
    matches, unknowns = tr.process(FRAME, edge=lambda b: True)  # at the tile's edge
    assert ids(matches) == [KIAN] and not unknowns


def test_weak_edge_detection_is_never_an_unknown_face(monkeypatch):
    tr, eng, clock = make(monkeypatch)
    low = (100, 300, 200, 400)
    eng.scores[low] = 0.35
    noise = unit([0.1, 0.1, 0.1, 1])
    eng.faces = [(low, noise)]
    matches, unknowns = tr.process(FRAME, edge=lambda b: True)
    assert ids(matches) == [] and unknowns == []


def test_clear_matches_are_offered_for_learning(monkeypatch):
    tr, eng, clock = make(monkeypatch)
    run(tr, eng, [((100, 100, 200, 220), mixed(KIAN, 0.75)), ((400, 100, 500, 220), mixed(ANA, 0.5))],
        clock, 0)
    clear = tr.take_clear()
    assert [sid for sid, _, _ in clear] == [KIAN]      # Ana's 0.5 is a match, but not a clear one
    assert tr.take_clear() == []

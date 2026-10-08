"""Cheap identity tracking across meeting-tile frames.

Full recognition (detect + embed every face) costs >1s per pass on CPU, which
made monitoring sluggish. Meeting tiles barely move, so identities are sticky:
detect faces every pass (fast), carry identity forward for boxes that overlap
the previous pass, and only compute embeddings for faces that are new or
haven't been re-verified recently. Steady-state cost is detection only.
"""

from __future__ import annotations

import itertools
import time
from typing import Callable

import numpy as np

from app.core.face_engine import MATCH_THRESHOLD, FaceEngine

REFRESH_EVERY = 10.0     # re-verify each face's identity at most this often
IOU_MATCH = 0.3          # box overlap needed to carry identity forward
MAX_EMBEDS_PER_PASS = 2  # spread expensive embeddings across passes

# A face only partly in view (turned away, half behind a laptop, cut off by
# the meeting's toolbar) matches its owner more weakly than a full face.
# Such a face was being reported as "unknown" while its owner was flagged as
# missing / camera off. Two rules keep a weaker match on the right student:
KEEP_SCORE = 0.28        # a face that already has an identity keeps it down to this
CLAIM_SCORE = 0.32       # a new face can be a student not on screen, at this score…
CLAIM_MARGIN = 0.08      # …if it beats every other student by this much…
CLAIM_NEAR = 0.40        # …and is near where they were last seen (share of the frame diagonal)
CLAIM_RECENT = 600.0     # …within the last 10 minutes
MISSES_TO_DROP = 3       # failed re-checks in a row before an identity is dropped
RETRY_SOON = 2.0         # after a weak or failed re-check, look again this soon (s)

# A face cut off by the edge of its meeting tile is often detected with less
# confidence than the detector's usual cut-off. With `edge` given, weaker
# detections are kept when they touch a tile's edge; they can only keep or
# claim a student by the rules above and are never listed as unknown faces.
DET_SCORE = 0.5          # the detector's usual cut-off
EDGE_DET_SCORE = 0.3     # …and for faces at the edge of a tile

# Clear, unambiguous matches are offered for learning how a student looks in
# meetings (app.core.face_gallery): this score, this far ahead of anyone else.
LEARN_SCORE = 0.60
LEARN_MARGIN = 0.15


def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


class TileTracker:
    def __init__(
        self,
        engine: FaceEngine,
        get_known: Callable[[], list[tuple[int, np.ndarray]]],
        refresh_every: float = REFRESH_EVERY,
    ) -> None:
        self._engine = engine
        self._get_known = get_known
        self._refresh_every = refresh_every
        # each track: {uid, bbox, kps, sid, score, emb, embedded_at, misses, weak}
        self._tracks: list[dict] = []
        self._uids = itertools.count(1)
        # where each student was last seen: sid -> (bbox, monotonic time)
        self._last_seen: dict[int, tuple[tuple, float]] = {}
        # clear matches embedded since the last take_clear(): (sid, emb, score)
        self._clear: list[tuple[int, np.ndarray, float]] = []

    def take_clear(self) -> list[tuple[int, np.ndarray, float]]:
        """Clear matches embedded since the last call (see LEARN_SCORE)."""
        out, self._clear = self._clear, []
        return out

    def reidentify(self) -> None:
        """Re-match cached embeddings against the (updated) known list.

        Cheap; used right after a student is enrolled mid-session so their
        existing on-screen face flips from unknown to recognized immediately.
        """
        known = self._get_known()
        for t in self._tracks:
            if t["emb"] is not None:
                match = FaceEngine.identify(t["emb"], known)
                t["sid"], t["score"] = match if match else (None, 0.0)

    def force_refresh(self, keep: int | None = None) -> None:
        """Mark tracks as due for a fresh embedding on the next pass.

        Used by on-demand re-verification: the instructor asks "is that
        really them?" and the sticky identity is re-proven instead of being
        carried forward from an earlier match.
        """
        for t in self._tracks:
            if keep is None or t["sid"] == keep or t["sid"] is None:
                t["embedded_at"] = None

    def process(self, frame: np.ndarray, edge: Callable[[tuple], bool] | None = None) -> tuple[
        list[tuple[int, float, tuple[int, int, int, int]]],
        list[tuple[np.ndarray, tuple[int, int, int, int], int]],
    ]:
        """One pass: detect, carry identities forward, embed only what's needed.

        `edge(bbox)` says whether a face box touches the edge of its meeting
        tile; weaker detections are then kept there (see EDGE_DET_SCORE).

        Returns (matches, unknowns): matches as in FaceEngine.analyze_all;
        unknowns as (embedding, bbox, uid) where uid stays the same for as
        long as that face keeps being tracked.
        """
        if edge is None:
            detections = [(b, sc, k, False) for b, sc, k in self._engine.detect_faces(frame)]
        else:
            detections = []
            for b, sc, k in self._engine.detect_faces(frame, min_score=EDGE_DET_SCORE):
                if sc >= DET_SCORE:
                    detections.append((b, sc, k, False))
                elif edge(b):
                    detections.append((b, sc, k, True))
        now = time.monotonic()

        pool = list(self._tracks)
        next_tracks: list[dict] = []
        for bbox, _det_score, kps, weak_det in detections:
            best_track, best_iou = None, IOU_MATCH
            for t in pool:
                overlap = _iou(bbox, t["bbox"])
                if overlap >= best_iou:
                    best_track, best_iou = t, overlap
            if best_track is not None:
                pool.remove(best_track)
                best_track["bbox"], best_track["kps"] = bbox, kps
                best_track["edge"] = weak_det
                next_tracks.append(best_track)
            else:
                next_tracks.append({
                    "uid": next(self._uids),
                    "bbox": bbox, "kps": kps, "sid": None, "score": 0.0,
                    "emb": None, "embedded_at": None, "misses": 0, "weak": False,
                    "edge": weak_det,
                })

        # embed new faces first, then the stalest verified ones
        known = self._get_known()
        candidates = [
            t for t in next_tracks
            if t["embedded_at"] is None
            or now - t["embedded_at"] >= self._refresh_every
        ]
        candidates.sort(key=lambda t: (t["embedded_at"] is not None,
                                       t["embedded_at"] or 0.0))
        diag = float(np.hypot(*frame.shape[:2])) or 1.0
        for t in candidates[:MAX_EMBEDS_PER_PASS]:
            t["emb"] = self._engine.embed_face(frame, t["bbox"], t["kps"])
            on_screen = {o["sid"] for o in next_tracks if o is not t and o["sid"] is not None}
            ranked = rank_top2(t["emb"], known)
            self._decide(t, ranked, on_screen, now, diag, edge_only=t.get("edge", False))
            if (t["sid"] is not None and not t.get("edge") and ranked and ranked[0][0] == t["sid"]
                    and ranked[0][1] >= LEARN_SCORE
                    and ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else -1.0) >= LEARN_MARGIN):
                self._clear.append((t["sid"], t["emb"], ranked[0][1]))

        for t in next_tracks:
            if t["sid"] is not None:
                self._last_seen[t["sid"]] = (t["bbox"], now)
        self._tracks = next_tracks

        # report each student once (best score), like analyze_all
        best: dict[int, dict] = {}
        unknowns: list[tuple[np.ndarray, tuple, int]] = []
        for t in next_tracks:
            if t["sid"] is None:
                # skip brand-new boxes that haven't been embedded yet, and
                # weak detections at a tile's edge (maybe not a face at all)
                if t["emb"] is not None and not t.get("edge"):
                    unknowns.append((t["emb"], t["bbox"], t["uid"]))
            elif t["sid"] not in best or t["score"] > best[t["sid"]]["score"]:
                best[t["sid"]] = t
        matches = [(sid, t["score"], t["bbox"]) for sid, t in best.items()]
        return matches, unknowns

    def _decide(self, t: dict, ranked: list[tuple[int, float]], on_screen: set[int],
                now: float, diag: float, edge_only: bool = False) -> None:
        """Set a track's identity from a fresh embedding (see KEEP_SCORE etc.).
        `edge_only`: a weak detection at a tile's edge, which must also beat
        every other student clearly to be anyone."""
        t["embedded_at"] = now
        best_sid, best = ranked[0] if ranked else (None, -1.0)
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        had = t.get("sid")

        if best_sid is not None and best >= MATCH_THRESHOLD and (not edge_only or best - second >= CLAIM_MARGIN):
            t["sid"], t["score"], t["misses"], t["weak"] = best_sid, best, 0, False
            return
        if had is not None and best_sid == had and best >= KEEP_SCORE:
            # Same person, seen less well (turned, half hidden): keep them.
            t["score"], t["misses"], t["weak"] = best, 0, True
            t["embedded_at"] = now - self._refresh_every + RETRY_SOON
            return
        if had is not None and t.get("misses", 0) + 1 < MISSES_TO_DROP:
            # One bad look is not enough to lose someone: check again soon.
            t["misses"] = t.get("misses", 0) + 1
            t["weak"] = True
            t["embedded_at"] = now - self._refresh_every + RETRY_SOON
            return
        if (best_sid is not None and best >= CLAIM_SCORE and best - second >= CLAIM_MARGIN
                and best_sid not in on_screen and self._near_last(best_sid, t["bbox"], now, diag)):
            # A new, partly visible face where a missing student just was.
            t["sid"], t["score"], t["misses"], t["weak"] = best_sid, best, 0, True
            t["embedded_at"] = now - self._refresh_every + RETRY_SOON
            return
        t["sid"], t["score"], t["misses"], t["weak"] = None, 0.0, 0, False

    def _near_last(self, sid: int, bbox: tuple, now: float, diag: float) -> bool:
        last = self._last_seen.get(sid)
        if last is None:
            return False
        (x1, y1, x2, y2), at = last
        if now - at > CLAIM_RECENT:
            return False
        bx1, by1, bx2, by2 = bbox
        d = np.hypot((x1 + x2 - bx1 - bx2) / 2, (y1 + y2 - by1 - by2) / 2)
        return d <= CLAIM_NEAR * diag


def rank_top2(embedding: np.ndarray, known: list[tuple[int, np.ndarray]]) -> list[tuple[int, float]]:
    """The two best (student_id, score) for an embedding, best first; one
    entry per student (their best enrolled picture)."""
    best: dict[int, float] = {}
    for sid, emb in known:
        sc = float(np.dot(embedding, emb))
        if sc > best.get(sid, -2.0):
            best[sid] = sc
    return sorted(best.items(), key=lambda kv: -kv[1])[:2]

"""Cheap identity tracking across meeting-tile frames.

Full recognition (detect + embed every face) costs >1s per pass on CPU, which
made monitoring sluggish. Meeting tiles barely move, so identities are sticky:
detect faces every pass (fast), carry identity forward for boxes that overlap
the previous pass, and only compute embeddings for faces that are new or
haven't been re-verified recently. Steady-state cost is detection only.
"""

from __future__ import annotations

import time
from typing import Callable

import numpy as np

from app.core.face_engine import FaceEngine

REFRESH_EVERY = 10.0     # re-verify each face's identity at most this often
IOU_MATCH = 0.3          # box overlap needed to carry identity forward
MAX_EMBEDS_PER_PASS = 2  # spread expensive embeddings across passes


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
        # each track: {bbox, kps, sid, score, emb, embedded_at}
        self._tracks: list[dict] = []

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

    def process(self, frame: np.ndarray) -> tuple[
        list[tuple[int, float, tuple[int, int, int, int]]],
        list[tuple[np.ndarray | None, tuple[int, int, int, int]]],
    ]:
        """One pass: detect, carry identities forward, embed only what's needed.

        Returns (matches, unknowns) shaped like FaceEngine.analyze_all.
        """
        detections = self._engine.detect_faces(frame)
        now = time.monotonic()

        pool = list(self._tracks)
        next_tracks: list[dict] = []
        for bbox, _det_score, kps in detections:
            best_track, best_iou = None, IOU_MATCH
            for t in pool:
                overlap = _iou(bbox, t["bbox"])
                if overlap >= best_iou:
                    best_track, best_iou = t, overlap
            if best_track is not None:
                pool.remove(best_track)
                best_track["bbox"], best_track["kps"] = bbox, kps
                next_tracks.append(best_track)
            else:
                next_tracks.append({
                    "bbox": bbox, "kps": kps, "sid": None, "score": 0.0,
                    "emb": None, "embedded_at": None,
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
        for t in candidates[:MAX_EMBEDS_PER_PASS]:
            t["emb"] = self._engine.embed_face(frame, t["bbox"], t["kps"])
            match = FaceEngine.identify(t["emb"], known)
            t["sid"], t["score"] = match if match else (None, 0.0)
            t["embedded_at"] = now

        self._tracks = next_tracks

        # report each student once (best score), like analyze_all
        best: dict[int, dict] = {}
        unknowns: list[tuple[np.ndarray | None, tuple]] = []
        for t in next_tracks:
            if t["sid"] is None:
                # skip brand-new boxes that haven't been embedded yet
                if t["emb"] is not None:
                    unknowns.append((t["emb"], t["bbox"]))
            elif t["sid"] not in best or t["score"] > best[t["sid"]]["score"]:
                best[t["sid"]] = t
        matches = [(sid, t["score"], t["bbox"]) for sid, t in best.items()]
        return matches, unknowns

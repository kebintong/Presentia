"""Face detection, embedding and matching built on InsightFace (buffalo_l, CPU)."""

from __future__ import annotations

import threading

import numpy as np

# Cosine similarity on normalized embeddings; >= threshold counts as a match.
MATCH_THRESHOLD = 0.45


class FaceEngine:
    """Lazily-initialized singleton around InsightFace's FaceAnalysis pipeline.

    First call downloads the buffalo_l model pack (~300 MB) if not cached, so
    call `FaceEngine.instance()` from a background thread.
    """

    _instance: "FaceEngine | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        from insightface.app import FaceAnalysis

        self._app = FaceAnalysis(
            name="buffalo_l",
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        self._app.prepare(ctx_id=0, det_size=(640, 640))

    @classmethod
    def instance(cls) -> "FaceEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    @classmethod
    def is_ready(cls) -> bool:
        return cls._instance is not None

    # ------------------------------------------------------------------

    def detect_faces(
        self, frame_bgr: np.ndarray
    ) -> list[tuple[tuple[int, int, int, int], float, np.ndarray]]:
        """Detection only (no identity embedding) — much cheaper per pass.

        Returns (bbox, det_score, keypoints) per face.
        """
        bboxes, kpss = self._app.det_model.detect(frame_bgr, max_num=0, metric="default")
        out = []
        for i in range(bboxes.shape[0]):
            bbox = tuple(int(v) for v in bboxes[i, :4])
            out.append((bbox, float(bboxes[i, 4]), kpss[i]))
        return out

    def embed_face(
        self, frame_bgr: np.ndarray, bbox: tuple, kps: np.ndarray
    ) -> np.ndarray:
        """Identity embedding for one already-detected face."""
        from insightface.app.common import Face

        face = Face(bbox=np.asarray(bbox, dtype=np.float32), kps=kps, det_score=1.0)
        self._app.models["recognition"].get(frame_bgr, face)
        return np.asarray(face.normed_embedding, dtype=np.float32)

    def largest_face(self, frame_bgr: np.ndarray):
        """Return the largest detected face object, or None."""
        faces = self._app.get(frame_bgr)
        if not faces:
            return None
        return max(
            faces,
            key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
        )

    def embed_largest(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        """L2-normalized 512-d embedding of the largest face, or None."""
        face = self.largest_face(frame_bgr)
        if face is None:
            return None
        return np.asarray(face.normed_embedding, dtype=np.float32)

    @staticmethod
    def similarity(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    @staticmethod
    def identify(
        embedding: np.ndarray,
        known: list[tuple[int, np.ndarray]],
        threshold: float = MATCH_THRESHOLD,
    ) -> tuple[int, float] | None:
        """Best (student_id, score) among `known`, or None if below threshold."""
        best_id, best_score = None, -1.0
        for student_id, emb in known:
            score = float(np.dot(embedding, emb))
            if score > best_score:
                best_id, best_score = student_id, score
        if best_id is None or best_score < threshold:
            return None
        return best_id, best_score

    def identify_all(
        self,
        frame_bgr: np.ndarray,
        known: list[tuple[int, np.ndarray]],
        threshold: float = MATCH_THRESHOLD,
    ) -> list[tuple[int, float, tuple[int, int, int, int]]]:
        """Recognize every face in the frame (e.g. a grid of meeting tiles).

        Returns one (student_id, score, bbox) per recognized student; each
        student is reported at most once, keeping their best-scoring face.
        """
        matches, _ = self.analyze_all(frame_bgr, known, threshold)
        return matches

    def analyze_all(
        self,
        frame_bgr: np.ndarray,
        known: list[tuple[int, np.ndarray]],
        threshold: float = MATCH_THRESHOLD,
    ) -> tuple[
        list[tuple[int, float, tuple[int, int, int, int]]],
        list[tuple[np.ndarray, tuple[int, int, int, int]]],
    ]:
        """Like identify_all, but also returns unrecognized faces.

        Returns (matches, unknowns) where matches are (student_id, score,
        bbox) — one per student, best score kept — and unknowns are
        (embedding, bbox) for every face that matched nobody.
        """
        best: dict[int, tuple[float, tuple[int, int, int, int]]] = {}
        unknowns: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []
        for face in self._app.get(frame_bgr):
            emb = np.asarray(face.normed_embedding, dtype=np.float32)
            bbox = tuple(int(v) for v in face.bbox)
            match = self.identify(emb, known, threshold)
            if match is None:
                unknowns.append((emb, bbox))
                continue
            student_id, score = match
            if student_id not in best or score > best[student_id][0]:
                best[student_id] = (score, bbox)
        matches = [(sid, score, bbox) for sid, (score, bbox) in best.items()]
        return matches, unknowns

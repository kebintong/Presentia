"""Face detection, embedding and matching built on InsightFace (buffalo_l).

Runs on the CPU or on a GPU chosen in Settings (see app.core.perf)."""

from __future__ import annotations

import contextlib
import threading

import numpy as np

# Cosine similarity on normalized embeddings; >= threshold counts as a match.
MATCH_THRESHOLD = 0.45


@contextlib.contextmanager
def _default_session_options(so):
    """InsightFace builds its onnxruntime sessions without SessionOptions,
    so there is no way to pass thread limits through it. Supply ours as the
    default for sessions created inside this block."""
    import onnxruntime as ort

    original = ort.InferenceSession.__init__

    def patched(self, path_or_bytes, sess_options=None, providers=None,
                provider_options=None, **kwargs):
        return original(self, path_or_bytes, sess_options or so, providers,
                        provider_options, **kwargs)

    ort.InferenceSession.__init__ = patched
    try:
        yield
    finally:
        ort.InferenceSession.__init__ = original


class FaceEngine:
    """Lazily-initialized singleton around InsightFace's FaceAnalysis pipeline.

    First call downloads the buffalo_l model pack (~300 MB) if not cached, so
    call `FaceEngine.instance()` from a background thread.

    Which device runs the models, and how many CPU threads they may use,
    comes from app.core.perf (Settings → Performance). `reconfigure()`
    rebuilds the engine with new settings and swaps it in once it works.
    """

    _instance: "FaceEngine | None" = None
    _lock = threading.Lock()
    _build_lock = threading.Lock()
    _status: dict = {"state": "loading"}

    def __init__(self, settings: dict | None = None) -> None:
        from insightface.app import FaceAnalysis

        from app.core import perf

        s = settings or perf.get_settings()
        high = bool(s.get("high_performance"))
        last_exc: Exception | None = None
        # Best plan first (the chosen GPU), CPU last. A GPU that fails to
        # initialise or to run falls back to the CPU so the app always works.
        for plan in perf.engine_plans(s):
            try:
                so = perf.session_options(plan["backend"], high)
                with _default_session_options(so):
                    app = FaceAnalysis(
                        name="buffalo_l",
                        allowed_modules=["detection", "recognition"],
                        providers=plan["providers"],
                        provider_options=plan["provider_options"],
                    )
                    app.prepare(ctx_id=0, det_size=(640, 640))
                # Warm-up: prove the device can actually run both models.
                app.det_model.detect(np.zeros((480, 640, 3), dtype=np.uint8), max_num=0)
                app.models["recognition"].get_feat(np.zeros((112, 112, 3), dtype=np.uint8))
                used = app.det_model.session.get_providers()[0]
                self._app = app
                self.backend = {"DmlExecutionProvider": "DirectML",
                                "CUDAExecutionProvider": "CUDA"}.get(used, "CPU")
                self.device = plan["device"] if self.backend != "CPU" else "CPU"
                self.threads = so.intra_op_num_threads
                self.fell_back = last_exc is not None or plan["backend"] != self.backend
                self.error = str(last_exc) if last_exc else None
                break
            except Exception as exc:  # noqa: BLE001 - try the next plan
                last_exc = exc
        else:
            raise last_exc  # type: ignore[misc]

    @classmethod
    def instance(cls) -> "FaceEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._status = {"state": "ready"}
        return cls._instance

    @classmethod
    def is_ready(cls) -> bool:
        return cls._instance is not None

    @classmethod
    def status(cls) -> dict:
        """What is running the models now, for the Settings panel."""
        inst = cls._instance
        out = dict(cls._status)
        if inst is not None:
            for key in ("backend", "device", "threads", "fell_back", "error"):
                out[key] = getattr(inst, key, None)
        return out

    @classmethod
    def reconfigure(cls) -> None:
        """Rebuild with the saved settings in the background. The current
        engine keeps serving until the new one has loaded and passed its
        warm-up; a running monitoring session keeps its engine until it ends."""

        def _build() -> None:
            with cls._build_lock:
                cls._status = {"state": "applying"}
                try:
                    new = cls()
                except Exception as exc:  # noqa: BLE001
                    cls._status = {"state": "error", "message": str(exc)}
                    return
                with cls._lock:
                    cls._instance = new
                cls._status = {"state": "ready"}

        threading.Thread(target=_build, daemon=True).start()

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

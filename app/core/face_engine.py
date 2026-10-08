"""Face detection, embedding and matching.

Runs the models directly with onnxruntime (see app.core.face_models) on the
CPU or a GPU chosen in Settings → Performance (see app.core.perf). The
performance profile picks the detectors; the ArcFace recogniser is the same
in every profile, so embeddings — and enrolled students — never change.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np

from app.core import face_models as fm

# Cosine similarity on normalized embeddings; >= threshold counts as a match.
MATCH_THRESHOLD = 0.45


class FaceEngine:
    """Lazily-initialized singleton holding the detectors and the recogniser.

    First use may download model files, so call `FaceEngine.instance()` from a
    background thread. `reconfigure()` rebuilds it with new settings and
    swaps it in once it works.
    """

    _instance: "FaceEngine | None" = None
    _lock = threading.Lock()
    _build_lock = threading.Lock()
    _status: dict = {"state": "loading"}

    def __init__(self, settings: dict | None = None) -> None:
        from app.core import perf

        s = settings or perf.get_settings()
        # First launch, or new hardware/driver: time the devices first so
        # "Auto" picks the fastest one and a fitting profile.
        if s.get("device", "auto") == "auto" or s.get("profile", "auto") == "auto":
            FaceEngine._status = {"state": "loading", "message": "Checking this computer's speed…"}
            try:
                perf.benchmark()
            except Exception:  # noqa: BLE001 - fall back to heuristics
                pass
            s = perf.get_settings()
        self.profile = perf.effective_profile(s)
        params = perf.PROFILES[self.profile]

        last_exc: Exception | None = None
        for plan in perf.engine_plans(s):
            try:
                make = perf.session_maker(plan, params["threads"])
                FaceEngine._status = {"state": "loading", "message": "Loading face models…"}
                rec = fm.ArcFace(fm.model_path("arcface"), make)
                meet, meet_name = self._detector(params["meet_detector"], params["meet_det_size"], make)
                if params["cam_detector"] == params["meet_detector"]:
                    cam, cam_name = meet, meet_name
                else:
                    cam, cam_name = self._detector(params["cam_detector"], params["cam_det_size"], make)
                # Warm-up: prove the device can actually run the models.
                meet.detect(np.zeros((480, 640, 3), dtype=np.uint8))
                rec.embed_aligned(np.zeros((112, 112, 3), dtype=np.uint8))
                used = rec.session.get_providers()[0]
                self.rec, self.det_meet, self.det_cam = rec, meet, cam
                self.cam_det_size = params["cam_det_size"]
                self.backend = {"DmlExecutionProvider": "DirectML",
                                "CUDAExecutionProvider": "CUDA"}.get(used, "CPU")
                self.device = plan["device"] if self.backend != "CPU" else "CPU"
                self.threads = perf.cpu_threads(params["threads"])
                self.detectors = {"camera": cam_name, "meeting": meet_name}
                self.fell_back = last_exc is not None or plan["backend"] != self.backend
                self.error = str(last_exc) if last_exc else None
                break
            except Exception as exc:  # noqa: BLE001 - try the next plan
                last_exc = exc
        else:
            raise last_exc  # type: ignore[misc]

    @staticmethod
    def _detector(name: str, det_size: int, make):
        """(detector, name actually used)."""
        if name == "yunet":
            try:
                return fm.YuNet(fm.model_path("yunet"), max_side=det_size), "yunet"
            except Exception:  # noqa: BLE001 - YuNet unavailable: use the light SCRFD
                name, det_size = "scrfd_2.5g", 480
        return fm.SCRFD(fm.model_path(name), make, det_size=det_size), name

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
        if fm.download_state.get("active"):
            d = fm.download_state
            out["message"] = (f"Downloading {d['file']} "
                              f"({d['done_mb']:.0f} of {max(d['total_mb'], d['done_mb']):.0f} MB)…")
        if inst is not None:
            for key in ("backend", "device", "threads", "fell_back", "error", "profile", "detectors"):
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
        self, frame_bgr: np.ndarray, source: str = "meeting", min_score: float | None = None,
    ) -> list[tuple[tuple[int, int, int, int], float, np.ndarray]]:
        """Detection only (no identity embedding) — much cheaper per pass.

        Returns (bbox, det_score, keypoints) per face. `source` picks the
        detector: "meeting" (screen captures, many small faces), "camera"
        (a webcam, one close face) or "photo" (uploaded pictures).
        `min_score` lowers the detector's own threshold, for faces cut off
        by the edge of a meeting tile (the caller decides which to keep).
        """
        det = self.det_cam if source == "camera" else self.det_meet
        if min_score is None:
            return det.detect(frame_bgr)
        return det.detect(frame_bgr, thresh=min_score)

    def embed_face(self, frame_bgr: np.ndarray, bbox: tuple, kps: np.ndarray) -> np.ndarray:
        """Identity embedding for one already-detected face."""
        return self.rec.embed(frame_bgr, kps)

    def largest_face(self, frame_bgr: np.ndarray, source: str = "photo"):
        """The largest detected face (with its embedding), or None.

        Returns an object with .bbox, .kps, .det_score and .normed_embedding.
        """
        faces = self.detect_faces(frame_bgr, source)
        if not faces:
            return None
        bbox, score, kps = max(faces, key=lambda f: (f[0][2] - f[0][0]) * (f[0][3] - f[0][1]))
        return SimpleNamespace(bbox=np.asarray(bbox, dtype=np.float32), kps=kps, det_score=score,
                               normed_embedding=self.rec.embed(frame_bgr, kps))

    def embed_largest(self, frame_bgr: np.ndarray, source: str = "photo") -> np.ndarray | None:
        """L2-normalized 512-d embedding of the largest face, or None."""
        face = self.largest_face(frame_bgr, source)
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
        """Recognize every face in the frame (e.g. a grid of meeting tiles)."""
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
        """Every face in the frame: (matches, unknowns), one match per student."""
        best: dict[int, tuple[float, tuple[int, int, int, int]]] = {}
        unknowns: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []
        for bbox, _score, kps in self.detect_faces(frame_bgr, "meeting"):
            emb = self.rec.embed(frame_bgr, kps)
            match = self.identify(emb, known, threshold)
            if match is None:
                unknowns.append((emb, bbox))
                continue
            student_id, score = match
            if student_id not in best or score > best[student_id][0]:
                best[student_id] = (score, bbox)
        matches = [(sid, score, bbox) for sid, (score, bbox) in best.items()]
        return matches, unknowns

"""Photo and screen-replay detection for check-in (optional).

Uses the two MiniFASNet models from minivision's Silent-Face-Anti-Spoofing
(Apache-2.0), as ONNX exports from github.com/yakhyo/face-anti-spoofing
(Apache-2.0). Each looks at a differently scaled crop around the face (2.7x
and 4.0x the face box) — enough surrounding context to see a phone bezel, a
paper edge or screen moiré — and the two opinions are averaged, exactly as
the original project does.

Off by default (Settings → Accessibility): it adds a small model run on
every check-in frame it samples.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.core import face_models as fm

_URL = "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/"

fm.MODELS.update({
    "fas_v2": fm.ModelFile("fas_v2", "MiniFASNetV2.onnx", fm._INSIGHTFACE_DIR / "antispoof",
                           url=_URL + "MiniFASNetV2.onnx", approx_mb=1.8),
    "fas_v1se": fm.ModelFile("fas_v1se", "MiniFASNetV1SE.onnx", fm._INSIGHTFACE_DIR / "antispoof",
                             url=_URL + "MiniFASNetV1SE.onnx", approx_mb=1.8),
})

REAL_THRESHOLD = 0.5  # averaged "real" probability needed to pass


def _crop(img: np.ndarray, bbox, scale: float, out_w: int, out_h: int) -> np.ndarray:
    """Silent-Face-Anti-Spoofing's CropImage: a scaled box around the face,
    shifted (not shrunk) to stay inside the frame."""
    src_h, src_w = img.shape[:2]
    x, y = bbox[0], bbox[1]
    bw, bh = max(1.0, bbox[2] - bbox[0]), max(1.0, bbox[3] - bbox[1])
    scale = min((src_h - 1) / bh, min((src_w - 1) / bw, scale))
    nw, nh = bw * scale, bh * scale
    cx, cy = bw / 2 + x, bh / 2 + y
    x1, y1, x2, y2 = cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2
    if x1 < 0:
        x2 -= x1
        x1 = 0
    if y1 < 0:
        y2 -= y1
        y1 = 0
    if x2 > src_w - 1:
        x1 -= x2 - src_w + 1
        x2 = src_w - 1
    if y2 > src_h - 1:
        y1 -= y2 - src_h + 1
        y2 = src_h - 1
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    return cv2.resize(img[y1:y2 + 1, x1:x2 + 1], (out_w, out_h))


@dataclass
class _Model:
    session: object
    scale: float
    w: int
    h: int
    name: str


class AntiSpoof:
    """Averages MiniFASNetV2 (2.7x crop) and MiniFASNetV1SE (4.0x crop)."""

    _instance: "AntiSpoof | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        import onnxruntime as ort

        # Tiny models: always on the CPU, one thread, so they never compete
        # with recognition or the live preview.
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.log_severity_level = 3
        self.models: list[_Model] = []
        for key, scale in (("fas_v2", 2.7), ("fas_v1se", 4.0)):
            path: Path = fm.model_path(key)
            sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
            shape = sess.get_inputs()[0].shape  # [N, 3, H, W]
            h = shape[2] if isinstance(shape[2], int) else 80
            w = shape[3] if isinstance(shape[3], int) else 80
            self.models.append(_Model(sess, scale, w, h, key))

    @classmethod
    def instance(cls) -> "AntiSpoof":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def real_probability(self, frame_bgr: np.ndarray, bbox) -> float:
        """0..1 — how likely this face is a live person rather than a print
        or a screen. Input: BGR frame and the face box (x1, y1, x2, y2)."""
        probs = []
        for m in self.models:
            crop = _crop(frame_bgr, bbox, m.scale, m.w, m.h)
            # Silent-Face feeds raw BGR pixel values (0-255), CHW, float.
            blob = crop.astype(np.float32).transpose(2, 0, 1)[None]
            inp = m.session.get_inputs()[0].name
            out = np.asarray(m.session.run(None, {inp: blob})[0][0], dtype=np.float64)
            if not (np.all(out >= 0) and abs(out.sum() - 1.0) < 1e-3):
                out = np.exp(out - out.max())
                out /= out.sum()
            probs.append(float(out[1]))  # class 1 = real face
        return float(np.mean(probs))


class SpoofVote:
    """Collects scores over a check-in and gives a verdict at the end, so a
    single odd frame can neither pass nor fail anyone."""

    def __init__(self, every: int = 3) -> None:
        self.every = every
        self._n = 0
        self.scores: list[float] = []

    def maybe_add(self, frame_bgr: np.ndarray, bbox) -> None:
        self._n += 1
        if self._n % self.every:
            return
        try:
            self.scores.append(AntiSpoof.instance().real_probability(frame_bgr, bbox))
        except Exception:  # noqa: BLE001 - model unavailable: do not block check-in
            pass

    def verdict(self) -> tuple[bool, float | None]:
        """(looks live, mean score). No samples → allowed (fail open)."""
        if len(self.scores) < 3:
            return True, None
        s = float(np.median(self.scores))
        return s >= REAL_THRESHOLD, s

"""Face detection and recognition models, run directly with onnxruntime.

This replaces the ``insightface`` Python package. We only ever used two of
its model files — an SCRFD face detector and the ArcFace ``w600k_r50``
recogniser — and the package dragged in scikit-image, scipy, matplotlib,
albumentations and more. The code below is a faithful port of the parts we
used (SCRFD decoding + NMS, the ArcFace 5-point alignment and embedding), so
embeddings are the same as before and every enrolled student keeps matching.

Detectors (all produce the same 5 landmarks, so all feed the same ArcFace):
  scrfd_10g   the original buffalo_l detector  (High profile)
  scrfd_2.5g  a ~4x cheaper SCRFD             (Balanced / Low meetings)
  yunet       OpenCV's built-in tiny detector  (Low profile webcam pages)

Model files are fetched on first use. From the InsightFace release zips only
the members we need are downloaded (HTTP range requests), and files already
present from older versions of Presentia (~/.insightface) are reused.
"""

from __future__ import annotations

import os
import struct
import sys
import threading
import time
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

# ── Model catalogue ───────────────────────────────────────────────────────────

_IF_RELEASE = "https://github.com/deepinsight/insightface/releases/download/v0.7/"
_INSIGHTFACE_DIR = Path.home() / ".insightface" / "models"
_BUNDLED_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "models"


@dataclass(frozen=True)
class ModelFile:
    key: str
    filename: str
    local_dir: Path          # where it is stored once downloaded
    zip_url: str | None = None   # fetched as one member of this zip...
    url: str | None = None       # ...or directly from here
    approx_mb: float = 0.0


MODELS = {
    "scrfd_10g": ModelFile("scrfd_10g", "det_10g.onnx", _INSIGHTFACE_DIR / "buffalo_l",
                           zip_url=_IF_RELEASE + "buffalo_l.zip", approx_mb=17),
    "scrfd_2.5g": ModelFile("scrfd_2.5g", "det_2.5g.onnx", _INSIGHTFACE_DIR / "buffalo_m",
                            zip_url=_IF_RELEASE + "buffalo_m.zip", approx_mb=3.5),
    "arcface": ModelFile("arcface", "w600k_r50.onnx", _INSIGHTFACE_DIR / "buffalo_l",
                         zip_url=_IF_RELEASE + "buffalo_l.zip", approx_mb=175),
    "yunet": ModelFile("yunet", "face_detection_yunet_2023mar.onnx", _INSIGHTFACE_DIR / "opencv",
                       url="https://github.com/opencv/opencv_zoo/raw/main/models/"
                           "face_detection_yunet/face_detection_yunet_2023mar.onnx", approx_mb=0.3),
}


# ── Download (range requests into the release zips) ──────────────────────────

_dl_lock = threading.Lock()
download_state: dict = {"active": False, "file": "", "done_mb": 0.0, "total_mb": 0.0}


def model_path(key: str, progress: bool = True) -> Path:
    """Local path of a model file, downloading it first if needed."""
    m = MODELS[key]
    for candidate in (_BUNDLED_DIR / m.filename, m.local_dir / m.filename):
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    with _dl_lock:
        dest = m.local_dir / m.filename
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        download_state.update(active=True, file=m.filename, done_mb=0.0, total_mb=m.approx_mb)
        try:
            if m.zip_url:
                _fetch_zip_member(m.zip_url, m.filename, tmp)
            else:
                _fetch_url(m.url, tmp)
            os.replace(tmp, dest)
        finally:
            download_state["active"] = False
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return dest


def _open(url: str, headers: dict | None = None, timeout: float = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "Presentia", **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _copy_stream(src, dst, total: int) -> None:
    done = 0
    while True:
        chunk = src.read(1 << 20)
        if not chunk:
            break
        dst.write(chunk)
        done += len(chunk)
        download_state["done_mb"] = done / 1e6
        if total:
            download_state["total_mb"] = total / 1e6


def _fetch_url(url: str, dest: Path) -> None:
    with _open(url) as r, open(dest, "wb") as f:
        _copy_stream(r, f, int(r.headers.get("Content-Length") or 0))


def _range(url: str, start: int, end: int) -> bytes:
    """Bytes [start, end] of url, or raise if the server ignores Range."""
    with _open(url, {"Range": f"bytes={start}-{end}"}) as r:
        if r.status != 206:
            raise OSError("server does not support range requests")
        return r.read()


def _fetch_zip_member(zip_url: str, name: str, dest: Path) -> None:
    """Download one file out of a remote zip without fetching the rest.

    Reads the zip's central directory from the end of the file, then only
    the chosen member's bytes. Falls back to downloading the whole zip if
    the server does not honour Range requests.
    """
    try:
        with _open(zip_url, {"Range": "bytes=0-0"}) as r:
            if r.status != 206:
                raise OSError("no range support")
            size = int(r.headers["Content-Range"].split("/")[-1])
        tail = _range(zip_url, max(0, size - 65557), size - 1)
        eocd = tail.rfind(b"PK\x05\x06")
        if eocd < 0:
            raise OSError("zip directory not found")
        cd_size, cd_offset = struct.unpack("<II", tail[eocd + 12:eocd + 20])
        cd = _range(zip_url, cd_offset, cd_offset + cd_size - 1)
        pos = 0
        while pos + 46 <= len(cd) and cd[pos:pos + 4] == b"PK\x01\x02":
            (method, crc, csize, _usize, nlen, xlen, clen,
             local_off) = (struct.unpack("<H", cd[pos + 10:pos + 12])[0],
                           *struct.unpack("<III", cd[pos + 16:pos + 28]),
                           *struct.unpack("<HHH", cd[pos + 28:pos + 34]),
                           struct.unpack("<I", cd[pos + 42:pos + 46])[0])
            fname = cd[pos + 46:pos + 46 + nlen].decode("utf-8", "replace")
            pos += 46 + nlen + xlen + clen
            if fname.rsplit("/", 1)[-1] != name:
                continue
            head = _range(zip_url, local_off, local_off + 29)
            lnlen, lxlen = struct.unpack("<HH", head[26:30])
            start = local_off + 30 + lnlen + lxlen
            download_state["total_mb"] = csize / 1e6
            with _open(zip_url, {"Range": f"bytes={start}-{start + csize - 1}"}) as r:
                if r.status != 206:
                    raise OSError("range refused")
                _extract_stream(r, dest, method, crc, csize)
            return
        raise FileNotFoundError(f"{name} not found in {zip_url}")
    except FileNotFoundError:
        raise
    except Exception:  # noqa: BLE001 - fall back to the whole zip
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / "pack.zip"
            _fetch_url(zip_url, zpath)
            with zipfile.ZipFile(zpath) as zf:
                member = next(n for n in zf.namelist() if n.rsplit("/", 1)[-1] == name)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())


def _extract_stream(resp, dest: Path, method: int, crc: int, csize: int) -> None:
    inflater = zlib.decompressobj(-15) if method == 8 else None
    got = 0
    check = 0
    with open(dest, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
            download_state["done_mb"] = got / 1e6
            data = inflater.decompress(chunk) if inflater else chunk
            check = zlib.crc32(data, check)
            out.write(data)
        if inflater:
            tail = inflater.flush()
            check = zlib.crc32(tail, check)
            out.write(tail)
    if got != csize or (check & 0xFFFFFFFF) != crc:
        raise OSError("model download was corrupted — please retry")


# ── Shared helpers (ported from insightface) ─────────────────────────────────

def _nms(dets: np.ndarray, thresh: float = 0.4) -> list[int]:
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(ovr <= thresh)[0]
        order = order[inds + 1]
    return keep


def _distance2bbox(points: np.ndarray, d: np.ndarray) -> np.ndarray:
    return np.stack([points[:, 0] - d[:, 0], points[:, 1] - d[:, 1],
                     points[:, 0] + d[:, 2], points[:, 1] + d[:, 3]], axis=-1)


def _distance2kps(points: np.ndarray, d: np.ndarray) -> np.ndarray:
    preds = []
    for i in range(0, d.shape[1], 2):
        preds.append(points[:, i % 2] + d[:, i])
        preds.append(points[:, i % 2 + 1] + d[:, i + 1])
    return np.stack(preds, axis=-1)


_ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                         [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Similarity transform src→dst (scikit-image's estimate, used by insightface)."""
    num, dim = src.shape
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_d, dst_d = src - src_mean, dst - dst_mean
    a = dst_d.T @ src_d / num
    d = np.ones((dim,), dtype=np.float64)
    if np.linalg.det(a) < 0:
        d[dim - 1] = -1
    t = np.eye(dim + 1, dtype=np.float64)
    u, s, v = np.linalg.svd(a)
    rank = np.linalg.matrix_rank(a)
    if rank == 0:
        return np.nan * t
    if rank == dim - 1:
        if np.linalg.det(u) * np.linalg.det(v) > 0:
            t[:dim, :dim] = u @ v
        else:
            sv = d[dim - 1]
            d[dim - 1] = -1
            t[:dim, :dim] = u @ np.diag(d) @ v
            d[dim - 1] = sv
    else:
        t[:dim, :dim] = u @ np.diag(d) @ v
    scale = 1.0 / src_d.var(axis=0).sum() * (s @ d)
    t[:dim, dim] = dst_mean - scale * (t[:dim, :dim] @ src_mean.T)
    t[:dim, :dim] *= scale
    return t


def norm_crop(img: np.ndarray, kps: np.ndarray, size: int = 112) -> np.ndarray:
    """ArcFace alignment: warp the face so its 5 points land on the template."""
    m = _umeyama(np.asarray(kps, dtype=np.float64), _ARCFACE_DST.astype(np.float64) * (size / 112.0))
    return cv2.warpAffine(img, m[0:2, :], (size, size), borderValue=0.0)


def _order_kps(kps: np.ndarray) -> np.ndarray:
    """Eyes and mouth corners in image order (left first), as ArcFace expects."""
    k = np.array(kps, dtype=np.float32).reshape(5, 2)
    if k[0, 0] > k[1, 0]:
        k[[0, 1]] = k[[1, 0]]
    if k[3, 0] > k[4, 0]:
        k[[3, 4]] = k[[4, 3]]
    return k


# ── Detectors ────────────────────────────────────────────────────────────────

Detection = tuple[tuple[int, int, int, int], float, np.ndarray]  # bbox, score, kps(5,2)


class SCRFD:
    """insightface's SCRFD detector (det_10g / det_2.5g), same decoding."""

    def __init__(self, path: Path, make_session: Callable, det_size: int = 640, thresh: float = 0.5):
        self.session = make_session(str(path))
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        n = len(self.output_names)
        self.batched = len(self.session.get_outputs()[0].shape) == 3
        self.fmc, self.strides, self.anchors, self.use_kps = {
            6: (3, [8, 16, 32], 2, False), 9: (3, [8, 16, 32], 2, True),
            10: (5, [8, 16, 32, 64, 128], 1, False), 15: (5, [8, 16, 32, 64, 128], 1, True),
        }[n]
        self.det_size = det_size
        self.thresh = thresh
        self._centers: dict = {}

    def detect(self, img: np.ndarray, det_size: int | None = None,
               thresh: float | None = None) -> list[Detection]:
        size = det_size or self.det_size
        thresh = self.thresh if thresh is None else thresh
        h, w = img.shape[:2]
        if h / w > 1.0:
            nh, nw = size, int(size / (h / w))
        else:
            nw, nh = size, int(size * (h / w))
        scale = nh / h
        det_img = np.zeros((size, size, 3), dtype=np.uint8)
        det_img[:nh, :nw] = cv2.resize(img, (nw, nh))
        blob = cv2.dnn.blobFromImage(det_img, 1.0 / 128.0, (size, size),
                                     (127.5, 127.5, 127.5), swapRB=True)
        outs = self.session.run(self.output_names, {self.input_name: blob})
        scores_l, boxes_l, kps_l = [], [], []
        for idx, stride in enumerate(self.strides):
            if self.batched:
                sc, bb = outs[idx][0], outs[idx + self.fmc][0] * stride
                kp = outs[idx + self.fmc * 2][0] * stride if self.use_kps else None
            else:
                sc, bb = outs[idx], outs[idx + self.fmc] * stride
                kp = outs[idx + self.fmc * 2] * stride if self.use_kps else None
            fh, fw = size // stride, size // stride
            key = (fh, fw, stride)
            centers = self._centers.get(key)
            if centers is None:
                centers = np.stack(np.mgrid[:fh, :fw][::-1], axis=-1).astype(np.float32)
                centers = (centers * stride).reshape((-1, 2))
                if self.anchors > 1:
                    centers = np.stack([centers] * self.anchors, axis=1).reshape((-1, 2))
                self._centers[key] = centers
            pos = np.where(sc >= thresh)[0]
            scores_l.append(sc[pos])
            boxes_l.append(_distance2bbox(centers, bb)[pos])
            if kp is not None:
                kps_l.append(_distance2kps(centers, kp).reshape((-1, 5, 2))[pos])
        scores = np.vstack(scores_l).ravel()
        if scores.size == 0:
            return []
        order = scores.argsort()[::-1]
        boxes = np.vstack(boxes_l) / scale
        pre = np.hstack((boxes, scores[:, None])).astype(np.float32)[order]
        keep = _nms(pre)
        dets = pre[keep]
        kpss = (np.vstack(kps_l) / scale)[order][keep] if kps_l else np.zeros((len(keep), 5, 2))
        # SCRFD's landmarks are already in the order ArcFace expects; they are
        # passed through untouched so embeddings match insightface exactly.
        return [(tuple(int(v) for v in d[:4]), float(d[4]), np.asarray(k, dtype=np.float32))
                for d, k in zip(dets, kpss, strict=True)]


class YuNet:
    """OpenCV's FaceDetectorYN — tiny and fast; for single close-up faces."""

    def __init__(self, path: Path, max_side: int = 320, thresh: float = 0.6):
        self.max_side = max_side
        self._det = cv2.FaceDetectorYN.create(str(path), "", (320, 320), thresh, 0.3, 50)
        self._size = (0, 0)

    def detect(self, img: np.ndarray, det_size: int | None = None,
               thresh: float | None = None) -> list[Detection]:
        # The score threshold is fixed when the detector is created.
        h, w = img.shape[:2]
        k = min(1.0, (det_size or self.max_side) / max(h, w))
        small = cv2.resize(img, (int(w * k), int(h * k))) if k < 1.0 else img
        sh, sw = small.shape[:2]
        if (sw, sh) != self._size:
            self._det.setInputSize((sw, sh))
            self._size = (sw, sh)
        _, faces = self._det.detect(small)
        out: list[Detection] = []
        if faces is None:
            return out
        for f in faces:
            x, y, fw, fh = (f[:4] / k).tolist()
            kps = _order_kps(f[4:14].reshape(5, 2) / k)
            out.append(((int(x), int(y), int(x + fw), int(y + fh)), float(f[14]), kps))
        return out


# ── Recogniser ───────────────────────────────────────────────────────────────

class ArcFace:
    """insightface's w600k_r50 ArcFace: 112x112 aligned face → 512-d embedding."""

    def __init__(self, path: Path, make_session: Callable):
        self.session = make_session(str(path))
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]
        # buffalo_l's w600k_r50 expects (x - 127.5) / 127.5, RGB.
        self.mean, self.std = 127.5, 127.5

    def embed(self, img: np.ndarray, kps: np.ndarray) -> np.ndarray:
        """L2-normalised embedding of the face with these landmarks."""
        crop = norm_crop(img, kps)
        return self.embed_aligned(crop)

    def embed_aligned(self, crop: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImages([crop], 1.0 / self.std, (112, 112),
                                      (self.mean, self.mean, self.mean), swapRB=True)
        emb = self.session.run(self.output_names, {self.input_name: blob})[0][0]
        return (emb / np.linalg.norm(emb)).astype(np.float32)


def time_it(fn: Callable, runs: int = 5, warmup: int = 2) -> float:
    """Median milliseconds per call."""
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return float(np.median(samples))


__all__ = ["MODELS", "model_path", "download_state", "SCRFD", "YuNet", "ArcFace",
           "norm_crop", "time_it"]

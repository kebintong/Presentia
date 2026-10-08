"""Meeting video tiles: where they are, and what each one shows.

Google Meet (and Zoom / Teams galleries) lay participants out as tiles on a
plain background. Each tile shows either live video or, when the camera is
off, a still picture: the person's avatar (a circle, or their name) on a
flat, dark grey fill. That makes "is this camera on?" answerable without
seeing a face:

  video   the tile's picture keeps changing, or is not flat
  avatar  the meeting's camera-off picture: flat dark grey, at most a round
          avatar in the middle, and not changing

`TileWatch` remembers which tile a student was recognised in, so that when
their face can't be recognised for a while (turned away, half out of the
picture) Presentia can still tell whether their camera is on.

When tiles reflow (someone joins or leaves, the layout changes) the
remembered tile is forgotten, and that student has to be recognised again.

Everything here runs on a small copy of the frame (WORK_W pixels wide): a
few milliseconds per pass.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

WORK_W = 480            # tiles are found on a copy this wide
BG_TOL = 16             # a pixel this far from the background colour is "something"
GAP_OCC = 0.02          # a row/column with at most this share of "something" is a gap
MIN_GAP = 2             # px (work size) for a gap between tiles
MIN_SIDE = 0.07         # a tile is at least this share of the frame's width and height
MIN_FILL = 0.55         # share of a tile that differs from the background
ASPECT = (0.55, 2.6)    # width / height of a tile

FLAT_TOL = 4            # pixel this close to the tile's fill colour is "flat": the
                        # meeting paints camera-off tiles one exact colour, while
                        # a camera's picture of a wall has noise and shading
AVATAR_FLAT = 0.70      # share of flat pixels in a camera-off tile (avatar shown)
PLAIN_FLAT = 0.93       # …or with no avatar at all (just a name)
STILL_TOL = 0.8         # mean grey-level change between samples for "not changing"
AVATAR_SAMPLES = 3      # samples in a row that look like the camera-off picture
VIDEO_SAMPLES = 2       # samples in a row that look like video

KEEP_IOU = 0.7          # the same tile in the next pass
MEMORY_IOU = 0.8        # a student's remembered tile is still in the same place
MEMORY_MISSES = 3       # passes without that tile before it is forgotten

Rect = tuple[int, int, int, int]


def _iou(a: Rect, b: Rect) -> float:
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _contains(r: Rect, x: float, y: float) -> bool:
    return r[0] <= x < r[2] and r[1] <= y < r[3]


# ── finding tiles ────────────────────────────────────────────────────────────

def _small(frame: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = frame.shape[:2]
    k = min(1.0, WORK_W / float(w))
    if k < 1.0:
        frame = cv2.resize(frame, (int(round(w * k)), max(1, int(round(h * k)))),
                           interpolation=cv2.INTER_AREA)
    return frame, k


def background_colour(small: np.ndarray) -> np.ndarray:
    """The meeting's page colour: the most common colour along the left,
    right and bottom edges (the top may be the browser's own toolbar)."""
    h, w = small.shape[:2]
    bw, bh = max(2, w // 50), max(2, h // 25)
    edge = np.concatenate([
        small[:, :bw].reshape(-1, 3), small[:, w - bw:].reshape(-1, 3), small[h - bh:].reshape(-1, 3),
    ]).astype(np.int32)
    q = edge // 16
    keys = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    common = np.bincount(keys).argmax()
    return edge[keys == common].mean(axis=0)


def _gaps(occ: np.ndarray) -> list[tuple[int, int]]:
    """Runs of (almost) empty rows or columns: (start, end)."""
    empty = occ <= GAP_OCC
    runs, start = [], None
    for i, e in enumerate(empty):
        if e and start is None:
            start = i
        elif not e and start is not None:
            if i - start >= MIN_GAP:
                runs.append((start, i))
            start = None
    if start is not None and len(empty) - start >= MIN_GAP:
        runs.append((start, len(empty)))
    return runs


def _cut(fg: np.ndarray, x0: int, y0: int, x1: int, y1: int, depth: int, out: list[Rect]) -> None:
    """Recursive XY-cut: split at empty rows, then empty columns, and so on."""
    sub = fg[y0:y1, x0:x1]
    if sub.size == 0:
        return
    rows, cols = sub.any(axis=1), sub.any(axis=0)
    if not rows.any():
        return
    # Trim to what is there.
    ry = np.flatnonzero(rows)
    cx = np.flatnonzero(cols)
    x0, x1, y0, y1 = x0 + cx[0], x0 + cx[-1] + 1, y0 + ry[0], y0 + ry[-1] + 1
    sub = fg[y0:y1, x0:x1]
    if depth >= 8:
        out.append((x0, y0, x1, y1))
        return
    for axis in (1, 0):                    # rows first (tile rows), then columns
        occ = sub.mean(axis=axis)
        gaps = _gaps(occ)
        if not gaps:
            continue
        edges = [0] + [g for run in gaps for g in run] + [len(occ)]
        for a, b in zip(edges[::2], edges[1::2]):
            if b - a <= 0:
                continue
            if axis == 1:
                _cut(fg, x0, y0 + a, x1, y0 + b, depth + 1, out)
            else:
                _cut(fg, x0 + a, y0, x0 + b, y1, depth + 1, out)
        return
    out.append((x0, y0, x1, y1))


def find_tiles(frame: np.ndarray) -> list[Rect]:
    """Video tiles in a meeting picture, in frame pixels (x0, y0, x1, y1)."""
    if frame is None or frame.size == 0:
        return []
    return _find(*_small(frame))


def _find(small: np.ndarray, k: float) -> list[Rect]:
    h, w = small.shape[:2]
    bg = background_colour(small)
    diff = np.abs(small.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
    fg = diff > BG_TOL
    leaves: list[Rect] = []
    _cut(fg, 0, 0, w, h, 0, leaves)
    tiles = []
    for x0, y0, x1, y1 in leaves:
        tw, th = x1 - x0, y1 - y0
        if tw < MIN_SIDE * w or th < MIN_SIDE * h:
            continue
        if not ASPECT[0] <= tw / th <= ASPECT[1]:
            continue
        if fg[y0:y1, x0:x1].mean() < MIN_FILL:
            continue
        tiles.append((int(x0 / k), int(y0 / k), int(x1 / k), int(y1 / k)))
    return tiles


# ── what a tile shows ────────────────────────────────────────────────────────

def _inner(small: np.ndarray, r: Rect, k: float) -> tuple[np.ndarray, np.ndarray]:
    """(the tile without its rounded corners and name label strip,
        the middle of that bottom strip — where a cut-off head would be)."""
    x0, y0, x1, y1 = (int(v * k) for v in r)
    w, h = x1 - x0, y1 - y0
    mx, my = max(1, w // 16), max(1, h // 16)
    label = max(my, h * 18 // 100)
    body = small[y0 + my:y1 - label, x0 + mx:x1 - mx]
    strip = small[y1 - label:y1 - my, x0 + w * 3 // 10:x1 - w * 3 // 10]
    return body, strip


def _fill(patch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    px = patch.reshape(-1, 3).astype(np.int16)
    med = np.median(px, axis=0)
    return med, np.abs(px - med).max(axis=1) <= FLAT_TOL


def looks_like_camera_off(patch: np.ndarray, strip: np.ndarray | None = None,
                          fill: tuple[np.ndarray, np.ndarray] | None = None) -> bool:
    """One picture of a tile: one flat dark grey with at most a round avatar
    (or just a name) in the middle — the meeting's camera-off picture.
    `strip` is the middle of the tile's bottom edge, which must be empty too
    (a head cut off by the bottom of the tile shows there)."""
    if patch.size == 0:
        return False
    med, flat = fill if fill is not None else _fill(patch)
    # Camera-off fills are dark, neutral greys; a pale wall behind someone is not.
    if med.mean() > 120 or med.max() - med.min() > 24:
        return False
    if strip is not None and strip.size:
        sp = strip.reshape(-1, 3).astype(np.int16)
        if float((np.abs(sp - med).max(axis=1) <= FLAT_TOL).mean()) < 0.9:
            return False
    share = float(flat.mean())
    if share >= PLAIN_FLAT:
        return True
    if share < AVATAR_FLAT:
        return False
    # The rest must be one roundish blob in the middle (the avatar).
    mask = ~flat.reshape(patch.shape[:2])
    ys, xs = np.nonzero(mask)
    ph, pw = mask.shape
    bx0, bx1, by0, by1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    bw, bh = bx1 - bx0, by1 - by0
    cx, cy = (bx0 + bx1) / 2 / pw, (by0 + by1) / 2 / ph
    return (abs(cx - 0.5) <= 0.15 and abs(cy - 0.5) <= 0.2
            and bw <= 0.55 * pw and bh <= 0.7 * ph and 0.6 <= bw / max(1, bh) <= 1.7)


THUMB = (64, 36)


def _thumb(patch: np.ndarray, flat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(grey picture, mask of the flat fill) at a fixed size, to compare passes.
    Only the fill is compared: Meet animates a ring around the avatar of a
    camera-off participant who is speaking."""
    if patch.size == 0:
        return np.zeros(THUMB[::-1], np.float32), np.zeros(THUMB[::-1], bool)
    grey = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), THUMB, interpolation=cv2.INTER_AREA)
    mask = cv2.resize(flat.reshape(patch.shape[:2]).astype(np.uint8), THUMB,
                      interpolation=cv2.INTER_NEAREST).astype(bool)
    return grey.astype(np.float32), mask


@dataclass
class _Tile:
    rect: Rect
    kind: str | None = None       # "video" | "avatar" | None (not sure yet)
    thumb: tuple[np.ndarray, np.ndarray] | None = None
    avatar_run: int = 0
    video_run: int = 0


@dataclass
class _Memory:
    rect: Rect
    misses: int = 0


@dataclass
class TileWatch:
    """Finds tiles every pass and remembers each student's tile."""

    tiles: list[_Tile] = field(default_factory=list)
    memory: dict[int, _Memory] = field(default_factory=dict)
    _shape: tuple[int, int] | None = None
    last_pass: float = 0.0

    def reset(self) -> None:
        self.tiles, self.memory, self._shape = [], {}, None

    def update(self, frame: np.ndarray, matches) -> dict[int, str]:
        """One analysis pass. `matches` are (student_id, score, bbox) of the
        faces recognised in this frame. Returns, for remembered students who
        were NOT recognised now, what their tile shows: "video" or "avatar"."""
        self.last_pass = time.monotonic()
        shape = frame.shape[:2]
        if shape != self._shape:
            self.reset()                  # resized: every position is different now
            self._shape = shape
        small, k = _small(frame)
        old = self.tiles
        fresh: list[_Tile] = []
        for rect in _find(small, k):
            prev = max(old, key=lambda t: _iou(t.rect, rect), default=None)
            if prev is not None and _iou(prev.rect, rect) >= KEEP_IOU:
                old.remove(prev)
                prev.rect = rect
                fresh.append(prev)
            else:
                fresh.append(_Tile(rect))
        for t in fresh:
            self._classify(t, small, k)
        self.tiles = fresh

        # Which tile each recognised student is in now.
        seen: dict[int, _Tile] = {}
        for sid, _score, (x1, y1, x2, y2) in matches:
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            tile = next((t for t in fresh if _contains(t.rect, cx, cy)), None)
            if tile is not None:
                seen[sid] = tile
        for sid, tile in seen.items():
            # A tile belongs to whoever is recognised in it now.
            for other in [o for o, m in self.memory.items()
                          if o != sid and o not in seen and _iou(m.rect, tile.rect) >= MEMORY_IOU]:
                del self.memory[other]
            self.memory[sid] = _Memory(tile.rect)

        taken = [t for t in seen.values()]
        out: dict[int, str] = {}
        for sid, mem in list(self.memory.items()):
            if sid in seen:
                continue
            tile = max(fresh, key=lambda t: _iou(t.rect, mem.rect), default=None)
            if tile is None or _iou(tile.rect, mem.rect) < MEMORY_IOU or tile in taken:
                mem.misses += 1
                if mem.misses >= MEMORY_MISSES:
                    del self.memory[sid]     # the layout changed
                continue
            mem.misses = 0
            mem.rect = tile.rect
            if tile.kind is not None:
                out[sid] = tile.kind
        return out

    def _classify(self, t: _Tile, small: np.ndarray, k: float) -> None:
        patch, strip = _inner(small, t.rect, k)
        fill = _fill(patch) if patch.size else (np.zeros(3), np.zeros(0, bool))
        thumb = _thumb(patch, fill[1])
        still = False
        if t.thumb is not None:
            mask = thumb[1] & t.thumb[1]
            if mask.any():
                still = float(np.abs(thumb[0] - t.thumb[0])[mask].mean()) <= STILL_TOL
        t.thumb = thumb
        if still and looks_like_camera_off(patch, strip, fill):
            t.avatar_run += 1
            t.video_run = 0
        else:
            t.video_run += 1
            t.avatar_run = 0
        if t.avatar_run >= AVATAR_SAMPLES:
            t.kind = "avatar"
        elif t.video_run >= VIDEO_SAMPLES:
            t.kind = "video"

    def tile_of(self, sid: int) -> tuple[Rect, str | None] | None:
        """The student's remembered tile and what it shows (for Live View)."""
        mem = self.memory.get(sid)
        if mem is None:
            return None
        tile = max(self.tiles, key=lambda t: _iou(t.rect, mem.rect), default=None)
        kind = tile.kind if tile is not None and _iou(tile.rect, mem.rect) >= MEMORY_IOU else None
        return mem.rect, kind

    def near_edge(self, bbox) -> bool:
        """A face box touching the edge of its tile (cut off by it)."""
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if self._shape is not None:
            fh, fw = self._shape
            m = 0.02 * max(fw, fh)
            if x1 <= m or y1 <= m or fw - x2 <= m or fh - y2 <= m:
                return True
        for t in self.tiles:
            if _contains(t.rect, cx, cy):
                tx0, ty0, tx1, ty1 = t.rect
                m = 0.04 * max(tx1 - tx0, ty1 - ty0)
                return x1 - tx0 <= m or tx1 - x2 <= m or y1 - ty0 <= m or ty1 - y2 <= m
        return False

"""Meeting tiles: found on the page, and camera-on vs camera-off told apart."""

from __future__ import annotations

import cv2
import numpy as np

from app.core.meet_tiles import TileWatch, find_tiles, looks_like_camera_off

BG = (36, 33, 32)            # Meet's page colour #202124, BGR
OFF_FILL = (67, 64, 60)      # camera-off tile #3c4043, BGR
W, H = 1600, 900


def grid(n_cols=3, n_rows=2, gap=12, top=60, bottom=90):
    """Tile rectangles laid out like Meet's tiled view."""
    tw = (W - gap * (n_cols + 1)) // n_cols
    th = min((H - top - bottom - gap * (n_rows - 1)) // n_rows, tw * 9 // 16)
    rects = []
    for r in range(n_rows):
        for c in range(n_cols):
            x0 = gap + c * (tw + gap)
            y0 = top + r * (th + gap)
            rects.append((x0, y0, x0 + tw, y0 + th))
    return rects


def video_tile(img, r, rng, wall=(90, 110, 130)):
    x0, y0, x1, y1 = r
    tile = np.full((y1 - y0, x1 - x0, 3), wall, np.uint8)
    noise = rng.integers(-25, 25, tile.shape, dtype=np.int16)
    tile = np.clip(tile.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    # A person: a skin-coloured head and shoulders, moving a little.
    h, w = tile.shape[:2]
    dx = int(rng.integers(-6, 6))
    cv2.ellipse(tile, (w // 2 + dx, int(h * 0.45)), (w // 9, h // 5), 0, 0, 360, (120, 150, 200), -1)
    cv2.rectangle(tile, (w // 3 + dx, int(h * 0.68)), (2 * w // 3 + dx, h), (60, 40, 30), -1)
    img[y0:y1, x0:x1] = tile


def half_face_tile(img, r, rng):
    """Camera on, only the top of the head at the bottom edge, dark grey wall."""
    x0, y0, x1, y1 = r
    h, w = y1 - y0, x1 - x0
    # A camera's picture of a grey wall: a little shading and noise.
    shade = np.linspace(-8, 8, w)[None, :, None] + np.linspace(-5, 5, h)[:, None, None]
    tile = np.full((h, w, 3), (58, 60, 62), np.float32) + shade
    tile += rng.normal(0, 2.5, tile.shape)
    tile = np.clip(tile, 0, 255).astype(np.uint8)
    dx = int(rng.integers(-8, 8))
    cv2.ellipse(tile, (w // 2 + dx, h), (w // 8, h // 4), 0, 180, 360, (110, 140, 190), -1)
    img[y0:y1, x0:x1] = tile


def camera_off_tile(img, r, avatar=True):
    x0, y0, x1, y1 = r
    img[y0:y1, x0:x1] = OFF_FILL
    h, w = y1 - y0, x1 - x0
    if avatar:
        cv2.circle(img, (x0 + w // 2, y0 + h // 2), h // 6, (180, 110, 70), -1)
        cv2.putText(img, "K", (x0 + w // 2 - 12, y0 + h // 2 + 12), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                    (255, 255, 255), 2)
    # The name label, bottom left.
    cv2.putText(img, "Kian Santos", (x0 + 12, y1 - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1)


def page(rects, kinds, rng):
    img = np.full((H, W, 3), BG, np.uint8)
    # Meet's control bar: a few round buttons on the page colour.
    for i in range(5):
        cv2.circle(img, (W // 2 - 160 + i * 80, H - 40), 20, (70, 70, 72), -1)
    for r, k in zip(rects, kinds):
        {"video": lambda: video_tile(img, r, rng),
         "half": lambda: half_face_tile(img, r, rng),
         "off": lambda: camera_off_tile(img, r),
         "plain": lambda: camera_off_tile(img, r, avatar=False)}[k]()
    return img


def close(a, b, tol=12):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_finds_every_tile():
    rng = np.random.default_rng(1)
    rects = grid()
    img = page(rects, ["video", "off", "half", "video", "plain", "off"], rng)
    found = find_tiles(img)
    assert len(found) == len(rects)
    for r in rects:
        assert any(close(r, f) for f in found), r


def test_finds_tiles_under_a_browser_toolbar():
    rng = np.random.default_rng(2)
    rects = grid(top=140)
    img = page(rects, ["video"] * 6, rng)
    img[:80] = (245, 245, 245)                 # a light browser toolbar and tab strip
    found = find_tiles(img)
    assert len(found) == 6


def test_camera_off_picture():
    rng = np.random.default_rng(3)
    r = (0, 0, 480, 270)
    img = np.zeros((270, 480, 3), np.uint8)
    camera_off_tile(img, r)
    assert looks_like_camera_off(img[20:220, 30:450])
    video_tile(img, r, rng)
    assert not looks_like_camera_off(img[20:220, 30:450])
    half_face_tile(img, r, rng)
    assert not looks_like_camera_off(img[20:220, 30:450], img[222:255, 144:336])


def test_kinds_settle_and_half_face_is_video():
    rng = np.random.default_rng(4)
    rects = grid()
    kinds = ["video", "off", "half", "video", "plain", "off"]
    tw = TileWatch()
    for _ in range(4):
        tw.update(page(rects, kinds, rng), [])
    got = {}
    for t in tw.tiles:
        idx = next(i for i, r in enumerate(rects) if close(r, t.rect))
        got[kinds[idx]] = got.get(kinds[idx], set()) | {t.kind}
    assert got["video"] == {"video"}
    assert got["half"] == {"video"}
    assert got["off"] == {"avatar"} and got["plain"] == {"avatar"}


def face_in(r, frac_y=0.45):
    x0, y0, x1, y1 = r
    cx, cy = (x0 + x1) // 2, y0 + int((y1 - y0) * frac_y)
    return (cx - 40, cy - 50, cx + 40, cy + 50)


def test_student_keeps_their_tile():
    rng = np.random.default_rng(5)
    rects = grid()
    kian = 7
    kinds = ["video", "off", "video", "video", "video", "video"]
    tw = TileWatch()
    for _ in range(3):
        tw.update(page(rects, kinds, rng), [(kian, 0.7, face_in(rects[2]))])
    # Kian's face slides half out of view: not recognised any more.
    kinds[2] = "half"
    for _ in range(3):
        ev = tw.update(page(rects, kinds, rng), [])
    assert ev == {kian: "video"}
    # He turns his camera off.
    kinds[2] = "off"
    for _ in range(4):
        ev = tw.update(page(rects, kinds, rng), [])
    assert ev == {kian: "avatar"}


def test_reflow_forgets_the_tile():
    rng = np.random.default_rng(6)
    tw = TileWatch()
    rects = grid()
    for _ in range(3):
        tw.update(page(rects, ["video"] * 6, rng), [(7, 0.7, face_in(rects[0]))])
    # Someone joins: 4 columns now, every tile moved.
    moved = grid(n_cols=4)
    for _ in range(4):
        ev = tw.update(page(moved, ["video"] * 8, rng), [])
    assert ev == {} and 7 not in tw.memory


def test_someone_else_recognised_in_the_tile_takes_it():
    rng = np.random.default_rng(7)
    tw = TileWatch()
    rects = grid()
    for _ in range(3):
        tw.update(page(rects, ["video"] * 6, rng), [(7, 0.7, face_in(rects[0]))])
    ev = tw.update(page(rects, ["video"] * 6, rng), [(8, 0.7, face_in(rects[0]))])
    assert 7 not in ev and 7 not in tw.memory


def test_near_edge():
    rng = np.random.default_rng(8)
    tw = TileWatch()
    rects = grid()
    tw.update(page(rects, ["video"] * 6, rng), [])
    x0, y0, x1, y1 = rects[0]
    cx = (x0 + x1) // 2
    assert tw.near_edge((cx - 40, y1 - 50, cx + 40, y1 - 2))      # cut off at the bottom
    assert not tw.near_edge(face_in(rects[0]))


def test_speaking_ring_on_a_camera_off_tile_stays_camera_off():
    rng = np.random.default_rng(9)
    rects = grid()
    tw = TileWatch()
    for i in range(6):
        img = page(rects, ["off"] * 6, rng)
        x0, y0, x1, y1 = rects[0]
        h = y1 - y0
        # Meet pulses a ring around the avatar of someone speaking.
        cv2.circle(img, ((x0 + x1) // 2, (y0 + y1) // 2), h // 6 + 6 + 3 * (i % 3), (220, 180, 140), 3)
        tw.update(img, [])
    first = next(t for t in tw.tiles if close(t.rect, rects[0]))
    assert first.kind == "avatar"

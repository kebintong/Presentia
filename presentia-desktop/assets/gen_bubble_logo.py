"""Render the Presentia marks into the PNGs the native bubble embeds.

The floating bubble is a Win32 layered window, so it cannot draw SVG. This
script renders both bubble looks at 4x their 96-DPI size, each with a soft
glow; bubble_logo_win.go scales them down to the monitor's DPI at runtime.

  light       presentia_mark_light.png (cyan mark), as-is
  dark        presentia_mark_dark.png (white mark), as-is
  iridescent  the four shapes from frontend/src/components/PresentiaLogo.tsx
              (keep the two in step) filled with the spectrum gradient

    python gen_bubble_logo.py        (needs Pillow and numpy)
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

# ── Iridescent geometry (viewBox 0 0 428 644) — mirrors PresentiaLogo.tsx ──
VIEW_W, VIEW_H = 428, 644
PATHS = [
    "M16,0 H184 Q200,0 200,16 V55 A115,115 0 0 1 85,170 H16 Q0,170 0,154 V16 Q0,0 16,0 Z",
    "M244,0 H412 Q428,0 428,16 V154 Q428,170 412,170 H343 A115,115 0 0 1 228,55 V16 Q228,0 244,0 Z",
    "M16,200 H85 A115,115 0 0 1 200,315 V618 Q200,644 178,644 Q170,644 162,636 L4,466 Q0,461 0,452 V216 Q0,200 16,200 Z",
    "M343,200 H412 Q428,200 428,216 V262 Q428,272 421,279 L270,428 Q260,438 248,436 Q228,432 228,410 V315 A115,115 0 0 1 343,200 Z",
]
# Spectrum stops, top-right -> bottom-left (same as the Figma fill).
STOPS = [(0.00, "97DEF0"), (0.25, "EFEEC6"), (0.50, "C888F9"), (0.75, "CBB9F6"), (1.00, "E5D5ED")]
GRAD_FROM, GRAD_TO = (428.0, 0.0), (100.0, 644.0)

# ── Output layout (96-DPI px; rendered at SCALE x) ────────────────────────
SCALE = 4
ART_H = 40  # mark height in the bubble
PAD = 8     # room around the mark for the glow
SS = 4      # supersampling for anti-aliased edges


def _tokens(d):
    out, num = [], ""
    for ch in d:
        if ch.isalpha():
            if num:
                out.append(float(num)); num = ""
            out.append(ch)
        elif ch in " ,":
            if num:
                out.append(float(num)); num = ""
        else:
            num += ch
    if num:
        out.append(float(num))
    return out


def _arc(p0, rx, large, sweep, p1, steps=32):
    """SVG circular arc (rx == ry, no rotation) as a list of points."""
    (x1, y1), (x2, y2) = p0, p1
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    r = max(rx, math.hypot(dx, dy))
    sq = max(0.0, (r * r - dx * dx - dy * dy) / (dx * dx + dy * dy))
    coef = math.sqrt(sq) * (-1 if large == sweep else 1)
    cx = coef * dy + (x1 + x2) / 2
    cy = -coef * dx + (y1 + y2) / 2
    a0 = math.atan2(y1 - cy, x1 - cx)
    a1 = math.atan2(y2 - cy, x2 - cx)
    da = a1 - a0
    if sweep and da < 0:
        da += 2 * math.pi
    if not sweep and da > 0:
        da -= 2 * math.pi
    return [(cx + r * math.cos(a0 + da * i / steps), cy + r * math.sin(a0 + da * i / steps))
            for i in range(1, steps + 1)]


def flatten(d):
    t, i, pts, cur = _tokens(d), 0, [], (0.0, 0.0)
    while i < len(t):
        c = t[i]; i += 1
        if c == "M" or c == "L":
            cur = (t[i], t[i + 1]); i += 2; pts.append(cur)
        elif c == "H":
            cur = (t[i], cur[1]); i += 1; pts.append(cur)
        elif c == "V":
            cur = (cur[0], t[i]); i += 1; pts.append(cur)
        elif c == "Q":
            (qx, qy), end = (t[i], t[i + 1]), (t[i + 2], t[i + 3]); i += 4
            for k in range(1, 17):
                s = k / 16
                pts.append(((1 - s) ** 2 * cur[0] + 2 * (1 - s) * s * qx + s * s * end[0],
                            (1 - s) ** 2 * cur[1] + 2 * (1 - s) * s * qy + s * s * end[1]))
            cur = end
        elif c == "A":
            rx, _ry, _rot, large, sweep, x, y = t[i:i + 7]; i += 7
            pts.extend(_arc(cur, rx, int(large), int(sweep), (x, y)))
            cur = (x, y)
    return pts


def hex_rgb(h):
    return np.array([int(h[k:k + 2], 16) for k in (0, 2, 4)], dtype=np.float32) / 255


def iridescent_art():
    """(rgb, alpha) of the spectrum mark, ART_H tall, at SCALE x."""
    k = ART_H * SCALE / VIEW_H
    W, H = round(VIEW_W * k), round(VIEW_H * k)
    big = Image.new("L", (W * SS, H * SS), 0)
    dr = ImageDraw.Draw(big)
    for d in PATHS:
        dr.polygon([(x * k * SS, y * k * SS) for x, y in flatten(d)], fill=255)
    mask = np.asarray(big.resize((W, H), Image.LANCZOS), dtype=np.float32) / 255

    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    vx, vy = xs / k - GRAD_FROM[0], ys / k - GRAD_FROM[1]
    dx, dy = GRAD_TO[0] - GRAD_FROM[0], GRAD_TO[1] - GRAD_FROM[1]
    tt = np.clip((vx * dx + vy * dy) / (dx * dx + dy * dy), 0, 1)
    rgb = np.zeros((H, W, 3), np.float32)
    for (p0, c0), (p1, c1) in zip(STOPS, STOPS[1:]):
        sel = (tt >= p0) & (tt <= p1)
        f = ((tt - p0) / (p1 - p0))[..., None]
        rgb[sel] = (hex_rgb(c0) * (1 - f) + hex_rgb(c1) * f)[sel]
    return rgb, mask


def mark_art(name):
    """(rgb, alpha) of a flat-colour app mark, ART_H tall, at SCALE x."""
    src = Image.open(os.path.join(HERE, name)).convert("RGBA")
    h = ART_H * SCALE
    w = round(src.width * h / src.height)
    a = np.asarray(src.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255
    return a[..., :3], a[..., 3]


def blur(a, r):
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255


def blur_rgb(c, a, r):
    # Blur premultiplied colour so the glow keeps each region's hue.
    pm = c * a[..., None]
    ch = [blur(pm[..., n], r) for n in range(3)]
    al = blur(a, r)
    return np.clip(np.stack(ch, -1) / np.maximum(al[..., None], 1e-4), 0, 1), al


def over(c_top, a_top, c_bot, a_bot):
    a = a_top + a_bot * (1 - a_top)
    c = (c_top * a_top[..., None] + c_bot * (a_bot * (1 - a_top))[..., None]) / np.maximum(a[..., None], 1e-4)
    return c, a


def render(art, glow_strength, glow_radius):
    rgb0, mask0 = art
    ah, aw = mask0.shape
    p = PAD * SCALE
    H, W = ah + 2 * p, aw + 2 * p
    rgb = np.zeros((H, W, 3), np.float32); rgb[p:p + ah, p:p + aw] = rgb0
    mask = np.zeros((H, W), np.float32); mask[p:p + ah, p:p + aw] = mask0

    # Back to front: faint dark shadow (an edge on light desktops), a glow in
    # the mark's own colours (the look on dark desktops), then the mark.
    sh_a = blur(np.roll(mask, SCALE, axis=0), 1.8 * SCALE) * 0.30
    sh_c = np.broadcast_to(hex_rgb("0B1220"), (H, W, 3))
    gl_c, gl_a = blur_rgb(rgb * 0.55 + 0.45, mask, glow_radius * SCALE)
    gl_a = np.clip(gl_a * glow_strength, 0, 1)
    c, a = over(gl_c, gl_a, sh_c, sh_a)
    c, a = over(rgb, mask, c, a)
    out = np.dstack([np.clip(c, 0, 1), np.clip(a, 0, 1)])
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGBA")


if __name__ == "__main__":
    for name, art in (
        ("bubble_light", mark_art("presentia_mark_light.png")),
        ("bubble_dark", mark_art("presentia_mark_dark.png")),
        ("bubble_iridescent", iridescent_art()),
    ):
        render(art, 0.7, 2.4).save(os.path.join(HERE, name + ".png"), optimize=True)
        # Dial-open state: a brighter, wider halo.
        render(art, 1.4, 3.4).save(os.path.join(HERE, name + "_active.png"), optimize=True)
        print("wrote", name)

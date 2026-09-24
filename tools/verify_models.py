"""Check the new model code against insightface, and time the detectors.

Run from the project root with the dev venv (insightface still installed):

    python tools\\verify_models.py path\\to\\photos

`photos` should hold a few ordinary pictures with faces (JPG/PNG). For each
picture it prints the similarity between insightface's embedding and ours for
the same face — it must be at least 0.99 everywhere (1.00 = identical), or
already-enrolled students might stop matching. It then times each detector
on the CPU, standing in for a low-end machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import face_models as fm  # noqa: E402
from app.core import perf  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    photos = [p for p in Path(sys.argv[1]).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    if not photos:
        print("No JPG/PNG files found.")
        return 2

    cpu = perf._plan("cpu")
    make = perf.session_maker(cpu, "half")
    ours_det = fm.SCRFD(fm.model_path("scrfd_10g"), make, det_size=640)
    ours_rec = fm.ArcFace(fm.model_path("arcface"), make)

    try:
        from insightface.app import FaceAnalysis
    except ImportError:
        print("insightface is not installed in this venv — only the timing runs.")
        FaceAnalysis = None

    worst = 1.0
    if FaceAnalysis is not None:
        ref = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"],
                           providers=["CPUExecutionProvider"])
        ref.prepare(ctx_id=0, det_size=(640, 640))
        print(f"\n{'photo':32s} {'faces':>5s} {'box diff':>9s} {'similarity':>10s}")
        for p in photos:
            img = cv2.imread(str(p))
            if img is None:
                continue
            theirs = ref.get(img)
            mine = ours_det.detect(img)
            if not theirs or not mine:
                print(f"{p.name[:32]:32s} {len(theirs):>2d}/{len(mine):<2d}  (no face found by one side)")
                continue
            t = max(theirs, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            m = min(mine, key=lambda d: np.abs(np.asarray(d[0]) - t.bbox).sum())
            box_diff = float(np.abs(np.asarray(m[0]) - t.bbox).max())
            sim = float(np.dot(ours_rec.embed(img, m[2]), t.normed_embedding))
            worst = min(worst, sim)
            print(f"{p.name[:32]:32s} {len(theirs):>2d}/{len(mine):<2d} {box_diff:9.1f} {sim:10.4f}")
        verdict = "PASS" if worst >= 0.99 else "FAIL — do not release; tell Claude the numbers"
        print(f"\nLowest similarity: {worst:.4f}  → {verdict}")

    img = cv2.imread(str(photos[0]))
    img = cv2.resize(img, (640, 480)) if img is not None else np.zeros((480, 640, 3), np.uint8)
    print("\nDetector speed on the CPU (median ms per frame, 640x480):")
    dets = {"SCRFD 10G (High)": fm.SCRFD(fm.model_path("scrfd_10g"), make, 640),
            "SCRFD 2.5G (Balanced)": fm.SCRFD(fm.model_path("scrfd_2.5g"), make, 640),
            "SCRFD 2.5G @480 (Balanced webcam)": fm.SCRFD(fm.model_path("scrfd_2.5g"), make, 480)}
    try:
        dets["YuNet @320 (Low webcam)"] = fm.YuNet(fm.model_path("yunet"), 320)
    except Exception as exc:  # noqa: BLE001
        print(f"  YuNet unavailable: {exc}")
    for name, d in dets.items():
        print(f"  {name:36s} {fm.time_it(lambda d=d: d.detect(img), runs=15, warmup=3):7.1f}")
    crop = np.zeros((112, 112, 3), np.uint8)
    print(f"  {'ArcFace embedding (per new face)':36s} "
          f"{fm.time_it(lambda: ours_rec.embed_aligned(crop), runs=15, warmup=3):7.1f}")
    print("\nHardware check result used by Auto:")
    b = perf.benchmark(force=True)
    for r in b["results"]:
        print(f"  {r['device']:36s} {r.get('ms', r.get('error'))}")
    print(f"  → fastest: {b['best']}, recommended profile: {b['recommended']}")
    return 0 if worst >= 0.99 else 1


if __name__ == "__main__":
    raise SystemExit(main())

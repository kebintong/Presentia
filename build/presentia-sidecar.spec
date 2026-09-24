# presentia-sidecar.spec
#
# Builds the FastAPI sidecar (app/sidecar.py) into a standalone folder
# (onedir, NOT onefile) so onnxruntime/mediapipe/insightface don't have to
# re-extract several hundred MB to a temp dir on every launch.
#
# Run from the PROJECT ROOT (the folder containing app/, models/, etc.):
#   pyinstaller build/presentia-sidecar.spec --noconfirm --clean
#
# Output: dist/presentia-sidecar/presentia-sidecar.exe  (+ its _internal libs)

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None
PROJECT_ROOT = Path.cwd()

# ---------------------------------------------------------------------------
# Libraries that hide C extensions / data files PyInstaller's static
# analysis can't find on its own. collect_all() grabs binaries, data files
# AND hidden imports in one shot for each.
# ---------------------------------------------------------------------------
datas = []
binaries = []
hiddenimports = []

# "onnxruntime" is also the import name of onnxruntime-directml (the Windows
# build, which adds GPU support through DirectML) — collect_all picks up its
# DirectML.dll either way.
# insightface is no longer used at run time (app/core/face_models.py runs its
# model files directly), so it and its large dependencies stay out of the build.
for pkg in ("onnxruntime", "mediapipe", "cv2", "uvicorn"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# FastAPI/uvicorn/pydantic dodge PyInstaller's import scanner via
# entry-point-style dynamic imports. Spell these out explicitly.
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "pydantic",
    "multipart",
    "websockets",
    "mss",
]

# Presentia's own modules. sidecar.py imports several of these *inside*
# functions (to keep startup light), and a module that is only reached that
# way is easy to lose in a frozen build — the failure then shows up as a
# ModuleNotFoundError in the installed app but never in dev. Spelling them
# out costs nothing and removes that whole class of surprise.
hiddenimports += [
    "app",
    "app.sidecar",
    "app.data.db",
    "app.core.camera",
    "app.core.enrollment",
    "app.core.face_engine",
    "app.core.face_models",
    "app.core.antispoof",
    "app.core.perf",
    "app.core.liveness",
    "app.core.monitor",
    "app.core.roster_monitor",
    "app.core.screen",
    "app.core.tile_tracker",
    "app.core.v4l2_reader",
]

# Bundle the MediaPipe landmark model that ships in the repo already.
datas += [(str(PROJECT_ROOT / "models" / "face_landmarker.task"), "models")]

# Optional: any .onnx placed in models/ (YuNet, the anti-spoofing models, even
# the face models) ships inside the build and is used instead of downloading.
datas += [(str(f), "models") for f in (PROJECT_ROOT / "models").glob("*.onnx")]

# NOTE on the face models: they are NOT bundled by default. On first launch
# app/core/face_models.py downloads only the files the chosen profile needs
# (ArcFace ~175 MB + a detector, 0.3–17 MB), reusing any already present in
# ~/.insightface/models from older versions. To ship them in the installer
# instead, put the .onnx files in models/ before building (see above).

a = Analysis(
    [str(PROJECT_ROOT / "app" / "sidecar_entry.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Not needed by the sidecar. insightface and helpers only it used are
    # excluded in case they are still installed in the build venv.
    # (matplotlib stays: mediapipe imports it.)
    excludes=["PySide6", "PyQt5", "PyQt6", "tkinter", "insightface", "skimage",
              "albumentations", "sklearn"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="presentia-sidecar",
    debug=False,
    strip=False,
    upx=False,          # UPX + onnxruntime/opencv DLLs is a common source of
                         # false-positive AV flags; leave it off.
    console=False,      # no console window; errors go to the log files.
    # Same Presentia mark as the desktop app (replaces the default icon).
    icon=str(PROJECT_ROOT / "presentia-desktop" / "build" / "windows" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="presentia-sidecar",
)

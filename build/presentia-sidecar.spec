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

for pkg in ("onnxruntime", "insightface", "mediapipe", "cv2", "uvicorn"):
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
    "app.core.liveness",
    "app.core.monitor",
    "app.core.roster_monitor",
    "app.core.screen",
    "app.core.tile_tracker",
    "app.core.v4l2_reader",
]

# insightface's get_object() checks sys.frozen and when True looks for data at:
#   sys._MEIPASS/objects/<name>.pkl   (NOT insightface/data/objects/)
# So we must place the file at the "objects" destination directly.
import site as _site
_if_objects = None
for _sp in _site.getsitepackages():
    _candidate = Path(_sp) / "insightface" / "data" / "objects"
    if _candidate.exists():
        _if_objects = _candidate
        break
if _if_objects is None:
    # fallback: search relative to this spec file's venv
    _if_objects = PROJECT_ROOT / ".venv" / "Lib" / "site-packages" / "insightface" / "data" / "objects"
datas += [(str(_if_objects / "meanshape_68.pkl"), "objects")]

# Bundle the MediaPipe landmark model that ships in the repo already.
datas += [(str(PROJECT_ROOT / "models" / "face_landmarker.task"), "models")]

# NOTE on InsightFace's buffalo_l pack (~300 MB): it is intentionally NOT
# bundled here. FaceEngine.instance() (app/core/face_engine.py) downloads it
# on first call via insightface's own model_zoo into ~/.insightface — this
# is exactly what powers the "Downloading AI model weights" step of the
# first-launch wizard. If you'd rather ship it in the installer instead of
# downloading it, see the note in BUILD.md ("Bundling buffalo_l instead of
# downloading it").

a = Analysis(
    [str(PROJECT_ROOT / "app" / "sidecar_entry.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6", "PyQt5", "PyQt6", "tkinter"],  # not needed by the sidecar
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
    icon=None,
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

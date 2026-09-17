# Building the Presentia installer

This is the *shipping* pipeline — separate from the dev workflow in the
main `README.md`. Do this on your build machine only; end users never see
any of these steps.

Target: **Windows**, x64. (Say the word if you also need macOS/Linux —
the PyInstaller spec is portable, but the Inno Setup step is Windows-only
and would need a `.pkg`/AppImage equivalent instead.)

## 0. One-time build-machine setup

Use **Python 3.11 or 3.12** for the build environment — `insightface`,
`onnxruntime`, and `mediapipe` all ship pre-built wheels for those, so pip
never tries to compile from source. (This is the build machine's Python;
end users never install Python at all.)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

Also install:
- [Go](https://go.dev/dl/) + `wails` CLI (`go install github.com/wailsapp/wails/v2/cmd/wails@latest`)
- [Node.js](https://nodejs.org/) (for the frontend build)
- [Inno Setup](https://jrsoftware.org/isinfo.php) (free, Windows)

## 1. Build the frontend + Wails shell

```bash
cd presentia-desktop
cd frontend && npm install && cd ..
wails build
cd ..
```

Produces `presentia-desktop\build\bin\Presentia.exe`.

## 2. Freeze the Python sidecar

From the **project root** (not `presentia-desktop/`):

```bash
pyinstaller build\presentia-sidecar.spec --noconfirm --clean
```

Produces `dist\presentia-sidecar\` — a folder containing
`presentia-sidecar.exe` plus its dependency DLLs (onnxruntime, opencv,
mediapipe, etc.). This is **onedir**, not onefile, on purpose: onefile
would re-extract ~500 MB to `%TEMP%` on every single launch, adding
10–30s of startup lag every time. Onedir extracts once, at install time.

**Sanity check before moving on** — run the frozen exe standalone, with no
venv active, to confirm PyInstaller caught everything:

```bash
dist\presentia-sidecar\presentia-sidecar.exe --host 127.0.0.1 --port 7788
```

Then hit `http://127.0.0.1:7788/api/engine/status` in a browser. You
should see `{"ready":false,...}` immediately, flipping to
`{"ready":true,"error":null}` after the InsightFace model finishes
downloading/loading. If it crashes instead, the traceback will almost
always point at a missing hidden import — add it to `hiddenimports` in
the `.spec` file and rebuild.

## 3. Compile the installer

```bash
ISCC.exe build\installer.iss
```

Produces `build\output\PresentiaSetup.exe` — this is the single file you
distribute. Double-clicking it installs everything; no Python, no Node,
no Go on the end user's machine.

## What changed in the code, and why

- **`app/sidecar_entry.py`** (new) — PyInstaller needs a real script to
  freeze, and `uvicorn app.sidecar:app` (an import-string) doesn't survive
  freezing. This imports the FastAPI `app` object directly and calls
  `uvicorn.run()` on it.
- **`app/data/db.py`** (edited) — `attendance.db` used to live next to
  `app/`, which resolves to `C:\Program Files\Presentia\` once installed —
  **not writable** by a standard user, so every DB write would silently
  fail or crash. It now writes to `%APPDATA%\Presentia\attendance.db`
  instead when running frozen (dev mode is unchanged).
- **`presentia-desktop/app.go`** (edited) — `startup()` now looks for
  `sidecar\presentia-sidecar.exe` next to the installed `Presentia.exe`
  first; if it's not there (i.e. you're running `wails dev`), it falls
  back to the original `python -m uvicorn` path automatically. Same
  binary, works in both dev and installed contexts.

## Two things worth deciding before you ship

**1. InsightFace's `buffalo_l` pack (~300 MB) — download on first launch,
or bundle it in the installer?**

Right now it's left as download-on-first-launch (that's what
`FaceEngine.instance()` already does, unchanged) — this keeps
`PresentiaSetup.exe` around 150–250 MB instead of 450–550 MB, and slots
naturally into a "Downloading AI models…" step in a first-launch wizard.
The tradeoff: it needs an internet connection the first time the app
runs, and if a school firewall blocks the download host, first launch
gets stuck.

**Bundling it instead of downloading it:** pre-download it once yourself
by calling `FaceEngine.instance()` on your build machine, then copy
`%USERPROFILE%\.insightface\models\buffalo_l\` into
`build\insightface_models\buffalo_l\` and add to `installer.iss`:
```ini
Source: "insightface_models\*"; DestDir: "{userappdata}\Presentia\.insightface\models"; Flags: recursesubdirs
```
and set `INSIGHTFACE_HOME` to `%APPDATA%\Presentia` in `app.go`'s
`cmd.Env` (same place `PRESENTIA_DATA_DIR` is set now) so InsightFace
finds it there instead of trying to download. Zero-failure-rate at the
cost of a bigger installer.

**2. Do you want a first-launch wizard UI, or is the raw
`/api/engine/status` progress enough?**

Nothing here builds the actual wizard screen (system check → model
download progress → camera test) discussed earlier — this pipeline just
makes `engine_status`'s `ready`/`error` fields available to poll. Happy
to build that React step-through next; it'd live in
`presentia-desktop/frontend/src/pages/` alongside your other pages and
gate on a flag like `localStorage.getItem('setupComplete')`.

## Every-release checklist

```bash
pyinstaller build\presentia-sidecar.spec --noconfirm --clean
cd presentia-desktop && wails build && cd ..
ISCC.exe build\installer.iss
```
Bump `MyAppVersion` in `installer.iss` each release so Windows shows the
correct version in Add/Remove Programs.

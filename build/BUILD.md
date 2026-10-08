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

## Checking window monitoring by hand

Window capture talks to Windows itself, so CI can only check that the
library is bundled (the smoke test's "Window capture" line). Before a
release that touches `app/core/window_capture.py` or `_WindowFollower`,
try it on a real PC with a meeting (or any video) in Chrome/Edge/Brave and
in the Zoom or Teams app:

| Do this while monitoring | Expected |
| --- | --- |
| Cover half of the meeting with File Explorer | Keeps monitoring, boxes stay on the right faces |
| Cover the meeting completely (Zoom/Teams app) | Keeps monitoring |
| Cover a browser meeting completely | After ~3 s: "Paused: window fully covered"; resumes when a corner shows |
| Minimise the meeting (or Win+D) | "Paused: window minimised"; **Keep monitoring** puts it back behind other windows |
| Close the meeting window | "was closed — monitoring paused" |

Settings → Diagnostics → *Window capture* shows the library version; with
diagnostic mode on, the activity log records which capture method was used
and every pause/resume.

## Releasing (the Release button)

Releases are built on GitHub, not on your PC (`.github/workflows/release.yml`):

1. Make sure the latest commit on `main` has a **green check** (Actions tab), and that **`RELEASE_NOTES.md`**
   has the changes under **Next release** (short bullet points in plain words — this is what teachers read in
   Settings → Updates). Add to it as you make changes, not on release day.
2. GitHub → **Actions** → **Release** → **Run workflow** → branch `main`, type the version (e.g. `1.5.4`),
   leave **Publish right away** ticked → **Run workflow**.
3. Wait 15–25 minutes. The workflow:
   - refuses a version that already exists or is not higher than the last release;
   - runs all CI checks;
   - on Windows: sets the version (`set-version.ps1`), builds the desktop app (`wails build`), the engine
     (`pyinstaller`), **starts the engine once** (`build/smoke-test-engine.ps1`) and builds the installer
     (Inno Setup);
   - builds the release text with `build/release_notes.py`: "What's new" from `RELEASE_NOTES.md`, then the
     commits since the last release and the download note;
   - commits "Release 1.5.4" (with the notes moved under a "## 1.5.4 — date" heading and an empty "Next
     release" left for the next changes), tags `v1.5.4`, and creates the release with `PresentiaSetup.exe`
     and its SHA-256 checksum.
4. With notes written and **Publish right away** ticked, the release is **published**: installed copies
   offer it within a few hours. If "Next release" was empty, or you unticked the box, it stays a **draft**:
   open **Releases** → the draft, write "What's new", press **Publish release**.

If a step fails, nothing is published: open the failed run, look at the red step's log, fix, and run the
Release again (a tag is only created after everything built). If the version commit could not be pushed to
`main` (for example because `main` is protected), the run shows a warning; the release itself is fine, because
it is built from the tag.

Every push also runs the **Engine build** workflow when engine code, `requirements.txt` or the spec changes, so
a module missing from the frozen build shows up before release day.

## Building by hand (fallback)

Still works the same as before, e.g. to test a build locally:

```powershell
.venv\Scripts\python -m pip install pytest httpx openpyxl
.venv\Scripts\python -m pytest tests -q
powershell -ExecutionPolicy Bypass -File build\set-version.ps1 1.5.4
cd presentia-desktop; wails build; cd ..
pyinstaller build\presentia-sidecar.spec --noconfirm --clean
powershell -ExecutionPolicy Bypass -File build\smoke-test-engine.ps1
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
```

Don't publish a hand-built installer under a version the Release button will also use.

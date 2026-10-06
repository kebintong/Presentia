# Presentia — Face-Verified Attendance for Online Classes

[![CI](https://github.com/kebintong/Presentia/actions/workflows/ci.yml/badge.svg)](https://github.com/kebintong/Presentia/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/kebintong/Presentia?label=download)](https://github.com/kebintong/Presentia/releases/latest)
[![Wails](https://img.shields.io/badge/Desktop-Wails_v2-df0000?logo=go&logoColor=white)](https://wails.io/)
[![React](https://img.shields.io/badge/Frontend-React_19_+_TypeScript-61dafb?logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/Engine-Python_3.12_+_FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![ONNX Runtime](https://img.shields.io/badge/Face_models-ONNX_Runtime-blueviolet)](https://onnxruntime.ai/)
[![MediaPipe](https://img.shields.io/badge/Liveness-MediaPipe-blue)](https://developers.google.com/mediapipe)
[![Cloudflare](https://img.shields.io/badge/Registration_site-Cloudflare_Workers-f38020?logo=cloudflare&logoColor=white)](web/README.md)

**Presentia** is a desktop app for Windows (and Linux, built from source) that takes attendance in online classes.
It verifies each student's identity with **face recognition and a live face check**, watches the Google Meet,
Zoom or Teams window during class to see who is present, and keeps every record **on the teacher's computer**.

Students can register in person with the teacher's webcam, or from their own phone or laptop on the
**Presentia registration website** using the class join code.

---

## Contents

- [Download and install](#download-and-install)
- [How it works](#how-it-works)
- [Features](#features)
- [Privacy and data](#privacy-and-data)
- [Architecture](#architecture)
- [Development](#development)
- [Building the installer and releasing](#building-the-installer-and-releasing)
- [Registration website](#registration-website)
- [Project structure](#project-structure)
- [Engine API](#engine-api)
- [License](#license)

---

## Download and install

1. Download **`PresentiaSetup.exe`** from the [latest release](https://github.com/kebintong/Presentia/releases/latest).
2. Run it. Windows 10 or 11 (64-bit) is required. No Python, Node or Go is needed on the teacher's computer.
3. On first launch Presentia checks the hardware and downloads its face models (about 180 MB, once).
   The status pill in the top bar shows the progress.

Presentia **keeps itself up to date**: it checks for new releases when it starts and every few hours while it runs, shows a notice when one is out (never while a meeting is being monitored), and installs it when you choose *Restart and install* in Settings → Updates.

---

## How it works

1. **Create a class** on the start screen (like Google Classroom). Each class gets a **join code**.
2. **Register students**, in person (webcam or photos) or online (students use the join code on the website,
   and you accept each one).
3. **Start a session** on the Meeting Monitor and select the meeting window. Presentia recognises the students in
   the video tiles and marks them present, late or missing as the class goes on.
4. **Review attendance** on the Students and Reports pages, correct statuses if needed, and export to Excel.

A student is stored **once** and can be in **many classes**. Registering for another class reuses their saved
face data, and nobody can be in the same class twice.

---

## Features

### Classes and students
- **Class picker** start screen: new class, past classes, join codes, waiting-registration counts.
- **Students page** per class: present / late / absent totals, attendance rate, join date, last seen,
  low-attendance filter (below 80%), per-student history, edit or remove from the class.
- **Add from other classes**: put existing students into a class without registering them again.
- **Excel export** (`.xlsx`): summary, attendance per session and class info sheets.

### Registration
- **In person**: guided 5-pose webcam capture (straight, left, right, up, blink), or import 1–5 photos.
- **Online**: students register on the website with the join code, a live face check (blink and head turns in a
  random order) and three photos; the app downloads the registration, builds the face template **on your
  computer**, and you **accept or reject** it. Turned on per class on the Student Registration page.
- **One face, one student**: a face that is already registered can't become a second student. A returning
  student is recognised (by number or by face) and simply added to the new class.
- **Student number protection**: the website won't let a different device overwrite a registration that's waiting,
  and the app won't accept someone else's face under a known student number.

### Meeting Monitor (Google Meet, Zoom, Teams)
- **Select a window or a screen area**; on Windows a selected window is captured with Windows Graphics Capture,
  so it keeps being monitored while it is moved or covered by other windows. A minimised window can't be read:
  **Keep monitoring** puts it back behind your other windows. Chrome, Edge and Brave stop drawing a window that
  is *completely* covered, so for a browser meeting leave any part of it showing; Presentia pauses and says so
  rather than reading an old picture.
- **Multi-face recognition** in all visible tiles, with each student tracked as *waiting → present → missing*.
- **Unknown faces** are listed with one-click enrolment.
- **Random action check** on a student's tile (blink, turn, look up) to catch photos or recordings; works for
  students on phones too.
- **Still-tile warnings** when a tile stops moving (a frozen video or a photo).
- **Alerts** for students who leave the frame, turn their camera off, or don't match their own face.
- **Bubble mode (Windows)**: a floating button with a dial, a tray icon, a stats chip and a resizable
  **Live View**, all hidden from screen sharing and from the capture itself.

### Single-student session
- Webcam check-in with an **active liveness challenge** (MediaPipe Face Landmarker), optional photo / screen
  replay detection (MiniFASNet), then continuous presence monitoring with periodic re-identification.

### Reports
- Session history with every enrolled student (including those never seen), manual **Present / Late / Absent**
  overrides, event log, CSV export.

### Settings
- **Appearance**: light / dark theme, the experimental **Iridescent** design, and **animations** (with a switch to
  turn them off; theme changes animate too).
- **Performance**: device (CPU or GPU via DirectML) and profile (Auto / Low / Balanced / High), with a benchmark.
- **Accessibility**: randomised challenges, photo / screen replay detection.
- **Updates**: what's new in the latest release, then download and install it (checked automatically).
- **Data**: what's stored on this computer, and **Delete all data**.
- **Diagnostics**: a step-by-step connection check for the registration website, recent problems, and an optional
  **diagnostic mode** that keeps an activity log and offers to send, copy or save a report when something fails
  (turns itself off after 7 days).

---

## Privacy and data

- **Attendance and face data stay on the teacher's computer**, in a local SQLite database
  (`%APPDATA%\Presentia\attendance.db`). A face is stored as a 512-number template, not as a
  photo.
- **The registration website only holds registrations until the app collects them.** Photos are deleted as soon as
  the app downloads them, and anything not collected is deleted after 14 days. No face template is ever computed
  or stored on the website.
- **Diagnostic reports are only sent when the user presses Send**, after seeing the full report (the Windows user
  name is replaced with `<you>`). They are kept for 30 days.
- **Settings → Data → Delete all data** removes every class, student and their face data, session and attendance
  record, and rewrites the database file so deleted face data doesn't remain on disk.
- The website's privacy notice refers to the Philippine **Data Privacy Act of 2012**. Have your school's data
  protection officer review it before use.

---

## Architecture

```
┌─────────────────────────── Teacher's computer ───────────────────────────┐
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Frontend: React 19 + TypeScript + Vite (Tailwind CSS v4)          │  │
│  │  Classes · Student Registration · Students · Meeting Monitor ·     │  │
│  │  Session · Reports · Settings                                      │  │
│  └──────────────────────────────┬─────────────────────────────────────┘  │
│                                 │ Wails bindings (dialogs, window)       │
│  ┌──────────────────────────────▼─────────────────────────────────────┐  │
│  │  Desktop shell: Wails v2 (Go)                                      │  │
│  │  frameless window · engine supervisor · auto-update · save dialogs │  │
│  │  bubble, tray, Live View, window capture helpers (Windows)         │  │
│  └──────────────────────────────┬─────────────────────────────────────┘  │
│                                 │ REST + WebSocket, 127.0.0.1:7788       │
│  ┌──────────────────────────────▼─────────────────────────────────────┐  │
│  │  Engine: Python 3.12 + FastAPI (frozen with PyInstaller)           │  │
│  │  SCRFD / YuNet detection + ArcFace embeddings (ONNX Runtime)       │  │
│  │  MediaPipe liveness · MiniFASNet replay check · MSS / WGC capture  │  │
│  │  SQLite attendance.db                                              │  │
│  └──────────────────────────────┬─────────────────────────────────────┘  │
└─────────────────────────────────┼────────────────────────────────────────┘
                                  │ HTTPS (only for online registration
                                  │ and diagnostic reports)
┌─────────────────────────────────▼────────────────────────────────────────┐
│  Registration website: Cloudflare Workers + D1 (free plan) — web/        │
│  join-code page · in-browser face check · waiting registrations · reports│
└──────────────────────────────────────────────────────────────────────────┘
```

Face models: SCRFD (10g / 2.5g) or YuNet for detection, and ArcFace `w600k_r50` for recognition, run directly
with ONNX Runtime (the `insightface` package is not used at run time). Two faces count as the same person at a
cosine similarity of **0.45** or more.

---

## Development

### Prerequisites

| Tool | Version |
|---|---|
| Python | 3.12 |
| Go | 1.25 (see `presentia-desktop/go.mod`) |
| Wails CLI | v2 (`go install github.com/wailsapp/wails/v2/cmd/wails@latest`) |
| Node.js | 20.19+ or 22 |
| Windows | WebView2 runtime (built into Windows 11) |
| Linux | `gcc`, `pkg-config`, `libgtk-3-dev`, `libwebkit2gtk-4.1-dev` |

### Setup

```bash
git clone https://github.com/kebintong/Presentia.git
cd Presentia

python -m venv .venv
# Windows PowerShell (if scripts are blocked: Set-ExecutionPolicy -Scope Process Bypass)
.venv\Scripts\Activate.ps1
# Linux
source .venv/bin/activate

pip install -r requirements.txt

cd presentia-desktop/frontend
npm install
cd ../..
```

### Run

```bash
cd presentia-desktop
wails dev
```

Wails starts the Vite dev server with hot reload, and the Go shell starts the Python engine on
`http://127.0.0.1:7788` from `.venv`. Like the installed app, it keeps its data in `%APPDATA%\Presentia`
(`~/.config/Presentia` on Linux). Running the engine on its own (below) uses `attendance.db` in the project folder.

To work on the screens in a normal browser instead:

```bash
python -m uvicorn app.sidecar:app --host 127.0.0.1 --port 7788 --reload
cd presentia-desktop/frontend && npm run dev      # http://localhost:5173
```

### Tests and checks

```bash
pip install pytest httpx openpyxl
python -m pytest tests -q
```

The tests cover the database and its upgrades from older versions, classes and attendance totals, the Excel export,
duplicate-face and student-number rules, diagnostic mode and reports, Delete all data, the HTTPS certificate
fallback (needs `openssl`, so it is skipped on Windows), and the PyInstaller build list.

Every push runs [GitHub Actions](.github/workflows/ci.yml): the Python tests, the frontend type check and build,
a Windows build of the desktop shell, and a dry-run deploy of the website.

---

## Building the installer and releasing

Releases are built by GitHub Actions, not on a developer's PC:

1. **Actions → Release → Run workflow**, type the new version (e.g. `1.5.4`).
2. The workflow checks the version, runs every CI check, builds the desktop app, the frozen engine and the
   installer on Windows, starts the engine once to make sure it runs, tags the version and creates a
   **draft release** with `PresentiaSetup.exe` and its SHA-256 checksum.
3. Open the draft, write the release notes, and press **Publish**. Installed copies check `releases/latest` and
   offer the update; drafts and pre-releases are never offered, and the repository must stay public for the
   update check to see releases.

Details and the by-hand fallback are in [`build/BUILD.md`](build/BUILD.md).

### Automation at a glance

| Workflow | When | What |
|---|---|---|
| [CI](.github/workflows/ci.yml) | every push and pull request | Python tests, frontend build, Windows build of the Go shell, website dry run; on `main`, deploys the website when `web/` changed and everything passed |
| [Engine build](.github/workflows/engine-build.yml) | changes to the engine, its dependencies or build list | builds the frozen engine on Windows and starts it once |
| [Release](.github/workflows/release.yml) | by hand (version box) | checks, builds the installer, tags, drafts the release |
| [Dependabot](.github/dependabot.yml) | monthly | grouped update pull requests for GitHub Actions, npm and Go packages |

---

## Registration website

The website in [`web/`](web/) runs on Cloudflare's free plan (Workers + D1, no payment card needed). Students enter
the class join code, their student number and name, agree to the privacy notice, and pass a live face check in the
browser. GitHub Actions deploys it after a push to `main` that changes `web/`, once every check has passed.

The default address is set in `app/data/cloud.py` (`DEFAULT_URL`, and `REPORTS_URL` for diagnostic reports).
Setup, local testing, reading diagnostic reports and the optional GitHub-issue integration are described in
[`web/README.md`](web/README.md).

---

## Project structure

```
Presentia/
├── app/                                # Python engine ("sidecar")
│   ├── sidecar.py                      # FastAPI REST + WebSocket server (port 7788)
│   ├── sidecar_entry.py                # Entry point for the frozen build
│   ├── core/
│   │   ├── face_models.py              # SCRFD / YuNet / ArcFace on ONNX Runtime, model downloads
│   │   ├── face_engine.py              # Detection + recognition, matching (threshold 0.45)
│   │   ├── liveness.py                 # MediaPipe landmarks, blink / turn / look-up challenges
│   │   ├── antispoof.py                # MiniFASNet photo / screen replay check
│   │   ├── tile_challenge.py           # Random action check on a meeting tile
│   │   ├── stillness.py                # Still-tile (frozen video / photo) warnings
│   │   ├── roster_monitor.py           # Waiting → present → missing per student
│   │   ├── monitor.py                  # Single-student presence monitoring
│   │   ├── screen.py, tile_tracker.py  # Screen / window capture, tile tracking
│   │   ├── enrollment.py, camera.py    # Guided 5-pose enrolment, webcam capture
│   │   ├── perf.py                     # Devices, profiles, benchmark
│   │   ├── window_capture.py           # Windows Graphics Capture, covered-window checks
│   │   └── diag.py                     # Recent problems, diagnostic mode, activity log
│   ├── data/
│   │   ├── db.py                       # SQLite schema + versioned migrations
│   │   ├── cloud.py                    # Registration website + reports (HTTPS with certificate fallback)
│   │   ├── export.py, xlsx.py          # Excel export (no extra dependencies)
│   └── ui/, main.py                    # Legacy PySide6 interface (kept for reference)
├── presentia-desktop/                  # Wails desktop app
│   ├── main.go, app.go                 # Window, engine supervisor, save dialogs
│   ├── update.go                       # Auto-update from GitHub releases (AppVersion lives here)
│   ├── bubble_*.go, tray_win.go,
│   │   live_win.go, pip_win.go         # Bubble mode, tray, stats chip, Live View (Windows)
│   └── frontend/src/
│       ├── App.tsx, main.tsx           # Layout, theme, animations, diagnostics hook
│       ├── diagnostics.ts              # Problem capture + report building
│       ├── themeTransition.ts          # Animated theme switch
│       ├── pages/                      # ClassPicker, Register, Students, Meet, Session, Reports
│       └── components/                 # TopBar, Sidebar, SettingsPanel and its tabs,
│                                       # OnlineRegistration, ProblemPrompt, ReportDialog, …
├── web/                                # Registration website (Cloudflare Worker + D1)
├── build/                              # PyInstaller spec, Inno Setup script, set-version.ps1,
│                                       # smoke-test-engine.ps1, BUILD.md
├── tests/                              # pytest suite
├── tools/verify_models.py              # Compare embeddings with the reference implementation
├── models/face_landmarker.task         # MediaPipe model (bundled)
├── .github/                            # CI, Engine build and Release workflows, Dependabot
└── requirements.txt
```

---

## Engine API

The engine listens on `http://127.0.0.1:7788` and is only reachable from the same computer. Main groups
(see `app/sidecar.py` for every endpoint):

| Area | Endpoints |
|---|---|
| Engine | `GET /api/engine/status`, `GET/PUT /api/perf`, `POST /api/perf/benchmark`, `GET/PUT /api/checks` |
| Classes | `GET/POST /api/classes`, `GET/PATCH/DELETE /api/classes/{id}`, `POST /api/classes/{id}/open`, `POST /api/classes/{id}/join-code`, `GET /api/classes/{id}/available-students` |
| Students | `GET/POST /api/students`, `PATCH/DELETE /api/students/{id}`, `PUT/DELETE /api/classes/{id}/students/{sid}`, `GET /api/classes/{id}/students/summary`, `GET /api/classes/{id}/students/{sid}/history`, `GET /api/classes/{id}/export.xlsx` |
| Enrolment | `POST /api/enroll/photos/preview`, `POST /api/enroll/photos`, `WS /ws/camera` |
| Online registration | `GET/PUT /api/cloud`, `PUT /api/classes/{id}/online`, `POST /api/classes/{id}/sync`, `GET /api/classes/{id}/pending`, `POST /api/pending/{id}/approve`, `DELETE /api/pending/{id}` |
| Sessions and attendance | `GET/POST /api/sessions`, `PUT /api/sessions/{id}/end`, `GET /api/sessions/{id}/report`, `GET /api/sessions/{id}/events`, `PATCH /api/sessions/{id}/attendance/status`, `GET /api/sessions/{id}/export-csv` |
| Meeting monitor | `WS /ws/screen`, `GET /api/screen/screenshot`, `GET /api/monitor/live`, `GET /api/monitor/frame` |
| Diagnostics | `GET /api/diagnostics`, `GET/PUT /api/diagnostics/mode`, `POST /api/diagnostics/event`, `GET/DELETE /api/diagnostics/log`, `POST /api/diagnostics/send` |
| Data | `GET /api/data/summary`, `POST /api/data/delete-all` |

---

## License

This project is licensed under the MIT License.

The pretrained InsightFace face models that Presentia downloads (SCRFD, ArcFace `w600k_r50`) are released by
InsightFace for **non-commercial research use only**. Commercial use needs a licence from InsightFace or different
models. YuNet (OpenCV Zoo) and MiniFASNet are under permissive licences.

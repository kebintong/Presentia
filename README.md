# Presentia — Face-Verified Attendance

[![CI](https://github.com/kebintong/Presentia/actions/workflows/ci.yml/badge.svg)](https://github.com/kebintong/Presentia/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/kebintong/Presentia?label=download)](https://github.com/kebintong/Presentia/releases/latest)
[![Wails](https://img.shields.io/badge/Desktop-Wails_v2-df0000?logo=go&logoColor=white)](https://wails.io/)
[![React](https://img.shields.io/badge/Frontend-React_19_+_TypeScript-61dafb?logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/Engine-Python_3.12_+_FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![ONNX Runtime](https://img.shields.io/badge/Face_models-ONNX_Runtime-blueviolet)](https://onnxruntime.ai/)
[![MediaPipe](https://img.shields.io/badge/Liveness-MediaPipe-blue)](https://developers.google.com/mediapipe)
[![Cloudflare](https://img.shields.io/badge/Registration_site-Cloudflare_Workers-f38020?logo=cloudflare&logoColor=white)](web/README.md)
[![Supabase](https://img.shields.io/badge/Phone_app_sync-Supabase-3ecf8e?logo=supabase&logoColor=white)](supabase/README.md)

**Presentia** is a Windows desktop app that takes attendance in online classes. It recognises each student by face,
with a live check against photos and recordings, while it watches the Google Meet, Zoom or Teams window. Every record
stays **on the teacher's computer**, unless the teacher signs in to the optional phone app.

Students register in person with the teacher's webcam, or from their own phone on the **registration website**
with the class join code.

## Download and install

1. Download **`PresentiaSetup.exe`** from the [latest release](https://github.com/kebintong/Presentia/releases/latest)
   and run it (Windows 10 or 11, 64-bit). On an Intel Mac (macOS 13 or later), download
   **`Presentia-mac-intel.dmg`** instead; see [`mac/README.md`](mac/README.md).
2. On first launch Presentia downloads its face models (about 180 MB, once).

Presentia updates itself: Settings → Updates shows what's new and installs it.

## How it works

1. **Create a class.** Each class gets a join code.
2. **Register students** in person (webcam or photos), or turn on online registration and accept each student who
   registers on the website. A student is stored once and can be in many classes; one face can't become two students.
3. **Monitor the meeting.** Pick the meeting window or browser tab; Presentia recognises the students in the video
   tiles and marks them present, late or missing. It flags unknown faces, frozen tiles and cameras turned off, and can
   ask a student to blink or turn their head to prove they're live.
4. **Review attendance** on the Students and Reports pages, correct it if needed, and export to Excel.

On first start Presentia shows a short welcome, the Terms of Use and Privacy Notice to accept, and offers the phone app.

**Phone app (coming).** With a free account (email and password; created on first start or on the Phone App page, the phone icon in the sidebar),
the teacher's classes sync with the Presentia phone app for in-person attendance, random recitation and accepting
registrations from either device. No account is needed to use Presentia on the computer.

## Privacy and data

- **Attendance and face data stay on the teacher's computer** (`%APPDATA%\Presentia\attendance.db`). A face is stored
  as a 512-number template, not a photo.
- **Phone app, only if the teacher signs in:** classes, students with their face templates, attendance and waiting
  registrations are copied to the teacher's own account on Supabase, where row-level security keeps everyone else
  out. Deleting on the desktop deletes there too. See [`supabase/README.md`](supabase/README.md).
- **The registration website keeps a registration only until the app collects it**, and deletes anything left after
  14 days. No face template is made or stored there.
- **Diagnostic reports are sent only when the teacher presses Send**, after seeing the whole report.
- **Settings → Data → Delete all data** removes every class, student, face and attendance record.
- Teachers accept the app's Terms of Use and Privacy Notice
  (`presentia-desktop/frontend/src/components/TermsText.tsx`) on first start; the version they accepted, and when,
  is kept on the computer.
- The website's privacy notice refers to the Philippine **Data Privacy Act of 2012**; have your school's data
  protection officer review it before use.

## Architecture

```
Teacher's computer
┌──────────────────────────────────────────────────────────────────────┐
│ Desktop app: Wails v2 (Go) + React 19, TypeScript, Vite, Tailwind    │
│   window, tray and bubble, engine supervisor, auto-update            │
│        │ REST + WebSocket on 127.0.0.1:7788 (this computer only)     │
│ Engine: Python 3.12, FastAPI (frozen with PyInstaller)               │
│   face detection and recognition: SCRFD / YuNet + ArcFace on         │
│   ONNX Runtime (DirectML), MediaPipe liveness, screen capture, SQLite│
└───────────────┬───────────────────────────────────┬──────────────────┘
                │ HTTPS                             │ HTTPS, only when signed in
┌───────────────▼─────────────────────┐ ┌───────────▼──────────────────────┐
│ Registration website (web/)         │ │ Phone app cloud (supabase/)      │
│ Cloudflare Workers + D1             │ │ Supabase: Postgres + row-level   │
│ join code, in-browser face check    │ │ security, Realtime               │
└─────────────────────────────────────┘ └──────────────────────────────────┘
```

| Folder | What |
|---|---|
| `app/` | The engine (`sidecar.py` is the API; `core/` face, liveness and capture; `data/` database, website and phone sync) |
| `presentia-desktop/` | The desktop app (Go) and its screens (`frontend/`) |
| `web/` | The registration website ([setup](web/README.md)) |
| `supabase/` | The phone app's tables and access rules ([setup](supabase/README.md)) |
| `build/` | Installer build: PyInstaller spec, Inno Setup script, version script ([BUILD.md](build/BUILD.md)) |
| `tests/` | Python tests |
| `models/`, `tools/` | The bundled MediaPipe model; a tool to check the face models |

## Development

Needs Python 3.12, Go 1.25, the [Wails CLI](https://wails.io/) v2 and Node.js 22. Run the tests with:

```bash
python -m pytest tests -q
```

Every push and pull request runs CI. Releases are built by GitHub Actions: **Actions → Release → Run workflow** with
the new version creates a draft release with `PresentiaSetup.exe`; publish it and installed copies offer the update.
Details, and building by hand, are in [`build/BUILD.md`](build/BUILD.md).

| Workflow | When | What |
|---|---|---|
| [CI](.github/workflows/ci.yml) | every push and pull request | Python tests, frontend build, Windows build of the desktop app, website dry run, Supabase rules on Postgres; on `main`, deploys the website when `web/` changed |
| [Engine build](.github/workflows/engine-build.yml) | engine changes | builds the frozen engine on Windows and starts it once |
| [Release](.github/workflows/release.yml) | by hand | builds the installer, tags the version, drafts the release, then adds the Mac disk image |
| [Mac build](.github/workflows/mac-build.yml) | Mac build changes, and every release | builds the Intel Mac app and its `.dmg`, starts its engine once |
| [Dependabot](.github/dependabot.yml) | monthly | update pull requests for GitHub Actions, npm and Go |

## License

MIT.

The InsightFace face models Presentia downloads (SCRFD, ArcFace `w600k_r50`) are for **non-commercial research use
only**; commercial use needs a licence from InsightFace or other models. YuNet (OpenCV Zoo) and MiniFASNet are under
permissive licences.

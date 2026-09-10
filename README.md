# Presentia — AI-Powered Virtual Classroom Attendance System

[![Wails](https://img.shields.io/badge/Desktop-Wails_v2-df0000?logo=go&logoColor=white)](https://wails.io/)
[![React](https://img.shields.io/badge/Frontend-React_19_+_TypeScript-61dafb?logo=react&logoColor=black)](https://react.dev/)
[![Tailwind CSS](https://img.shields.io/badge/Styles-Tailwind_CSS_v4-38bdf8?logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![FastAPI](https://img.shields.io/badge/Sidecar-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![InsightFace](https://img.shields.io/badge/Face_Engine-InsightFace_Buffalo__L-orange)](https://github.com/deepinsight/insightface)
[![MediaPipe](https://img.shields.io/badge/Liveness-MediaPipe-blue)](https://developers.google.com/mediapipe)

**Presentia** is a modern desktop application (Windows / Linux) that verifies student identity through **facial recognition + active liveness detection**, continuously monitors presence during virtual class sessions (Google Meet, Zoom, Microsoft Teams), and manages attendance records locally.

Migrated from legacy PySide6 to a modern **Wails v2 + React 19 + TypeScript** architecture, Presentia pairs a sleek, frameless desktop experience with an asynchronous **Python FastAPI sidecar** powering computer vision and biometric verification.

---

## Key Features

- **Student Registration & Enrollment**
  - **Interactive 5-Pose Webcam Guide**: Guided capture capturing five key poses (Straight, Left, Right, Up, Blink) with real-time pose feedback.
  - **Batch Photo Import**: Import 1–5 profile pictures using native OS file pickers (`OpenImageFilesDialog`).
  - **Live In-Call Enrollment**: Enroll unrecognized faces directly from active Google Meet sessions on the fly.
  - **Student Directory**: Search, review, and manage enrolled students and their 512-d facial embeddings.

- **Meet Monitor (Virtual Classroom Screen Capture)**
  - **Target Area Selection**: Drag-and-select region picker over Google Meet, Zoom, or Teams video tiles.
  - **Multi-Face Presence Tracking**: Continuously detects and matches all visible faces against the registered student roster.
  - **Real-Time State Machine**: Tracks students across `Waiting` ➔ `Present` ➔ `Missing` states with configurable missing thresholds (e.g. 5 seconds).
  - **Unknown Face Tagging**: Crops and lists unidentified faces with one-click enrollment.
  - **Live Alert Feed**: Instant notifications when students disappear, leave their desks, or turn off cameras.
  - **On-Demand Re-Verification**: Trigger interactive verification prompts for specific students during class.

- **Webcam Session (Single-Student Mode)**
  - **Interactive Liveness Challenge**: Anti-spoofing challenge requiring active eye blinks and head turns (yaw angles) powered by MediaPipe Face Landmarker.
  - **Biometric Matching**: Verifies student identity against enrolled InsightFace embeddings using cosine similarity (threshold: `0.45`).
  - **Continuous Presence Monitoring**: Continuous periodic re-identification via webcam with audit events logged.

- **Attendance Reports & Analytics**
  - **Session History**: Detailed audit trail per session including start/end timestamps, duration, and participant counts.
  - **Attendance Record Table**: Logs student number, name, first sighting (`time_in`), departure (`time_out`), status, and alert frequency.
  - **Manual Status Overrides**: Update statuses (`Present`, `Late`, `Absent`) directly in the UI.
  - **Native CSV Export**: Export formatted attendance logs via native OS save dialogs (`SaveCSVDialog`).

- **Modern Desktop Experience**
  - **Frameless UI**: Native custom titlebar (`TopBar`) with draggable region, window controls (minimize, maximize/restore, close), and engine status indicator.
  - **Theme Switcher**: Fluid dark/light theme support with persistent preferences.
  - **Automated Sidecar Management**: Go backend automatically launches, health-checks, and terminates the Python AI sidecar.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────┐
│                   Presentia Desktop                    │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │      Frontend: React 19 + TypeScript + Vite      │  │
│  │   Tailwind CSS v4 • Custom Glassmorphism UI      │  │
│  │   Pages: Register, Meet Monitor, Session, Reports│  │
│  └────────────────────────┬─────────────────────────┘  │
│                           │ Wails Runtime Bindings     │
│                           │ (Native Dialogs & Window)  │
│  ┌────────────────────────▼─────────────────────────┐  │
│  │             Wails v2 Desktop Shell (Go)          │  │
│  │  - Frameless Window Management                   │  │
│  │  - Sidecar Process Supervisor (Startup/Shutdown) │  │
│  │  - Native File & Save Dialogs                    │  │
│  └────────────────────────┬─────────────────────────┘  │
└───────────────────────────┼────────────────────────────┘
                            │ REST / WebSocket (Port 7788)
┌───────────────────────────▼────────────────────────────┐
│         Python FastAPI AI Sidecar (Localhost)          │
│                                                        │
│  ┌──────────────────────┐    ┌──────────────────────┐  │
│  │ InsightFace Buffalo_L│    │ MediaPipe Landmarker │  │
│  │  Detection & Embed   │    │  Liveness Challenges │  │
│  └──────────────────────┘    └──────────────────────┘  │
│  ┌──────────────────────┐    ┌──────────────────────┐  │
│  │  MSS Screen Capture  │    │  OpenCV Video Stream │  │
│  └──────────────────────┘    └──────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │       SQLite Database (attendance.db)            │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

---

## Prerequisites

Before running or building Presentia, ensure your environment has:

1. **Go**: Version `1.21` or higher (installed and on `PATH`).
2. **Wails CLI v2**:
   ```bash
   go install github.com/wailsapp/wails/v2/cmd/wails@latest
   ```
3. **Node.js & npm**: Node.js `18.x` or higher.
4. **Python**: Python `3.10` – `3.12`.
5. **C++ Build Tools**:
   - **Windows**: Microsoft Visual Studio C++ Build Tools (required by Go / Wails on Windows).
   - **Linux**: `gcc`, `pkg-config`, `libgtk-3-dev`, `libwebkit2gtk-4.0-dev` (or `4.1`).
6. **Webcam & Screen Capture Permissions**: A working webcam and display capture permissions.

---

## Installation & Setup

### 1. Clone Repository & Setup Python Virtual Environment

```bash
git clone https://github.com/your-repo/Presentia.git
cd Presentia

# Create and activate Python virtual environment
python -m venv .venv

# Windows (Command Prompt / PowerShell)
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# Install Python sidecar dependencies
pip install -r requirements.txt
```

> **Note**: On the first launch, InsightFace automatically downloads the `buffalo_l` model pack (~300 MB) into `~/.insightface`, and MediaPipe's `face_landmarker.task` is loaded from `models/`. Ensure an active internet connection on first startup.

### 2. Install Frontend Dependencies

```bash
cd presentia-desktop/frontend
npm install
cd ../..
```

---

## Running in Development Mode

You can run Presentia using either the unified **Wails dev workflow** or by running the components independently.

### Option A: Unified Wails Dev (Recommended)

From the `presentia-desktop` directory, launch the Wails development server:

```bash
cd presentia-desktop
wails dev
```

What happens automatically:
1. Wails boots and invokes `app.go`.
2. `app.go` spawns the Python sidecar on `http://127.0.0.1:7788` using `.venv` or your active Python environment.
3. Wails launches Vite in live watch mode with Hot Module Replacement (HMR).
4. The native desktop application window opens with live reload enabled.

### Option B: Standalone Sidecar + Frontend (Browser Testing)

If you prefer testing the UI in a standard web browser:

1. **Start the Python FastAPI sidecar:**
   ```bash
   python -m uvicorn app.sidecar:app --host 127.0.0.1 --port 7788 --reload
   ```
2. **Start the Vite frontend dev server:**
   ```bash
   cd presentia-desktop/frontend
   npm run dev
   ```
3. Open `http://localhost:5173` in your browser.

---

## Building for Production

To produce an optimized, standalone desktop application bundle:

```bash
cd presentia-desktop
wails build
```

The compiled binary and package assets will be generated in:
```
presentia-desktop/build/bin/
```

- **Windows**: `presentia-desktop/build/bin/Presentia.exe`
- **Linux**: `presentia-desktop/build/bin/Presentia`

---

## Project Structure

```
Presentia/
├── app/                              # Python AI & Computer Vision Sidecar
│   ├── core/
│   │   ├── camera.py                 # Threaded OpenCV webcam capture
│   │   ├── face_engine.py            # InsightFace Buffalo_L detection & 512-d embeddings
│   │   ├── liveness.py               # MediaPipe face landmarks & blink/yaw challenge logic
│   │   └── monitor.py                # Presence tracking state machine
│   ├── data/
│   │   └── db.py                     # SQLite schema, student profiles, attendance logs
│   ├── sidecar.py                    # FastAPI REST & WebSocket server (port 7788)
│   └── main.py                       # Legacy PySide6 launcher
├── models/                           # MediaPipe face landmarker binary tasks
├── presentia-desktop/                # Wails Desktop Shell
│   ├── app.go                        # Go runtime: sidecar supervisor & native OS bindings
│   ├── main.go                       # Wails entrypoint & window configuration
│   ├── wails.json                    # Wails application config
│   ├── go.mod / go.sum               # Go modules
│   ├── build/                        # App icons, Windows manifests, build artifacts
│   └── frontend/                     # React + TypeScript Web Application
│       ├── package.json              # Dependencies: React 19, TypeScript, Tailwind v4
│       ├── vite.config.ts            # Vite build configuration
│       ├── src/
│       │   ├── App.tsx               # Main layout, theme management, sidecar health poll
│       │   ├── style.css             # Liquid glass aesthetic & Tailwind styles
│       │   ├── components/
│       │   │   ├── TopBar.tsx        # Frameless window controls, status, theme toggle
│       │   │   ├── Sidebar.tsx       # Navigation sidebar with status badges
│       │   │   ├── VideoCanvas.tsx   # Video renderer with dynamic face bounding boxes
│       │   │   ├── RosterList.tsx    # Live student attendance roster
│       │   │   ├── AlertList.tsx     # Real-time alert notifications
│       │   │   └── StatusBanner.tsx  # Verification status message banners
│       │   └── pages/
│       │       ├── RegisterPage.tsx  # 5-pose webcam capture & photo import
│       │       ├── MeetPage.tsx      # Google Meet screen monitor & instant enrollment
│       │       ├── SessionPage.tsx   # Single-student liveness & webcam monitoring
│       │       └── ReportsPage.tsx   # Session review, status overrides & CSV export
├── requirements.txt                  # Python dependencies
└── README.md                         # Project documentation
```

---

## API & Sidecar Endpoints

The Python sidecar serves REST and WebSocket endpoints on `http://127.0.0.1:7788`:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/engine/status` | Model loading readiness check |
| `GET` | `/api/students` | List all registered students |
| `POST` | `/api/students` | Create new student profile with face embeddings |
| `DELETE` | `/api/students/{id}` | Remove a student profile |
| `GET` | `/api/sessions` | Fetch past attendance sessions |
| `POST` | `/api/sessions/start` | Start a new monitoring session |
| `POST` | `/api/sessions/stop` | End active session and commit departures |
| `GET` | `/api/sessions/{id}/report` | Fetch full session attendance summary |
| `PATCH` | `/api/attendance/{id}` | Update manual attendance status (`Present`/`Late`/`Absent`) |
| `GET` | `/api/sessions/{id}/export` | Export session attendance as CSV |
| `WS` | `/ws/enroll` | Live 5-pose guided webcam enrollment stream |
| `WS` | `/ws/meet` | Google Meet screen capture and multi-face recognition stream |
| `WS` | `/ws/session` | Single-student webcam liveness verification and presence stream |

---

## License

This project is licensed under the MIT License — see the LICENSE file for details.

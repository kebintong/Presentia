# AI-Powered Virtual Classroom Attendance System (Alpha)

Desktop application (Windows / Linux) that verifies student identity with **facial
recognition + liveness detection**, continuously monitors presence during a class
session, and records attendance locally.

This alpha is a **single-machine feasibility demo**: registration, verification,
presence monitoring, and reporting all run in one app against a local SQLite
database. The networked instructor dashboard is a later phase.

## Features

- **Student registration** — enter student ID + name, capture ~5 face samples,
  embeddings stored in SQLite.
- **Meet Monitor (instructor overlay)** — screen-captures the Google Meet (or
  Zoom/Teams) window on the instructor's PC, recognizes every registered
  student visible in the video tiles, records time-in on first sighting, and
  alerts in real time when a student disappears (camera off / left frame /
  left the call). Includes a draggable always-on-top overlay with live
  per-student status.
- **Webcam session (single-student mode)** — full identity verification with a
  liveness challenge (blink, then turn head) followed by face recognition,
  then continuous webcam presence monitoring with periodic re-identification.
- **Attendance reports** — per-session table with time-in / time-out, alert
  counts, manual status override (Present / Late / Absent), event log, CSV
  export.

## Requirements

- Python 3.10 – 3.12
- A webcam
- ~1 GB free disk (InsightFace model weights, ~300 MB, download on first run)

## Setup

```bash
python3 -m venv .venv

# Linux/macOS
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

## Run

```bash
python -m app.main
```

The first launch downloads two model files automatically (internet required
once): the InsightFace `buffalo_l` pack (~300 MB, cached in `~/.insightface`)
and MediaPipe's `face_landmarker.task` (~4 MB, cached in `models/`). The SQLite
database is created at `attendance.db` in the project folder.

## Usage flow

1. **Register** tab — three ways to enroll a student:
   - *Webcam capture* (student is with you): press *Start Face Capture* and
     follow the prompts until 5 samples are taken, then *Save*.
   - *Photo import* (student is remote): press *Import Photos…* and pick 1-5
     clear photos the student sent you, then *Save*.
   - *From the Meet call itself*: see the Meet Monitor tab below — unknown
     faces can be enrolled live during a session.
2. **Meet Monitor** tab (main mode) — open Google Meet with the student tiles
   visible, press *Select Meet Area on Screen* and drag a box around the Meet
   window, then press *Start Monitoring*. The roster shows each student as
   *not seen yet* / *PRESENT* / *MISSING* and every change is logged with an
   alert. *Show Overlay* opens a small always-on-top status panel you can drag
   next to the Meet window. Faces that match no registered student appear
   under *Unknown faces* — click one to enroll that student on the spot (they
   are tracked immediately). Press *Stop Monitoring* to record time-outs.
   Tip: use Meet's grid/tile layout and keep tiles reasonably large — faces
   need to be big enough on screen to recognize.
3. **Webcam Session** tab — single-student mode with full liveness
   verification: press *Start Session*, select who is joining, pass the
   liveness challenge (blink twice, turn head), and time-in is recorded after
   the face is verified. Monitoring then runs continuously on the webcam.
4. **Reports** tab — pick a session to review attendance, change the status
   dropdown to override (Present / Late / Absent), and export to CSV.

Note (Linux): screen capture requires an X11 session. On Wayland desktops,
log in with "Ubuntu on Xorg" (or similar). Windows needs no extra setup.

## Packaging (later)

A standalone Windows executable can be produced with PyInstaller on a Windows
machine:

```bash
pip install pyinstaller
pyinstaller --name AIAttendance --windowed app/main.py
```

## Project structure

```
app/
  main.py               # entry point
  ui/
    main_window.py      # navigation shell
    register_page.py    # student registration + face capture
    session_page.py     # live session: verify + monitor
    reports_page.py     # attendance table, manual override, CSV export
  core/
    camera.py           # threaded OpenCV capture
    face_engine.py      # InsightFace detect/embed/match
    liveness.py         # blink + head-turn challenge logic
    monitor.py          # presence state machine
  data/
    db.py               # SQLite schema + queries
```

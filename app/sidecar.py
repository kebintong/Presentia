"""FastAPI sidecar for Presentia — wraps all core/ and data/ modules as
REST + WebSocket endpoints consumed by the Wails desktop frontend.

Start with:
    python -m uvicorn app.sidecar:app --host 127.0.0.1 --port 7788
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import itertools
import threading
import time

import cv2
import numpy as np
from fastapi import (
    FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect,
    Body,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.data import db
from app.core.face_engine import FaceEngine

# ── DB init ──────────────────────────────────────────────────────────────────
db.init_db()

app = FastAPI(title="Presentia Sidecar", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Background model preload ──────────────────────────────────────────────────
_engine_ready = threading.Event()
_engine_error: str | None = None


def _preload_engine() -> None:
    global _engine_error
    try:
        FaceEngine.instance()
        _engine_ready.set()
    except Exception as exc:  # noqa: BLE001
        _engine_error = str(exc)
        _engine_ready.set()


threading.Thread(target=_preload_engine, daemon=True).start()

# ── Pydantic models ───────────────────────────────────────────────────────────


class StudentCreate(BaseModel):
    student_no: str
    name: str
    embedding_b64: str  # base64-encoded float32 bytes


class SessionCreate(BaseModel):
    name: str


class StatusUpdate(BaseModel):
    status: str


class SessionUpdate(BaseModel):
    session_id: int
    student_id: int


class StudentStatusUpdate(BaseModel):
    student_id: int
    status: str


# ── Engine status ─────────────────────────────────────────────────────────────

@app.get("/api/engine/status")
async def engine_status() -> dict:
    """Used by the Go shell to poll readiness; also consumed by the frontend."""
    ready = _engine_ready.is_set() and _engine_error is None
    return {"ready": ready, "error": _engine_error}


# ── Screen screenshot (for in-app region picker) ──────────────────────────────

@app.get("/api/screen/screenshot")
async def screen_screenshot() -> dict:
    """Capture the primary screen and return a compressed JPEG + dimensions.

    The frontend renders this inside a full-screen canvas overlay so the user
    can draw a selection rectangle without opening a separate browser window.
    """
    try:
        import mss  # already used by ws_screen; available in frozen exe
        from PIL import Image as PILImage

        with mss.mss() as sct:
            # monitors[0] is the virtual combined desktop; monitors[1] is the
            # first physical monitor.  We always capture the primary (index 1)
            # so coordinates are in single-monitor space.
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            sct_img = sct.grab(mon)
            img = PILImage.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            # Quality 55 keeps the file small (~200 KB for 1080p) while still
            # being sharp enough to identify window boundaries.
            img.save(buf, format="JPEG", quality=55)
            b64 = base64.b64encode(buf.getvalue()).decode()

        return {
            "jpeg": b64,
            "width":  mon["width"],
            "height": mon["height"],
            "left":   mon["left"],
            "top":    mon["top"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Students ──────────────────────────────────────────────────────────────────

@app.get("/api/students")
async def list_students() -> list[dict]:
    return db.list_students()


@app.post("/api/students", status_code=201)
async def create_student(body: StudentCreate) -> dict:
    try:
        raw = base64.b64decode(body.embedding_b64)
        embedding = np.frombuffer(raw, dtype=np.float32)
        student_id = db.add_student(body.student_no, body.name, embedding)
        return {"id": student_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/students/{student_id}", status_code=204)
async def delete_student(student_id: int) -> None:
    db.delete_student(student_id)


@app.get("/api/students/{student_id}/embedding")
async def student_embedding(student_id: int) -> dict:
    """Base64 face embedding for one student (used by verification flows)."""
    emb = db.get_student_embedding(student_id)
    if emb is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"embedding_b64": base64.b64encode(emb.astype(np.float32).tobytes()).decode()}


async def _mean_embedding_from_uploads(files: list[UploadFile]) -> tuple[np.ndarray, int]:
    """Average the face embedding across up to 5 uploaded photos."""
    if not FaceEngine.is_ready():
        raise HTTPException(status_code=503, detail="AI models still loading")

    engine = FaceEngine.instance()
    embeddings: list[np.ndarray] = []

    for upload in files[:5]:
        data = await upload.read()
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            continue
        face = engine.largest_face(img)
        if face is None or float(face.det_score) < 0.55:
            continue
        embeddings.append(np.asarray(face.normed_embedding, dtype=np.float32))

    if not embeddings:
        raise HTTPException(
            status_code=422,
            detail="No usable face found in the uploaded photos.",
        )

    mean = np.mean(np.stack(embeddings), axis=0)
    mean /= np.linalg.norm(mean)
    return mean.astype(np.float32), len(embeddings)


@app.post("/api/enroll/photos/preview")
async def preview_photo_enrollment(files: list[UploadFile] = File(...)) -> dict:
    """Extract a face embedding from photos WITHOUT creating the student.

    The Register page calls this while the instructor is still filling in the
    form, then saves through /api/students like the webcam path does, so a
    student is never written to the database twice.
    """
    mean, count = await _mean_embedding_from_uploads(files)
    return {
        "embedding_b64": base64.b64encode(mean.tobytes()).decode(),
        "samples": count,
    }


@app.post("/api/enroll/photos")
async def enroll_from_photos(
    student_no: str = Body(...),
    name: str = Body(...),
    files: list[UploadFile] = File(...),
) -> dict:
    """Accept 1-5 image files, extract face embeddings, average and save."""
    mean, count = await _mean_embedding_from_uploads(files)
    try:
        student_id = db.add_student(student_no, name, mean)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"id": student_id, "samples": count}


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions() -> list[dict]:
    return db.list_sessions()


@app.post("/api/sessions", status_code=201)
async def create_session(body: SessionCreate) -> dict:
    session_id = db.create_session(body.name)
    return {"id": session_id}


@app.put("/api/sessions/{session_id}/end", status_code=204)
async def end_session(session_id: int) -> None:
    db.end_session(session_id)


@app.get("/api/sessions/{session_id}/report")
async def session_report(session_id: int) -> list[dict]:
    return db.session_report(session_id)


@app.get("/api/sessions/{session_id}/events")
async def session_events(session_id: int) -> list[dict]:
    return db.session_events(session_id)


# ── Attendance ────────────────────────────────────────────────────────────────

@app.patch("/api/attendance/{attendance_id}/status", status_code=204)
async def set_attendance_status(attendance_id: int, body: StatusUpdate) -> None:
    db.set_status(attendance_id, body.status)


@app.patch("/api/sessions/{session_id}/attendance/status")
async def set_session_student_status(
    session_id: int, body: StudentStatusUpdate
) -> dict:
    """Override a student's status even if they were never detected.

    Absent students have no attendance row, so the row is created on demand.
    """
    if body.status not in ("Present", "Late", "Absent"):
        raise HTTPException(status_code=400, detail="Invalid status")
    attendance_id = db.set_student_status(session_id, body.student_id, body.status)
    db.log_event(
        session_id, body.student_id, "manual_override",
        f"Attendance manually set to {body.status} by the instructor.",
    )
    return {"attendance_id": attendance_id}


@app.post("/api/attendance/time-in", status_code=204)
async def record_time_in(body: SessionUpdate) -> None:
    db.record_time_in(body.session_id, body.student_id)


@app.post("/api/attendance/time-out", status_code=204)
async def record_time_out(body: SessionUpdate) -> None:
    db.record_time_out(body.session_id, body.student_id)


@app.post("/api/sessions/{session_id}/log-event", status_code=204)
async def log_event_endpoint(
    session_id: int,
    student_id: int | None = Body(None),
    event_type: str = Body(...),
    message: str = Body(...),
) -> None:
    db.log_event(session_id, student_id, event_type, message)


# ── CSV Export ────────────────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/export-csv")
async def export_csv(session_id: int) -> StreamingResponse:
    rows = db.session_report(session_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student No.", "Name", "Time In", "Time Out", "Status", "Alerts"])
    for row in rows:
        writer.writerow([
            row["student_no"], row["name"], row["time_in"] or "",
            row["time_out"] or "", row["status"], row["alert_count"],
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=attendance_{session_id}.csv"},
    )


# ── WebSocket helpers ─────────────────────────────────────────────────────────

def _frame_to_jpeg_b64(frame: np.ndarray) -> str:
    """Encode a BGR frame to JPEG and return as base64 string."""
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return base64.b64encode(buf).decode()


async def _send_json(ws: WebSocket, data: dict) -> bool:
    """Send one JSON message. Returns False once the socket is gone."""
    try:
        await ws.send_json(data)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _run_until_first_done(*coros) -> None:
    """Run websocket pumps until any one finishes, then cancel the rest.

    Without this a disconnected client left the send pump waiting forever on
    its queue, so the capture thread — and the webcam — was never released.
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ── WebSocket: /ws/camera  (Register page + Session page webcam) ─────────────

@app.websocket("/ws/camera")
async def ws_camera(websocket: WebSocket) -> None:  # noqa: C901 – intentionally monolithic
    """Stream webcam frames with optional guided-enrollment or
    liveness+recognition analysis.

    Client sends JSON control messages:
      {"action": "start_enroll", "directional": true}
      {"action": "start_liveness", "directional": true}
      {"action": "start_recognize", "student_id": 1}      # or "embedding_b64"
      {"action": "start_monitor",   "student_id": 1}      # or "embedding_b64"
      {"action": "stop"}

    Server sends JSON frames:
      {"type": "frame",  "jpeg": "<base64>"}
      {"type": "enroll", ...guided state...}
      {"type": "liveness", ...liveness state...}
      {"type": "recognize", "found": bool, "score": float}
      {"type": "monitor",  "face_found": bool, "brightness": float, "reid": float|null}
      {"type": "presence_alert", "event_type": str, "message": str}
      {"type": "error",   "message": str}
    """
    await websocket.accept()

    from app.core.camera import frame_brightness
    from app.core.face_engine import FaceEngine, MATCH_THRESHOLD
    from app.core.liveness import FaceMeshTracker, LivenessChecker
    from app.core.enrollment import GuidedEnrollment
    from app.core.monitor import PresenceMonitor

    result_queue: asyncio.Queue = asyncio.Queue(maxsize=8)

    # How often the monitored student's identity is re-checked, and how long
    # to wait before repeating a mismatch warning.
    REID_EVERY = 4.0
    RECOGNIZE_EVERY = 0.6
    MISMATCH_COOLDOWN = 30.0

    # Analysis state
    mode = "idle"
    tracker: FaceMeshTracker | None = None
    liveness: LivenessChecker | None = None
    guided: GuidedEnrollment | None = None
    presence: PresenceMonitor | None = None
    target_emb: np.ndarray | None = None
    last_reid = 0.0
    last_mismatch = 0.0
    analysis_lock = threading.Lock()

    def _embed_largest(frame: np.ndarray) -> np.ndarray | None:
        face = FaceEngine.instance().largest_face(frame)
        if face is None or float(face.det_score) < 0.55:
            return None
        return np.asarray(face.normed_embedding, dtype=np.float32)

    def _push(payload: dict) -> None:
        """Queue a message from the camera thread onto the event loop."""
        loop.call_soon_threadsafe(
            lambda p=payload: result_queue.put_nowait(p)
            if not result_queue.full() else None
        )

    def _presence_event(event_type: str, message: str) -> None:
        _push({"type": "presence_alert", "event_type": event_type, "message": message})

    def _analyse(frame: np.ndarray) -> dict | None:
        nonlocal mode, tracker, liveness, guided, target_emb
        nonlocal presence, last_reid, last_mismatch
        with analysis_lock:
            m = mode
        if m == "idle":
            return None
        if m == "enroll":
            g = guided
            if g is None:
                return None
            result = g.process(frame)
            result["type"] = "enroll"
            result["jpeg"] = _frame_to_jpeg_b64(frame)
            # When all poses are done, compute the mean embedding and send it
            # back so the frontend can call /api/students directly.
            if result.get("done") and g.samples:
                mean = np.mean(np.stack(g.samples), axis=0).astype(np.float32)
                mean /= np.linalg.norm(mean)
                result["embedding_b64"] = base64.b64encode(mean.tobytes()).decode()
            return result
        if m == "liveness":
            lv = liveness
            if lv is None:
                return None
            result = lv.process(frame)
            result["type"] = "liveness"
            result["jpeg"] = _frame_to_jpeg_b64(frame)
            return result
        if m == "recognize":
            # Embedding a face costs ~1s on CPU. Throttling it keeps the
            # preview moving instead of freezing on every frame, and stops
            # the attempt counter from burning through in a couple of seconds.
            now = time.monotonic()
            if now - last_reid < RECOGNIZE_EVERY:
                return {"type": "frame", "jpeg": _frame_to_jpeg_b64(frame)}
            last_reid = now
            emb = FaceEngine.instance().embed_largest(frame)
            if emb is None:
                return {"type": "recognize", "found": False, "score": 0.0,
                        "jpeg": _frame_to_jpeg_b64(frame)}
            score = FaceEngine.similarity(emb, target_emb)
            return {"type": "recognize", "found": True, "score": float(score),
                    "jpeg": _frame_to_jpeg_b64(frame)}
        if m == "monitor":
            t = tracker
            if t is None:
                return None
            lms = t.landmarks(frame)
            face_found = lms is not None
            brightness = float(frame_brightness(frame))

            # Presence state machine: fires out_of_frame / camera_off /
            # back_in_frame alerts through _presence_event.
            if presence is not None:
                presence.update(face_found, brightness)

            # Periodic re-identification: confirms the person on camera is
            # still the student who checked in, rather than a stand-in.
            reid: float | None = None
            now = time.monotonic()
            if (
                face_found
                and target_emb is not None
                and now - last_reid >= REID_EVERY
            ):
                last_reid = now
                emb = FaceEngine.instance().embed_largest(frame)
                if emb is not None:
                    reid = float(FaceEngine.similarity(emb, target_emb))
                    if (
                        reid < MATCH_THRESHOLD
                        and presence is not None
                        and now - last_mismatch >= MISMATCH_COOLDOWN
                    ):
                        last_mismatch = now
                        presence.identity_mismatch(reid)

            return {
                "type": "monitor",
                "face_found": face_found,
                "brightness": brightness,
                "reid": reid,
                "jpeg": _frame_to_jpeg_b64(frame),
            }
        return None

    # Run camera in a thread
    stop_event = threading.Event()
    loop = asyncio.get_event_loop()

    def _camera_thread() -> None:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            _push({"type": "error", "message": "Could not open camera"})
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        try:
            while not stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    # Losing the device mid-session is exactly the "student
                    # turned their camera off" case the instructor needs to
                    # hear about.
                    if presence is not None:
                        presence.camera_failed()
                    _push({"type": "error",
                           "message": "Camera stopped delivering frames."})
                    break
                frame = cv2.flip(frame, 1)
                try:
                    result = _analyse(frame)
                except Exception as exc:  # noqa: BLE001 – keep the stream alive
                    _push({"type": "error", "message": f"Analysis error: {exc}"})
                    result = None
                if result is not None:
                    _push(result)
                else:
                    _push({"type": "frame", "jpeg": _frame_to_jpeg_b64(frame)})
                time.sleep(0.05)
        finally:
            cap.release()

    cam_thread = threading.Thread(target=_camera_thread, daemon=True)
    cam_thread.start()

    def _resolve_embedding(msg: dict) -> np.ndarray:
        """Target embedding from either a raw blob or a student id.

        The frontend only knows student ids, so accepting `student_id` here is
        what makes check-in and monitoring work at all.
        """
        raw_b64 = msg.get("embedding_b64")
        if raw_b64:
            return np.frombuffer(base64.b64decode(raw_b64), dtype=np.float32)
        student_id = msg.get("student_id")
        if student_id is None:
            raise ValueError("start_recognize/start_monitor needs student_id")
        emb = db.get_student_embedding(int(student_id))
        if emb is None:
            raise ValueError(f"Student {student_id} has no enrolled face data")
        return np.asarray(emb, dtype=np.float32)

    async def _recv_loop() -> None:
        nonlocal mode, tracker, liveness, guided, target_emb
        nonlocal presence, last_reid, last_mismatch
        while True:
            try:
                msg = await websocket.receive_json()
            except (WebSocketDisconnect, RuntimeError):
                return
            except Exception:  # noqa: BLE001 – malformed frame, keep listening
                continue

            action = msg.get("action", "")
            try:
                with analysis_lock:
                    if action == "start_enroll":
                        if tracker:
                            tracker.close()
                        tracker = FaceMeshTracker()
                        guided = GuidedEnrollment(
                            tracker, _embed_largest,
                            directional=msg.get("directional", True),
                        )
                        liveness = None
                        presence = None
                        mode = "enroll"
                    elif action == "start_liveness":
                        if tracker:
                            tracker.close()
                        tracker = FaceMeshTracker()
                        liveness = LivenessChecker(
                            tracker, directional=msg.get("directional", True)
                        )
                        guided = None
                        presence = None
                        mode = "liveness"
                    elif action == "start_recognize":
                        target_emb = _resolve_embedding(msg)
                        presence = None
                        last_reid = 0.0   # check the first usable frame at once
                        mode = "recognize"
                    elif action == "start_monitor":
                        target_emb = _resolve_embedding(msg)
                        if tracker is None:
                            tracker = FaceMeshTracker()
                        presence = PresenceMonitor(
                            _presence_event,
                            out_of_frame_after=float(msg.get("out_of_frame_after", 5.0)),
                            camera_off_after=float(msg.get("camera_off_after", 3.0)),
                        )
                        last_reid = 0.0
                        last_mismatch = 0.0
                        mode = "monitor"
                    elif action == "stop":
                        presence = None
                        mode = "idle"
            except Exception as exc:  # noqa: BLE001
                # A bad control message must not tear down the socket — the
                # old code let one KeyError kill the whole session.
                with analysis_lock:
                    mode = "idle"
                await _send_json(websocket, {"type": "error", "message": str(exc)})

    async def _send_loop() -> None:
        while True:
            result = await result_queue.get()
            if not await _send_json(websocket, result):
                return

    try:
        await _run_until_first_done(_recv_loop(), _send_loop())
    finally:
        stop_event.set()
        if tracker:
            tracker.close()


# ── WebSocket: /ws/screen  (Meet Monitor page) ────────────────────────────────

@app.websocket("/ws/screen")
async def ws_screen(websocket: WebSocket) -> None:  # noqa: C901
    """Stream screen-region capture with face recognition.

    Client sends:
      {"action": "start", "region": {left,top,width,height}, "session_id": int,
       "missing_after": float}
      {"action": "stop"}
      {"action": "enroll_unknown", "index": int, "student_no": str, "name": str}

    Server sends:
      {"type": "frame",  "jpeg": str, "matches": [...], "unknowns": [...]}
      {"type": "roster", "students": [...]}
      {"type": "alert",  "message": str, "level": str}
      {"type": "error",  "message": str}
    """
    await websocket.accept()

    from app.core.face_engine import FaceEngine
    from app.core.roster_monitor import RosterMonitor
    from app.core.tile_tracker import TileTracker
    from app.data import db

    stop_event = threading.Event()
    loop = asyncio.get_event_loop()
    result_queue: asyncio.Queue = asyncio.Queue(maxsize=4)

    session_id: int | None = None
    roster_monitor: RosterMonitor | None = None
    tracker: TileTracker | None = None
    embeddings: list[tuple[int, np.ndarray]] = []
    names: dict[int, str] = {}
    unknowns_cache: list[dict] = []
    unknown_registry: dict[int, dict] = {}
    unknown_ids = itertools.count(1)
    verify_student: int | None = None
    verify_deadline = 0.0
    state_lock = threading.Lock()

    def _push(payload: dict) -> None:
        loop.call_soon_threadsafe(
            lambda p=payload: result_queue.put_nowait(p)
            if not result_queue.full() else None
        )

    def _roster_event(sid: int, event_type: str, message: str) -> None:
        nonlocal session_id
        s_id = session_id
        if s_id is None:
            return
        if event_type == "time_in":
            db.record_time_in(s_id, sid)
            db.log_event(s_id, sid, "verified", message)
            _push({"type": "alert", "message": message, "level": "ok"})
        elif event_type == "missing":
            db.log_event(s_id, sid, "out_of_frame", message)
            # A student who vanishes has effectively left the class; stamping
            # time-out here means the report reflects when they went, not
            # just when the session was closed.
            db.record_time_out(s_id, sid)
            _push({"type": "alert", "message": message, "level": "error"})
        elif event_type == "returned":
            db.log_event(s_id, sid, "back_in_frame", message)
            db.clear_time_out(s_id, sid)
            _push({"type": "alert", "message": message, "level": "ok"})

    def _capture_thread(region: dict, missing_after: float) -> None:
        nonlocal unknowns_cache, verify_student, verify_deadline
        import mss
        try:
            with mss.mss() as sct:
                while not stop_event.is_set():
                    start = time.monotonic()
                    shot = sct.grab(region)
                    frame = np.asarray(shot, dtype=np.uint8)[:, :, :3]
                    frame = np.ascontiguousarray(frame)

                    with state_lock:
                        t = tracker
                        rm = roster_monitor
                        embs = embeddings
                        nms = names

                    if t is None or rm is None:
                        time.sleep(0.1)
                        continue

                    matches, unknown_faces = t.process(frame)
                    rm.update({m[0] for m in matches})

                    # On-demand re-verification: the instructor tapped a
                    # student in the roster and is waiting for a fresh answer.
                    with state_lock:
                        pending = verify_student
                    if pending is not None:
                        hit = next((m for m in matches if m[0] == pending), None)
                        s_id = session_id
                        if hit is not None:
                            with state_lock:
                                verify_student = None
                            msg_txt = (
                                f"{nms.get(pending, 'Student')} re-verified on camera "
                                f"(score {hit[1]:.2f})."
                            )
                            if s_id is not None:
                                db.log_event(s_id, pending, "verified", msg_txt)
                            _push({"type": "verify_result", "student_id": pending,
                                   "ok": True, "score": float(hit[1]),
                                   "message": msg_txt})
                            _push({"type": "alert", "message": msg_txt, "level": "ok"})
                        elif time.monotonic() >= verify_deadline:
                            with state_lock:
                                verify_student = None
                            msg_txt = (
                                f"{nms.get(pending, 'Student')} could not be "
                                f"re-verified — face not recognised on screen."
                            )
                            if s_id is not None:
                                db.log_event(s_id, pending, "identity_mismatch", msg_txt)
                            _push({"type": "verify_result", "student_id": pending,
                                   "ok": False, "score": 0.0, "message": msg_txt})
                            _push({"type": "alert", "message": msg_txt, "level": "error"})

                    ulist = []
                    for emb, (x1, y1, x2, y2) in unknown_faces:
                        h, w = frame.shape[:2]
                        pad_x = int((x2 - x1) * 0.3)
                        pad_y = int((y2 - y1) * 0.3)
                        crop = frame[
                            max(0, y1 - pad_y):min(h, y2 + pad_y),
                            max(0, x1 - pad_x):min(w, x2 + pad_x),
                        ].copy()
                        if crop.size == 0:
                            continue
                        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                        # Each unknown face gets a stable id. Enrolling by list
                        # position used to attach the wrong face whenever the
                        # list shifted between the click and the message.
                        uid = next(unknown_ids)
                        entry = {
                            "uid": uid,
                            "embedding": base64.b64encode(emb.tobytes()).decode(),
                            "crop_jpeg": base64.b64encode(buf).decode(),
                            "bbox": [x1, y1, x2, y2],
                        }
                        ulist.append(entry)
                        unknown_registry[uid] = entry
                    while len(unknown_registry) > 64:
                        unknown_registry.pop(next(iter(unknown_registry)))
                    with state_lock:
                        unknowns_cache = ulist

                    # Build annotated frame
                    annotated = frame.copy()
                    for sid, score, (x1, y1, x2, y2) in matches:
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (80, 220, 120), 2)
                        label = f"{nms.get(sid, '?')} {score:.2f}"
                        cv2.putText(annotated, label, (x1, max(20, y1 - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 220, 120), 2)
                    for i, u in enumerate(ulist):
                        x1, y1, x2, y2 = u["bbox"]
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (90, 90, 240), 2)
                        cv2.putText(annotated, f"Unknown {i+1}", (x1, max(20, y1 - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (90, 90, 240), 2)

                    _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    jpeg = base64.b64encode(buf).decode()

                    roster = rm.status()
                    payload = {
                        "type": "frame",
                        "jpeg": jpeg,
                        "roster": roster,
                        "unknowns": [{"uid": u["uid"], "crop_jpeg": u["crop_jpeg"],
                                      "bbox": u["bbox"]} for u in ulist],
                    }
                    loop.call_soon_threadsafe(
                        lambda p=payload: result_queue.put_nowait(p)
                        if not result_queue.full() else None
                    )
                    elapsed = time.monotonic() - start
                    time.sleep(max(0, 0.067 - elapsed))  # ~15 fps
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                {"type": "error", "message": str(exc)},
            )

    cap_thread: threading.Thread | None = None

    async def _recv_loop() -> None:
        nonlocal session_id, roster_monitor, tracker, embeddings, names, cap_thread
        nonlocal verify_student, verify_deadline
        try:
            while True:
                msg = await websocket.receive_json()
                action = msg.get("action", "")

                if action == "start":
                    if not FaceEngine.is_ready():
                        await _send_json(websocket, {"type": "error", "message": "AI not ready"})
                        continue
                    region = msg["region"]
                    missing_after = float(msg.get("missing_after", 5.0))
                    session_name = msg.get("name", "")

                    sid = db.create_session(session_name)
                    roster = db.list_students()
                    embs = db.all_embeddings()
                    nms_map = {s["id"]: s["name"] for s in roster}
                    engine = FaceEngine.instance()
                    t = TileTracker(engine, lambda: embeddings)
                    rm = RosterMonitor(roster, _roster_event, missing_after=missing_after)

                    with state_lock:
                        session_id = sid
                        embeddings = embs
                        names = nms_map
                        tracker = t
                        roster_monitor = rm

                    stop_event.clear()
                    cap_thread = threading.Thread(
                        target=_capture_thread,
                        args=(region, missing_after),
                        daemon=True,
                    )
                    cap_thread.start()
                    await _send_json(websocket, {"type": "started", "session_id": sid})

                elif action == "stop":
                    stop_event.set()
                    s_id = session_id
                    if s_id is not None:
                        db.end_session(s_id)
                    with state_lock:
                        session_id = None
                        tracker = None
                        roster_monitor = None
                    await _send_json(websocket, {"type": "stopped"})

                elif action == "verify":
                    student_id = msg.get("student_id")
                    with state_lock:
                        t = tracker
                        active = session_id is not None
                    if student_id is None or not active or t is None:
                        await _send_json(websocket, {
                            "type": "error",
                            "message": "Start monitoring before verifying a student.",
                        })
                        continue
                    # Drop the cached identity so the next passes have to
                    # prove it again from a fresh embedding.
                    t.force_refresh()
                    with state_lock:
                        verify_student = int(student_id)
                        verify_deadline = time.monotonic() + float(
                            msg.get("timeout", 8.0)
                        )
                    await _send_json(websocket, {
                        "type": "verify_started", "student_id": int(student_id),
                    })

                elif action == "enroll_unknown":
                    uid = msg.get("uid")
                    idx = msg.get("index", -1)
                    with state_lock:
                        cache = unknowns_cache
                        u = unknown_registry.get(uid) if uid is not None else None
                    if u is None:
                        if idx < 0 or idx >= len(cache):
                            await _send_json(websocket, {
                                "type": "error",
                                "message": "That face is no longer on screen — "
                                           "click it again from the current list.",
                            })
                            continue
                        u = cache[idx]
                    raw = base64.b64decode(u["embedding"])
                    emb = np.frombuffer(raw, dtype=np.float32)
                    try:
                        new_id = db.add_student(msg["student_no"], msg["name"], emb)
                    except Exception as exc:
                        await _send_json(websocket, {"type": "error", "message": str(exc)})
                        continue
                    with state_lock:
                        embeddings.append((new_id, emb))
                        names[new_id] = msg["name"]
                        if roster_monitor:
                            roster_monitor.add_student({"id": new_id, "name": msg["name"]})
                        if tracker:
                            tracker.reidentify()
                        s_id = session_id
                    if s_id:
                        db.log_event(s_id, new_id, "verified",
                                     f"{msg['name']} enrolled live from meeting tile.")
                    await _send_json(websocket, {"type": "enrolled", "id": new_id,
                                                  "name": msg["name"]})
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            pass
        except Exception as exc:  # noqa: BLE001
            await _send_json(websocket, {"type": "error", "message": str(exc)})
        finally:
            stop_event.set()
            s_id = session_id
            if s_id is not None:
                db.end_session(s_id)

    async def _send_loop() -> None:
        while True:
            result = await result_queue.get()
            if not await _send_json(websocket, result):
                return

    try:
        await _run_until_first_done(_recv_loop(), _send_loop())
    finally:
        stop_event.set()

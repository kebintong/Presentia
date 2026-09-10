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
import threading
import time
from pathlib import Path
from typing import Any

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


# ── Engine status ─────────────────────────────────────────────────────────────

@app.get("/api/engine/status")
async def engine_status() -> dict:
    """Used by the Go shell to poll readiness; also consumed by the frontend."""
    ready = _engine_ready.is_set() and _engine_error is None
    return {"ready": ready, "error": _engine_error}


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


@app.post("/api/enroll/photos")
async def enroll_from_photos(
    student_no: str = Body(...),
    name: str = Body(...),
    files: list[UploadFile] = File(...),
) -> dict:
    """Accept 1-5 image files, extract face embeddings, average and save."""
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
    try:
        student_id = db.add_student(student_no, name, mean.astype(np.float32))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"id": student_id, "samples": len(embeddings)}


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


async def _send_json(ws: WebSocket, data: dict) -> None:
    try:
        await ws.send_json(data)
    except Exception:  # noqa: BLE001
        pass


# ── WebSocket: /ws/camera  (Register page + Session page webcam) ─────────────

@app.websocket("/ws/camera")
async def ws_camera(websocket: WebSocket) -> None:  # noqa: C901 – intentionally monolithic
    """Stream webcam frames with optional guided-enrollment or
    liveness+recognition analysis.

    Client sends JSON control messages:
      {"action": "start_enroll", "directional": true}
      {"action": "start_liveness", "directional": true}
      {"action": "start_recognize", "embedding_b64": "..."}
      {"action": "start_monitor",   "embedding_b64": "..."}
      {"action": "stop"}

    Server sends JSON frames:
      {"type": "frame",  "jpeg": "<base64>"}
      {"type": "enroll", ...guided state...}
      {"type": "liveness", ...liveness state...}
      {"type": "recognize", "found": bool, "score": float}
      {"type": "monitor",  "face_found": bool, "brightness": float, "reid": float|null}
      {"type": "error",   "message": str}
    """
    await websocket.accept()

    from app.core.camera import CameraThread, frame_brightness
    from app.core.face_engine import FaceEngine, MATCH_THRESHOLD
    from app.core.liveness import FaceMeshTracker, LivenessChecker
    from app.core.enrollment import GuidedEnrollment

    # Shared state (modified from both threads via asyncio queue)
    frame_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    result_queue: asyncio.Queue = asyncio.Queue(maxsize=8)

    # Analysis state
    mode = "idle"
    tracker: FaceMeshTracker | None = None
    liveness: LivenessChecker | None = None
    guided: GuidedEnrollment | None = None
    target_emb: np.ndarray | None = None
    analysis_lock = threading.Lock()

    def _embed_largest(frame: np.ndarray) -> np.ndarray | None:
        face = FaceEngine.instance().largest_face(frame)
        if face is None or float(face.det_score) < 0.55:
            return None
        return np.asarray(face.normed_embedding, dtype=np.float32)

    def _analyse(frame: np.ndarray) -> dict | None:
        nonlocal mode, tracker, liveness, guided, target_emb
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
            jpeg = _frame_to_jpeg_b64(frame)
            result["jpeg"] = jpeg
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
            result: dict[str, Any] = {
                "type": "monitor",
                "face_found": lms is not None,
                "brightness": float(frame_brightness(frame)),
                "reid": None,
                "jpeg": _frame_to_jpeg_b64(frame),
            }
            return result
        return None

    # Run camera in a thread
    stop_event = threading.Event()
    loop = asyncio.get_event_loop()

    def _camera_thread() -> None:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                {"type": "error", "message": "Could not open camera"},
            )
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        try:
            while not stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                frame = cv2.flip(frame, 1)
                result = _analyse(frame)
                if result is not None:
                    loop.call_soon_threadsafe(
                        lambda r=result: result_queue.put_nowait(r) if not result_queue.full() else None
                    )
                else:
                    jpeg = _frame_to_jpeg_b64(frame)
                    loop.call_soon_threadsafe(
                        lambda j=jpeg: result_queue.put_nowait({"type": "frame", "jpeg": j})
                        if not result_queue.full() else None
                    )
                time.sleep(0.05)
        finally:
            cap.release()

    cam_thread = threading.Thread(target=_camera_thread, daemon=True)
    cam_thread.start()

    async def _recv_loop() -> None:
        nonlocal mode, tracker, liveness, guided, target_emb
        try:
            while True:
                msg = await websocket.receive_json()
                action = msg.get("action", "")
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
                        mode = "enroll"
                    elif action == "start_liveness":
                        if tracker:
                            tracker.close()
                        tracker = FaceMeshTracker()
                        liveness = LivenessChecker(
                            tracker, directional=msg.get("directional", True)
                        )
                        guided = None
                        mode = "liveness"
                    elif action == "start_recognize":
                        raw = base64.b64decode(msg["embedding_b64"])
                        target_emb = np.frombuffer(raw, dtype=np.float32)
                        mode = "recognize"
                    elif action == "start_monitor":
                        raw = base64.b64decode(msg["embedding_b64"])
                        target_emb = np.frombuffer(raw, dtype=np.float32)
                        if tracker is None:
                            tracker = FaceMeshTracker()
                        mode = "monitor"
                    elif action == "stop":
                        mode = "idle"
        except (WebSocketDisconnect, Exception):
            pass

    async def _send_loop() -> None:
        try:
            while True:
                result = await result_queue.get()
                await _send_json(websocket, result)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        await asyncio.gather(_recv_loop(), _send_loop())
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
    state_lock = threading.Lock()

    def _roster_event(sid: int, event_type: str, message: str) -> None:
        nonlocal session_id
        s_id = session_id
        if s_id is None:
            return
        if event_type == "time_in":
            db.record_time_in(s_id, sid)
            db.log_event(s_id, sid, "verified", message)
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                {"type": "alert", "message": message, "level": "ok"},
            )
        elif event_type == "missing":
            db.log_event(s_id, sid, "out_of_frame", message)
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                {"type": "alert", "message": message, "level": "error"},
            )
        elif event_type == "returned":
            db.log_event(s_id, sid, "back_in_frame", message)
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                {"type": "alert", "message": message, "level": "ok"},
            )

    def _capture_thread(region: dict, missing_after: float) -> None:
        nonlocal unknowns_cache
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

                    ulist = []
                    for emb, (x1, y1, x2, y2) in unknown_faces:
                        h, w = frame.shape[:2]
                        pad_x = int((x2 - x1) * 0.3)
                        pad_y = int((y2 - y1) * 0.3)
                        crop = frame[
                            max(0, y1 - pad_y):min(h, y2 + pad_y),
                            max(0, x1 - pad_x):min(w, x2 + pad_x),
                        ].copy()
                        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 70])
                        ulist.append({
                            "embedding": base64.b64encode(emb.tobytes()).decode(),
                            "crop_jpeg": base64.b64encode(buf).decode(),
                            "bbox": [x1, y1, x2, y2],
                        })
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
                        "unknowns": [{"crop_jpeg": u["crop_jpeg"], "bbox": u["bbox"]}
                                     for u in ulist],
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

                elif action == "enroll_unknown":
                    idx = msg.get("index", -1)
                    with state_lock:
                        cache = unknowns_cache
                    if idx < 0 or idx >= len(cache):
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
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            stop_event.set()
            s_id = session_id
            if s_id is not None:
                db.end_session(s_id)

    async def _send_loop() -> None:
        try:
            while True:
                result = await result_queue.get()
                await _send_json(websocket, result)
        except (WebSocketDisconnect, Exception):
            pass

    try:
        await asyncio.gather(_recv_loop(), _send_loop())
    finally:
        stop_event.set()

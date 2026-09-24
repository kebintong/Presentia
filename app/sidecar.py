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
import sys
import threading
import time

import cv2
import numpy as np
from fastapi import (
    FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect,
    Body,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.data import db
from app.core import perf
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


perf.apply_process_priority()
threading.Thread(target=_preload_engine, daemon=True).start()


# ── Lifetime: never outlive the desktop app ───────────────────────────────────
#
# The Go shell also ties us to it with a Windows Job Object and kills us on
# exit; this is the belt-and-braces half. If the app that started us goes
# away for any reason (crash, End Task, a failed job assignment, Linux), we
# notice within a second or two and exit, releasing the webcam and port.

def _watch_parent(pid: int) -> None:
    import os

    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            k32.WaitForSingleObject(handle, 0xFFFFFFFF)  # until it exits
            os._exit(0)
    while True:  # fallback polling (POSIX, or no handle)
        time.sleep(1.5)
        try:
            os.kill(pid, 0)
        except OSError:
            os._exit(0)
        if sys.platform != "win32" and os.getppid() != pid:
            os._exit(0)


def _start_parent_watch() -> None:
    import os

    raw = os.environ.get("PRESENTIA_PARENT_PID", "")
    if raw.isdigit() and int(raw) > 0:
        threading.Thread(target=_watch_parent, args=(int(raw),), daemon=True).start()


_start_parent_watch()

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


@app.post("/api/shutdown", status_code=204)
async def shutdown_sidecar() -> None:
    """Exit now. The desktop app calls this at startup to clear out a
    sidecar left behind by an earlier run, then starts its own."""
    import os

    asyncio.get_event_loop().call_later(0.2, os._exit, 0)


# ── Performance: processing device + high performance mode ───────────────────

class PerfUpdate(BaseModel):
    device: str | None = None
    high_performance: bool | None = None


def _perf_payload() -> dict:
    import os

    return {
        "settings": perf.get_settings(),
        "devices": perf.list_gpus(),
        "gpu_runtime": perf.gpu_runtime(),
        "status": FaceEngine.status(),
        "cpu_cores": os.cpu_count() or 0,
        "threads": {"balanced": perf.cpu_threads(False), "high": perf.cpu_threads(True)},
    }


@app.get("/api/perf")
async def get_perf() -> dict:
    """Processing device options found on this machine and what is in use."""
    return await asyncio.to_thread(_perf_payload)


@app.put("/api/perf")
async def put_perf(body: PerfUpdate) -> dict:
    """Save the settings and rebuild the face engine with them."""
    valid = {"auto", "cpu"} | {g["id"] for g in perf.list_gpus()}
    if body.device is not None and body.device not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown device {body.device!r}")
    before = perf.get_settings()
    after = perf.save_settings(body.device, body.high_performance)
    if after != before:
        FaceEngine.reconfigure()
    return await asyncio.to_thread(_perf_payload)


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


# ── Live monitoring snapshot (for the native bubble, tray and pop-out) ──────
#
# While a Meet monitoring run is active, the native windows in the Go shell
# (the stats chip under the bubble, the tray tooltip and the Live View
# pop-out) poll these endpoints directly. They keep working when the main
# window is hidden in the tray, without routing video through the webview.

class _LiveMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.active = False
        self.name = ""
        self.started = 0.0
        self.jpeg: bytes = b""
        self.seq = 0
        self.counts = {"present": 0, "missing": 0, "waiting": 0, "total": 0, "unknown": 0}
        self.last_alert = ""
        self.last_level = ""

    def start(self, name: str) -> None:
        with self._lock:
            self._reset()
            self.active = True
            self.name = name
            self.started = time.time()

    def stop(self) -> None:
        with self._lock:
            self.active = False
            self.jpeg = b""

    def frame(self, jpeg: bytes) -> None:
        with self._lock:
            if self.active:
                self.jpeg = jpeg
                self.seq += 1

    def stats(self, roster: list[dict], unknown: int) -> None:
        c = {"present": 0, "missing": 0, "waiting": 0}
        for r in roster:
            c[r["state"]] = c.get(r["state"], 0) + 1
        c["total"] = len(roster)
        c["unknown"] = unknown
        with self._lock:
            self.counts = c

    def alert(self, message: str, level: str) -> None:
        with self._lock:
            self.last_alert, self.last_level = message, level

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active": self.active,
                "name": self.name,
                "elapsed": (time.time() - self.started) if self.active else 0.0,
                **self.counts,
                "last_alert": self.last_alert,
                "last_level": self.last_level,
                "frame_seq": self.seq,
            }

    def latest_frame(self, after: int) -> tuple[bytes, int] | None:
        with self._lock:
            if not self.active or not self.jpeg or self.seq <= after:
                return None
            return self.jpeg, self.seq


_live = _LiveMonitor()


@app.get("/api/monitor/live")
async def monitor_live() -> dict:
    """Counts, elapsed time and the last alert of the active monitoring run."""
    return _live.snapshot()


@app.get("/api/monitor/frame")
async def monitor_frame(after: int = 0) -> Response:
    """Newest annotated preview frame as JPEG; 204 if nothing newer than `after`."""
    got = _live.latest_frame(after)
    if got is None:
        return Response(status_code=204)
    jpeg, seq = got
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"X-Frame-Seq": str(seq), "Cache-Control": "no-store"})


# ── Following a selected window ───────────────────────────────────────────────

class _WindowFollower:
    """Captures one top-level window, wherever it is (Windows only).

    The window's own contents are rendered with PrintWindow
    (PW_RENDERFULLCONTENT), so it keeps being monitored while it is covered
    by other windows — e.g. after Alt+Tab to Chrome — or dragged partly off
    screen. Only a minimised or closed window pauses monitoring.

    A few apps draw in a way PrintWindow cannot see and come back black. If
    that keeps happening while the window is actually showing something, we
    fall back to grabbing its area of the screen (which needs it visible) and
    tell the instructor.
    """

    BLACK_FRAMES_BEFORE_FALLBACK = 8

    def __init__(self, hwnd: int, title: str) -> None:
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._wt = wintypes
        self.hwnd = wintypes.HWND(hwnd)
        self.title = title or "The selected window"
        u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
        self._u32, self._g32 = u32, g32
        u32.IsWindow.argtypes = [wintypes.HWND]
        u32.IsIconic.argtypes = [wintypes.HWND]
        u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        u32.GetDC.argtypes = [wintypes.HWND]
        u32.GetDC.restype = wintypes.HDC
        u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g32.CreateCompatibleDC.restype = wintypes.HDC
        g32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                         ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        g32.CreateDIBSection.restype = wintypes.HBITMAP
        g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g32.SelectObject.restype = wintypes.HGDIOBJ
        g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g32.DeleteDC.argtypes = [wintypes.HDC]
        try:
            self._dwm = ctypes.windll.dwmapi.DwmGetWindowAttribute
            self._dwm.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        except Exception:  # noqa: BLE001
            self._dwm = None
        self._dc = None
        self._bmp = None
        self._bits = None
        self._size = (0, 0)
        self._black = 0
        self.screen_only = False  # PrintWindow can't see this app

    @classmethod
    def create(cls, hwnd, title: str) -> "_WindowFollower | None":
        if sys.platform != "win32" or not hwnd:
            return None
        try:
            return cls(int(hwnd), title)
        except Exception:  # noqa: BLE001
            return None

    # -- geometry --------------------------------------------------------

    def _rects(self):
        """(window rect, visible frame rect). The window rect includes the
        invisible resize border; DWM's extended frame bounds do not."""
        wr, fr = self._wt.RECT(), self._wt.RECT()
        if not self._u32.GetWindowRect(self.hwnd, self._ct.byref(wr)):
            return None, None
        if self._dwm is None or self._dwm(self.hwnd, 9, self._ct.byref(fr), self._ct.sizeof(fr)) != 0:
            fr = wr
        return wr, fr

    def state(self) -> str:
        if not self._u32.IsWindow(self.hwnd):
            return "closed"
        if self._u32.IsIconic(self.hwnd):
            return "minimized"
        return "ok"

    # -- capture ---------------------------------------------------------

    def _ensure_buffer(self, w: int, h: int) -> bool:
        if self._bmp is not None and self._size == (w, h):
            return True
        self.close()
        ct = self._ct

        class BITMAPINFOHEADER(ct.Structure):
            _fields_ = [("biSize", ct.c_uint32), ("biWidth", ct.c_int32), ("biHeight", ct.c_int32),
                        ("biPlanes", ct.c_uint16), ("biBitCount", ct.c_uint16),
                        ("biCompression", ct.c_uint32), ("biSizeImage", ct.c_uint32),
                        ("biXPelsPerMeter", ct.c_int32), ("biYPelsPerMeter", ct.c_int32),
                        ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32)]

        bih = BITMAPINFOHEADER(ct.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        screen = self._u32.GetDC(None)
        self._dc = self._g32.CreateCompatibleDC(screen)
        self._u32.ReleaseDC(None, screen)
        bits = ct.c_void_p()
        self._bmp = self._g32.CreateDIBSection(self._dc, ct.byref(bih), 0, ct.byref(bits), None, 0)
        if not self._bmp or not bits.value:
            self.close()
            return False
        self._g32.SelectObject(self._dc, self._bmp)
        self._bits = (ct.c_ubyte * (w * h * 4)).from_address(bits.value)
        self._size = (w, h)
        return True

    def _print_window(self, wr, fr) -> np.ndarray | None:
        w, h = wr.right - wr.left, wr.bottom - wr.top
        if w < 40 or h < 40 or not self._ensure_buffer(w, h):
            return None
        if not self._u32.PrintWindow(self.hwnd, self._dc, 2):  # PW_RENDERFULLCONTENT
            return None
        self._g32.GdiFlush()
        full = np.frombuffer(self._bits, dtype=np.uint8).reshape(h, w, 4)
        # Crop to the visible frame (drop the invisible resize border).
        x0, y0 = max(0, fr.left - wr.left), max(0, fr.top - wr.top)
        x1, y1 = min(w, fr.right - wr.left), min(h, fr.bottom - wr.top)
        return np.ascontiguousarray(full[y0:y1, x0:x1, :3])

    def _screen_grab(self, sct, desktop: dict, fr) -> np.ndarray | None:
        left = max(fr.left, desktop["left"])
        top = max(fr.top, desktop["top"])
        right = min(fr.right, desktop["left"] + desktop["width"])
        bottom = min(fr.bottom, desktop["top"] + desktop["height"])
        if right - left < 40 or bottom - top < 40:
            return None
        shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
        return np.ascontiguousarray(np.asarray(shot, dtype=np.uint8)[:, :, :3])

    def capture(self, sct, desktop: dict) -> tuple[str, np.ndarray | None]:
        """(state, BGR frame or None). States: ok, minimized, closed,
        offscreen (screen-only mode), screen_only (switched to screen mode)."""
        st = self.state()
        if st != "ok":
            return st, None
        wr, fr = self._rects()
        if wr is None:
            return "closed", None
        if not self.screen_only:
            frame = self._print_window(wr, fr)
            if frame is not None and frame.size and frame[::8, ::8].max() > 12:
                self._black = 0
                return "ok", frame
            self._black += 1
            if self._black < self.BLACK_FRAMES_BEFORE_FALLBACK:
                return "ok", None  # a transient blank frame; try again
            self.screen_only = True
            self.close()
            frame = self._screen_grab(sct, desktop, fr)
            return "screen_only", frame
        frame = self._screen_grab(sct, desktop, fr)
        return ("screen_only" if frame is not None else "offscreen"), frame

    def describe(self, state: str, previous: str) -> tuple[str, str] | None:
        if state == "ok" or (state == "screen_only" and previous == "offscreen"):
            if previous in ("minimized", "offscreen", "closed"):
                return (f"{self.title} is back — monitoring resumed.", "ok")
            return None
        return {
            "minimized": (f"{self.title} is minimised — monitoring paused until it is restored.", "warn"),
            "offscreen": (f"{self.title} is off-screen — monitoring paused.", "warn"),
            "closed": (f"{self.title} was closed — monitoring paused. Select another window.", "error"),
            "screen_only": (f"{self.title} can't be read while it is covered, so keep it visible "
                            f"on screen for monitoring to work.", "warn"),
        }.get(state)

    def close(self) -> None:
        if self._bmp:
            self._g32.DeleteObject(self._bmp)
        if self._dc:
            self._g32.DeleteDC(self._dc)
        self._dc = self._bmp = self._bits = None
        self._size = (0, 0)


# ── WebSocket helpers ─────────────────────────────────────────────────────────

def _frame_to_jpeg_b64(frame: np.ndarray, quality: int = 72) -> str:
    """Encode a BGR frame to JPEG and return as base64 string."""
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf).decode()


def _frame_to_jpeg(frame: np.ndarray, quality: int = 72) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


def _orient(frame: np.ndarray, mirror: bool, invert: bool) -> np.ndarray:
    """Apply the viewer's camera orientation.

    mirror = selfie view (horizontal flip); invert = rotate 180 degrees, for
    cameras mounted or reporting upside down. Both together = vertical flip.
    """
    if mirror and invert:
        return cv2.flip(frame, 0)
    if mirror:
        return cv2.flip(frame, 1)
    if invert:
        return cv2.flip(frame, -1)
    return frame


class _LatestFrame:
    """Single-slot mailbox: the producer overwrites, the consumer always gets
    the newest item. Slow consumers drop stale frames instead of lagging."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._item = None

    def put(self, item) -> None:
        with self._cond:
            self._item = item
            self._cond.notify()

    def take(self, timeout: float = 0.5):
        with self._cond:
            if self._item is None:
                self._cond.wait(timeout)
            item, self._item = self._item, None
            return item


class _FramePump:
    """Delivers preview JPEGs to the socket at whatever rate it can take,
    always the newest one, without ever queueing a backlog.

    Frames go out as binary WebSocket messages (raw JPEG bytes). Base64
    inside JSON made every frame a third bigger and forced the page to parse
    and decode it on its UI thread; binary frames are decoded off-thread by
    the browser (createImageBitmap). JSON messages remain for everything else.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._event = asyncio.Event()
        self._jpeg: bytes | None = None

    def publish(self, jpeg: bytes) -> None:  # any thread
        self._jpeg = jpeg
        self._loop.call_soon_threadsafe(self._event.set)

    async def run(self, ws: WebSocket, send_lock: asyncio.Lock) -> None:
        while True:
            await self._event.wait()
            self._event.clear()
            jpeg, self._jpeg = self._jpeg, None
            if jpeg is None:
                continue
            async with send_lock:
                try:
                    await ws.send_bytes(jpeg)
                except Exception:  # noqa: BLE001 - socket gone
                    return


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
      {"action": "set_view", "mirror": true, "invert": false}  # any time
      {"action": "stop"}

    Preview frames and analysis run on separate threads: the camera is read
    and streamed at its native rate (~30 fps) while analysis — which can take
    hundreds of ms when an embedding is computed — works on the newest frame
    whenever it is free. A slow analysis step never stalls the preview.

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

    # Analysis results are small and some must never be dropped (the final
    # enrollment message carries the embedding), so the queue is generous.
    result_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    send_lock = asyncio.Lock()
    view = {"mirror": True, "invert": False}

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
    # Held by the analysis thread while it works on a frame, so a tracker is
    # never closed underneath it (see _retire).
    work_lock = threading.Lock()

    def _retire(old) -> None:
        """Close a replaced FaceMesh tracker once analysis is done with it,
        without blocking the event loop."""
        if old is None:
            return

        def _close() -> None:
            with work_lock:
                old.close()

        threading.Thread(target=_close, daemon=True).start()

    def _set_view(msg: dict) -> None:
        if "mirror" in msg:
            view["mirror"] = bool(msg["mirror"])
        if "invert" in msg:
            view["invert"] = bool(msg["invert"])
        # Left/right prompts are defined for the mirrored (selfie) view.
        for obj in (guided, liveness):
            if obj is not None:
                obj.mirrored = view["mirror"]

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
            return result
        if m == "recognize":
            # Embedding a face costs ~1s on CPU. Throttling it keeps the
            # preview moving instead of freezing on every frame, and stops
            # the attempt counter from burning through in a couple of seconds.
            now = time.monotonic()
            if now - last_reid < RECOGNIZE_EVERY:
                return None
            last_reid = now
            emb = FaceEngine.instance().embed_largest(frame)
            if emb is None:
                return {"type": "recognize", "found": False, "score": 0.0}
            score = FaceEngine.similarity(emb, target_emb)
            return {"type": "recognize", "found": True, "score": float(score)}
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
            }
        return None

    # Camera → preview on one thread, analysis on another.
    stop_event = threading.Event()
    loop = asyncio.get_event_loop()
    frames = _FramePump(loop)
    to_analyse = _LatestFrame()

    def _camera_thread() -> None:
        from app.core.camera import open_fast_capture

        cap, first = open_fast_capture(0)
        if cap is None:
            _push({"type": "error", "message": "Could not open camera"})
            return
        min_gap = 1.0 / 30.0  # some virtual cameras return frames instantly
        last = 0.0
        frame = first
        try:
            while not stop_event.is_set():
                if frame is None:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        # Losing the device mid-session is exactly the
                        # "student turned their camera off" case.
                        if presence is not None:
                            presence.camera_failed()
                        _push({"type": "error",
                               "message": "Camera stopped delivering frames."})
                        break
                frame = _orient(frame, view["mirror"], view["invert"])
                to_analyse.put(frame)
                frames.publish(_frame_to_jpeg(frame))
                frame = None
                gap = time.monotonic() - last
                if gap < min_gap:
                    time.sleep(min_gap - gap)
                last = time.monotonic()
        finally:
            cap.release()
            to_analyse.put(None)

    def _analysis_thread() -> None:
        while not stop_event.is_set():
            frame = to_analyse.take()
            if frame is None:
                continue
            try:
                with work_lock:
                    result = _analyse(frame)
            except Exception as exc:  # noqa: BLE001 – keep the stream alive
                _push({"type": "error", "message": f"Analysis error: {exc}"})
                continue
            if result is not None:
                _push(result)

    cam_thread = threading.Thread(target=_camera_thread, daemon=True)
    cam_thread.start()
    threading.Thread(target=_analysis_thread, daemon=True).start()

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
                        _retire(tracker)
                        tracker = FaceMeshTracker()
                        guided = GuidedEnrollment(
                            tracker, _embed_largest,
                            directional=msg.get("directional", True),
                        )
                        guided.mirrored = view["mirror"]
                        liveness = None
                        presence = None
                        mode = "enroll"
                    elif action == "start_liveness":
                        _retire(tracker)
                        tracker = FaceMeshTracker()
                        liveness = LivenessChecker(
                            tracker, directional=msg.get("directional", True)
                        )
                        liveness.mirrored = view["mirror"]
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
                    if action == "set_view" or "mirror" in msg or "invert" in msg:
                        _set_view(msg)
            except Exception as exc:  # noqa: BLE001
                # A bad control message must not tear down the socket — the
                # old code let one KeyError kill the whole session.
                with analysis_lock:
                    mode = "idle"
                async with send_lock:
                    await _send_json(websocket, {"type": "error", "message": str(exc)})

    async def _send_loop() -> None:
        while True:
            result = await result_queue.get()
            async with send_lock:
                if not await _send_json(websocket, result):
                    return

    try:
        await _run_until_first_done(
            _recv_loop(), _send_loop(), frames.run(websocket, send_lock)
        )
    finally:
        stop_event.set()
        _retire(tracker)


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
    result_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    session_id: int | None = None
    roster_monitor: RosterMonitor | None = None
    tracker: TileTracker | None = None
    embeddings: list[tuple[int, np.ndarray]] = []
    names: dict[int, str] = {}
    unknowns_cache: list[dict] = []
    unknown_registry: dict[int, dict] = {}
    verify_student: int | None = None
    verify_deadline = 0.0
    state_lock = threading.Lock()

    def _push(payload: dict) -> None:
        if payload.get("type") == "alert":
            _live.alert(payload.get("message", ""), payload.get("level", "info"))
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

    # Screen → preview and analysis run on separate threads. The preview is
    # captured at PREVIEW_FPS, downscaled and overlaid with the most recent
    # analysis boxes; recognition works on the newest frame whenever it is
    # free. Meeting tiles barely move, so boxes that are a few frames old
    # still sit on the right faces.
    PREVIEW_FPS = 24
    PREVIEW_MAX_W = 1280
    ANALYSIS_PUSH_EVERY = 0.25  # roster/unknowns updates, unless something changed
    CROP_REFRESH = 2.0          # re-encode an unknown face's thumbnail at most this often

    frames = _FramePump(loop)
    send_lock = asyncio.Lock()
    to_analyse = _LatestFrame()
    overlay: list[tuple[tuple[int, int, int, int], str, tuple[int, int, int]]] = []
    crop_cache: dict[int, tuple[float, str]] = {}

    def _preview_thread(region: dict, stop: threading.Event) -> None:
        import mss
        gap = 1.0 / PREVIEW_FPS
        follow = None
        try:
            with mss.mss() as sct:
                # A window picked with "Select Window" is captured directly,
                # wherever it is and whatever covers it. A screen area is a
                # fixed rectangle of the screen.
                follow = _WindowFollower.create(region.get("hwnd"), region.get("title", ""))
                desktop = sct.monitors[0]
                state = "ok"
                while not stop.is_set() and not stop_event.is_set():
                    start = time.monotonic()
                    if follow is not None:
                        # The window itself, even behind other windows.
                        now_state, frame = follow.capture(sct, desktop)
                        if now_state != state:
                            msg = follow.describe(now_state, state)
                            state = now_state
                            if msg:
                                _push({"type": "alert", "message": msg[0], "level": msg[1]})
                        if frame is None:
                            time.sleep(0.2 if state != "ok" else 0.03)
                            continue
                    else:
                        shot = sct.grab(region)
                        frame = np.ascontiguousarray(np.asarray(shot, dtype=np.uint8)[:, :, :3])
                    to_analyse.put(frame)

                    h, w = frame.shape[:2]
                    k = min(1.0, PREVIEW_MAX_W / float(w))
                    view = (cv2.resize(frame, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
                            if k < 1.0 else frame.copy())
                    boxes = overlay  # swapped atomically by the analysis thread
                    for (x1, y1, x2, y2), label, colour in boxes:
                        p1 = (int(x1 * k), int(y1 * k))
                        p2 = (int(x2 * k), int(y2 * k))
                        cv2.rectangle(view, p1, p2, colour, 2)
                        cv2.putText(view, label, (p1[0], max(18, p1[1] - 7)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2, cv2.LINE_AA)
                    ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        raw = buf.tobytes()
                        _live.frame(raw)
                        frames.publish(raw)

                    remaining = gap - (time.monotonic() - start)
                    if remaining > 0:
                        time.sleep(remaining)
        except Exception as exc:  # noqa: BLE001
            _push({"type": "error", "message": str(exc)})
        finally:
            if follow is not None:
                follow.close()
            to_analyse.put(None)

    def _analysis_thread(stop: threading.Event) -> None:
        nonlocal unknowns_cache, verify_student, verify_deadline, overlay
        last_push = 0.0
        last_sig = None
        last_pass = 0.0
        try:
            while not stop.is_set() and not stop_event.is_set():
                frame = to_analyse.take()
                if frame is None:
                    continue

                with state_lock:
                    t = tracker
                    rm = roster_monitor
                    nms = names

                if t is None or rm is None:
                    continue

                # Balanced mode paces recognition so it leaves CPU for the
                # preview and the meeting app; High performance does not.
                gap = perf.analysis_min_interval() - (time.monotonic() - last_pass)
                if gap > 0:
                    time.sleep(gap)
                    fresh = to_analyse.take(timeout=0)
                    if fresh is not None:
                        frame = fresh
                last_pass = time.monotonic()

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

                # Unknown faces keep their tracker id while they stay on
                # screen, so a click always enrolls the face that was shown.
                now = time.monotonic()
                h, w = frame.shape[:2]
                ulist = []
                for emb, (x1, y1, x2, y2), uid in unknown_faces:
                    cached = crop_cache.get(uid)
                    if cached is None or now - cached[0] >= CROP_REFRESH:
                        pad_x = int((x2 - x1) * 0.3)
                        pad_y = int((y2 - y1) * 0.3)
                        crop = frame[
                            max(0, y1 - pad_y):min(h, y2 + pad_y),
                            max(0, x1 - pad_x):min(w, x2 + pad_x),
                        ]
                        if crop.size == 0:
                            continue
                        cached = (now, _frame_to_jpeg_b64(crop, 70))
                        crop_cache[uid] = cached
                    entry = {
                        "uid": uid,
                        "embedding": base64.b64encode(emb.tobytes()).decode(),
                        "crop_jpeg": cached[1],
                        "bbox": [x1, y1, x2, y2],
                    }
                    ulist.append(entry)
                    unknown_registry[uid] = entry
                live = {u["uid"] for u in ulist}
                for uid in [u for u in crop_cache if u not in live]:
                    crop_cache.pop(uid, None)
                while len(unknown_registry) > 64:
                    unknown_registry.pop(next(iter(unknown_registry)))
                with state_lock:
                    unknowns_cache = ulist

                boxes = [((x1, y1, x2, y2), f"{nms.get(sid, '?')} {score:.2f}", (80, 220, 120))
                         for sid, score, (x1, y1, x2, y2) in matches]
                boxes += [(tuple(u["bbox"]), f"Unknown {i + 1}", (90, 90, 240))
                          for i, u in enumerate(ulist)]
                overlay = boxes

                roster = rm.status()
                _live.stats(roster, len(ulist))
                sig = (tuple((r["id"], r["state"]) for r in roster), tuple(sorted(live)))
                if sig != last_sig or now - last_push >= ANALYSIS_PUSH_EVERY:
                    last_sig, last_push = sig, now
                    _push({
                        "type": "analysis",
                        "roster": roster,
                        "unknowns": [{"uid": u["uid"], "crop_jpeg": u["crop_jpeg"],
                                      "bbox": u["bbox"]} for u in ulist],
                    })
        except Exception as exc:  # noqa: BLE001
            _push({"type": "error", "message": str(exc)})

    cap_thread: threading.Thread | None = None
    run_stop = threading.Event()

    async def _say(data: dict) -> bool:
        async with send_lock:
            return await _send_json(websocket, data)

    async def _recv_loop() -> None:
        nonlocal session_id, roster_monitor, tracker, embeddings, names, cap_thread
        nonlocal verify_student, verify_deadline, overlay, run_stop
        try:
            while True:
                msg = await websocket.receive_json()
                action = msg.get("action", "")

                if action == "start":
                    if not FaceEngine.is_ready():
                        await _say({"type": "error", "message": "AI not ready"})
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

                    # Each run gets its own stop flag, so threads from a
                    # previous run can never keep going after a quick restart.
                    run_stop.set()
                    run_stop = threading.Event()
                    overlay = []
                    cap_thread = threading.Thread(
                        target=_preview_thread, args=(region, run_stop), daemon=True,
                    )
                    cap_thread.start()
                    threading.Thread(target=_analysis_thread, args=(run_stop,),
                                     daemon=True).start()
                    _live.start(session_name or "Meet session")
                    _live.stats(rm.status(), 0)
                    await _say({"type": "started", "session_id": sid})

                elif action == "stop":
                    run_stop.set()
                    _live.stop()
                    s_id = session_id
                    if s_id is not None:
                        db.end_session(s_id)
                    with state_lock:
                        session_id = None
                        tracker = None
                        roster_monitor = None
                    await _say({"type": "stopped"})

                elif action == "verify":
                    student_id = msg.get("student_id")
                    with state_lock:
                        t = tracker
                        active = session_id is not None
                    if student_id is None or not active or t is None:
                        await _say({
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
                    await _say({
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
                            await _say({
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
                        await _say({"type": "error", "message": str(exc)})
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
                    await _say({"type": "enrolled", "id": new_id,
                                                  "name": msg["name"]})
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            pass
        except Exception as exc:  # noqa: BLE001
            await _say({"type": "error", "message": str(exc)})
        finally:
            stop_event.set()
            s_id = session_id
            if s_id is not None:
                db.end_session(s_id)

    async def _send_loop() -> None:
        while True:
            result = await result_queue.get()
            async with send_lock:
                if not await _send_json(websocket, result):
                    return

    try:
        await _run_until_first_done(
            _recv_loop(), _send_loop(), frames.run(websocket, send_lock)
        )
    finally:
        stop_event.set()
        _live.stop()

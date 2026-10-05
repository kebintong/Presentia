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
import os
import sys
import threading
import time
import traceback

import cv2
import numpy as np
from fastapi import (
    FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect,
    Body,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.data import cloud, db
from app.core import diag, perf
from app.core.face_engine import FaceEngine, MATCH_THRESHOLD

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

# Polled several times a second; only logged when they fail.
_QUIET_PATHS = ("/api/engine/status", "/api/monitor/", "/api/diagnostics", "/api/screen/screenshot")


@app.middleware("http")
async def _diagnostic_log(request, call_next):
    """Diagnostic mode: one activity-log line per request. Always: crashes
    go to the recent-problems list."""
    started = time.perf_counter()
    path = request.url.path
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        diag.record("engine", f"{request.method} {path} crashed: {exc!r}")
        diag.log(traceback.format_exc().rstrip(), "error")
        raise
    if diag.enabled and request.method != "OPTIONS":
        status = response.status_code
        if status >= 400 or not path.startswith(_QUIET_PATHS):
            ms = round((time.perf_counter() - started) * 1000)
            diag.log(f"{request.method} {path} -> {status} ({ms} ms)",
                     "error" if status >= 500 else "warning" if status >= 400 else "info")
    return response

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
        diag.record("engine", f"Face models failed to load: {exc!r}")
        _engine_ready.set()


diag.install()
perf.apply_process_priority()
# Tests (and CI) import this module without the face models; they set
# PRESENTIA_SKIP_ENGINE=1 so nothing is downloaded or loaded.
if os.environ.get("PRESENTIA_SKIP_ENGINE") != "1":
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
    class_id: int | None = None  # also put the student on this class's roster


class SessionCreate(BaseModel):
    name: str
    class_id: int | None = None


class ClassCreate(BaseModel):
    name: str
    section: str = ""


class ClassUpdate(BaseModel):
    name: str | None = None
    section: str | None = None


class StudentUpdate(BaseModel):
    student_no: str | None = None
    name: str | None = None


class CloudSettings(BaseModel):
    url: str


class OnlineToggle(BaseModel):
    enabled: bool


class DeleteAllData(BaseModel):
    confirm: str


class DiagnosticMode(BaseModel):
    enabled: bool


class ClientEvent(BaseModel):
    """A problem the app's screens ran into (failed request, script error)."""
    kind: str = "error"
    title: str = ""
    message: str = ""
    path: str = ""
    status: int = 0


class ReportUpload(BaseModel):
    summary: str = ""
    text: str
    app_version: str = ""


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
    # "message" says what first launch is busy with (hardware check, model
    # download progress) so the app can show it instead of a bare spinner.
    return {"ready": ready, "error": _engine_error, "message": FaceEngine.status().get("message")}


@app.get("/api/diagnostics")
async def diagnostics(network: bool = True) -> dict:
    """Settings → Diagnostics: this computer, the website connection step by
    step, recent problems and the end of the sidecar log. Nothing is sent
    anywhere; the instructor copies the report themselves."""
    import platform
    import tempfile

    def system() -> list[dict]:
        rows = [
            ("Python", f"{platform.python_version()} ({'installed app' if getattr(sys, 'frozen', False) else 'development'})"),
            ("System", platform.platform()),
            ("Data folder", str(db.DB_PATH.parent)),
        ]
        try:
            with db._connect() as conn:  # noqa: SLF001
                rows.append(("Database schema", str(conn.execute("PRAGMA user_version").fetchone()[0])))
        except Exception as exc:  # noqa: BLE001
            rows.append(("Database", f"error: {exc}"))
        st = FaceEngine.status()
        if _engine_error:
            engine = f"failed: {_engine_error}"
        elif _engine_ready.is_set():
            engine = f"ready ({st.get('backend') or '?'} on {st.get('device') or '?'})"
        else:
            engine = st.get("message") or "loading"
        rows.append(("Face engine", engine))
        try:
            rows.append(("ONNX providers", ", ".join(perf.available_providers())))
        except Exception:  # noqa: BLE001
            pass
        for mod in ("truststore", "certifi"):
            try:
                m = __import__(mod)
                rows.append((mod, getattr(m, "__version__", "present")))
            except Exception:  # noqa: BLE001
                rows.append((mod, "missing"))
        return [{"label": k, "value": v} for k, v in rows]

    def log_tail(lines: int = 60) -> str:
        path = os.path.join(tempfile.gettempdir(), "presentia-sidecar-stderr.log")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-lines:])
        except OSError:
            return ""

    net = await asyncio.to_thread(cloud.diagnose, 5.0) if network else None
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system": system(),
        "network": net,
        "recent": diag.recent(),
        "log": log_tail(),
        "mode": {**diag.mode(), "reports_url": cloud.reports_url()},
    }


@app.get("/api/diagnostics/mode")
async def diagnostic_mode() -> dict:
    return {**diag.mode(), "reports_url": cloud.reports_url()}


@app.put("/api/diagnostics/mode")
async def set_diagnostic_mode(body: DiagnosticMode) -> dict:
    return {**diag.set_mode(body.enabled), "reports_url": cloud.reports_url()}


@app.post("/api/diagnostics/event", status_code=204)
async def diagnostic_event(body: ClientEvent) -> None:
    where = f" ({body.path} → {body.status})" if body.path else ""
    diag.record("app", f"{body.title or body.kind}: {body.message}{where}",
                "warning" if body.kind == "warning" else "error")


@app.get("/api/diagnostics/log")
async def diagnostic_log(lines: int = 400) -> dict:
    return {"text": diag.read_log(max(1, min(lines, 5000))), "path": str(diag.log_path()),
            "size": diag.log_size(), "enabled": diag.enabled}


@app.delete("/api/diagnostics/log", status_code=204)
async def clear_diagnostic_log() -> None:
    diag.clear_log()


@app.post("/api/diagnostics/send")
async def send_diagnostic_report(body: ReportUpload) -> dict:
    """Upload a report the user has seen and agreed to send."""
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="The report is empty.")
    try:
        out = await asyncio.to_thread(cloud.send_report, body.summary, body.text, body.app_version)
    except cloud.CloudError as exc:
        raise _cloud_error(exc) from exc
    diag.log(f"Report {out.get('id')} sent.")
    return out


@app.post("/api/shutdown", status_code=204)
async def shutdown_sidecar() -> None:
    """Exit now. The desktop app calls this at startup to clear out a
    sidecar left behind by an earlier run, then starts its own."""
    import os

    asyncio.get_event_loop().call_later(0.2, os._exit, 0)


# ── Performance: processing device + profile ─────────────────────────────────

class PerfUpdate(BaseModel):
    device: str | None = None
    profile: str | None = None


class ChecksUpdate(BaseModel):
    random_challenges: bool | None = None
    antispoof: bool | None = None


def _perf_payload() -> dict:
    import os

    s = perf.get_settings()
    return {
        "settings": {k: s.get(k) for k in ("device", "profile")},
        "effective_profile": perf.effective_profile(s),
        "profiles": {k: {"label": v["label"], "summary": v["summary"]} for k, v in perf.PROFILES.items()},
        "benchmark": {k: v for k, v in (s.get("benchmark") or {}).items() if k != "signature"},
        "devices": perf.list_gpus(),
        "gpu_runtime": perf.gpu_runtime(),
        "status": FaceEngine.status(),
        "cpu_cores": os.cpu_count() or 0,
        "ram_gb": round(perf.total_ram_gb(), 1),
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
    if body.profile is not None and body.profile not in {"auto", *perf.PROFILES}:
        raise HTTPException(status_code=400, detail=f"Unknown profile {body.profile!r}")
    before = perf.get_settings()
    after = perf.save_settings(device=body.device, profile=body.profile)
    if after != before:
        FaceEngine.reconfigure()
    return await asyncio.to_thread(_perf_payload)


@app.post("/api/perf/benchmark")
async def rerun_benchmark() -> dict:
    """Measure this computer again (Settings → Performance → Check again)."""
    await asyncio.to_thread(perf.benchmark, True)
    FaceEngine.reconfigure()
    return await asyncio.to_thread(_perf_payload)


# ── Check-in security (Settings → Accessibility) ─────────────────────────────

@app.get("/api/checks")
async def get_checks() -> dict:
    s = perf.get_settings()
    return {"random_challenges": bool(s.get("random_challenges", True)),
            "antispoof": bool(s.get("antispoof", False)),
            "profile": perf.effective_profile(s)}


@app.put("/api/checks")
async def put_checks(body: ChecksUpdate) -> dict:
    perf.save_settings(random_challenges=body.random_challenges, antispoof=body.antispoof)
    if body.antispoof:
        # Fetch the two small models now, not during a student's check-in.
        def _warm() -> None:
            try:
                from app.core.antispoof import AntiSpoof

                AntiSpoof.instance()
            except Exception:  # noqa: BLE001 - retried at check-in
                pass

        threading.Thread(target=_warm, daemon=True).start()
    return await get_checks()


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


# ── Classes ───────────────────────────────────────────────────────────────────
#
# What the instructor picks on the start screen. Students and sessions belong
# to a class; a student can be on several rosters with one set of face data.

_CLASS_NAME_MAX = 80


def _require_class(class_id: int) -> dict:
    cls = db.get_class(class_id)
    if cls is None:
        raise HTTPException(status_code=404, detail="Class not found")
    return cls


def _clean_class_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Class name is required")
    return name[:_CLASS_NAME_MAX]


@app.get("/api/classes")
async def list_classes() -> list[dict]:
    return db.list_classes()


@app.post("/api/classes", status_code=201)
async def create_class(body: ClassCreate) -> dict:
    return db.create_class(_clean_class_name(body.name), body.section.strip()[:_CLASS_NAME_MAX])


@app.get("/api/classes/{class_id}")
async def get_class(class_id: int) -> dict:
    return _require_class(class_id)


@app.patch("/api/classes/{class_id}")
async def update_class(class_id: int, body: ClassUpdate) -> dict:
    _require_class(class_id)
    db.update_class(
        class_id,
        name=_clean_class_name(body.name) if body.name is not None else None,
        section=body.section.strip()[:_CLASS_NAME_MAX] if body.section is not None else None,
    )
    cls = _require_class(class_id)
    if cls["online"]:
        # Students see the new name on the website. Best effort: a rename
        # must not fail because the internet is down.
        try:
            await asyncio.to_thread(cloud.publish, cls)
        except cloud.CloudError:
            pass
    return cls


@app.post("/api/classes/{class_id}/open")
async def open_class(class_id: int) -> dict:
    """Called when the instructor enters a class from the start screen."""
    _require_class(class_id)
    db.touch_class(class_id)
    return _require_class(class_id)


@app.post("/api/classes/{class_id}/join-code")
async def regenerate_join_code(class_id: int) -> dict:
    """Issue a new join code; the old one stops working."""
    cls = _require_class(class_id)
    if cls["online"]:
        try:
            await asyncio.to_thread(cloud.unpublish, cls["join_code"])
        except cloud.CloudError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
    code = db.regenerate_join_code(class_id)
    if cls["online"]:
        try:
            code = await asyncio.to_thread(cloud.publish, _require_class(class_id))
        except cloud.CloudError as exc:
            db.set_class_online(class_id, False)
            raise HTTPException(status_code=502, detail=exc.message) from exc
    return {"join_code": code}


@app.delete("/api/classes/{class_id}")
async def delete_class(class_id: int) -> dict:
    cls = _require_class(class_id)
    if cls["online"]:
        # Remove it from the website too, with anything still waiting there.
        try:
            await asyncio.to_thread(cloud.unpublish, cls["join_code"])
        except cloud.CloudError:
            pass  # unreachable: the website deletes uncollected data after 14 days anyway
    return {"students_removed": db.delete_class(class_id)}


# ── All data (Settings → Data) ───────────────────────────────────────────────

@app.get("/api/data/summary")
async def data_summary() -> dict:
    return {**db.data_summary(), "data_folder": str(db.DB_PATH.parent)}


@app.post("/api/data/delete-all")
async def delete_all_data(body: DeleteAllData) -> dict:
    """Delete every class, student (face data included), session and
    attendance record on this computer. The user must type DELETE."""
    if body.confirm.strip().upper() != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm.")
    # Take online classes off the website first, with anything still waiting
    # there. If the website can't be reached it deletes it after 14 days anyway.
    website_problems = []
    for cls in db.list_classes():
        if cls.get("online"):
            try:
                await asyncio.to_thread(cloud.unpublish, cls["join_code"])
            except cloud.CloudError as exc:
                website_problems.append(f"{cls['name']}: {exc.message}")
    deleted = await asyncio.to_thread(db.delete_all_data)
    diag.clear_log()
    diag.log(f"All data deleted: {deleted}.")
    return {"deleted": deleted, "website_problems": website_problems}


# ── Online registration (web/ — students register with the join code) ───────

SAME_PERSON = 0.45  # photos of one registration must match each other this well


def _embed_registration(images: list[np.ndarray]) -> tuple[np.ndarray | None, int, str]:
    """Face template from a website registration's photos, or a reason why not."""
    engine = FaceEngine.instance()
    embeddings = []
    for img in images[:5]:
        face = engine.largest_face(img)
        if face is None or float(face.det_score) < 0.55:
            continue
        embeddings.append(np.asarray(face.normed_embedding, dtype=np.float32))
    if not embeddings:
        return None, 0, "No clear face was found in the photos."
    if len(embeddings) > 1:
        worst = min(float(a @ b) for i, a in enumerate(embeddings) for b in embeddings[i + 1:])
        if worst < SAME_PERSON:
            return None, len(embeddings), "The photos do not all show the same person."
    mean = np.mean(np.stack(embeddings), axis=0)
    mean /= np.linalg.norm(mean)
    return mean.astype(np.float32), len(embeddings), ""


def _cloud_error(exc: "cloud.CloudError") -> HTTPException:
    return HTTPException(status_code=502, detail=exc.message)


@app.get("/api/cloud")
async def cloud_settings() -> dict:
    url = cloud.server_url()
    return {"url": url, "configured": bool(url), "default_url": cloud.DEFAULT_URL}


@app.put("/api/cloud")
async def update_cloud_settings(body: CloudSettings) -> dict:
    try:
        url = cloud.set_server_url(body.url)
    except cloud.CloudError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    if url:
        try:  # check the address really is a Presentia site
            await asyncio.to_thread(cloud._request, "GET", "/api/health", None, False)  # noqa: SLF001
        except cloud.CloudError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc
    return {"url": url, "configured": bool(url), "default_url": cloud.DEFAULT_URL}


@app.put("/api/classes/{class_id}/online")
async def set_class_online(class_id: int, body: OnlineToggle) -> dict:
    """Turn online registration on (publish the class on the website) or off
    (remove it, with anything not yet collected)."""
    cls = _require_class(class_id)
    try:
        if body.enabled:
            await asyncio.to_thread(cloud.publish, cls)
            db.set_class_online(class_id, True)
        else:
            if cls["online"]:
                await asyncio.to_thread(cloud.unpublish, cls["join_code"])
            db.set_class_online(class_id, False)
    except cloud.CloudError as exc:
        raise _cloud_error(exc) from exc
    cls = _require_class(class_id)
    return {**cls, "share_link": cloud.share_link(cls["join_code"]) if cls["online"] else ""}


@app.post("/api/classes/{class_id}/sync")
async def sync_class(class_id: int) -> dict:
    """Download new registrations from the website."""
    cls = _require_class(class_id)
    if not cls["online"]:
        return {"received": 0, "pending": cls["pending_count"]}
    if not FaceEngine.is_ready():
        raise HTTPException(status_code=503, detail="AI models still loading")
    try:
        received = await asyncio.to_thread(cloud.pull, cls, _embed_registration)
    except cloud.CloudError as exc:
        raise _cloud_error(exc) from exc
    return {"received": received, "pending": _require_class(class_id)["pending_count"]}


# ── Who a waiting registration is (Google Classroom style) ────────────────────
# One student, many classes: a student already registered on this computer
# (same student number, or same face under another number) is simply ADDED to
# the class with the face data already saved; nothing new is stored. A class
# never lists the same student twice.

def _resolve_pending(p: dict, face: np.ndarray | None, roster: set[int]) -> dict:
    """What accepting this registration would do.

    action: "new"      — a new student (face saved)
            "join"     — an existing student joins this class (saved data reused)
            "in_class" — that student is already in this class; nothing to do
            "conflict" — the number is one student's, the face another's
            "number_taken" — the number is a registered student's, the face
                         is nobody's on file (a different person)
            "no_face"  — new student number and no usable face
    """
    by_number = db.find_student_by_no(p["student_no"])
    by_face = _face_owner(face, p["student_no"]) if face is not None else None
    out: dict = {"action": "new", "match": None, "via": None, "conflict_with": None, "face_differs": False}
    if by_number and by_face:
        out.update(action="conflict", match=_brief(by_number), conflict_with=_brief(by_face))
        return out
    if by_number and face is not None:
        saved = db.get_student_embedding(by_number["id"])
        if saved is not None and float(_unit(saved) @ face) < DUPLICATE_FACE:
            # The number is a registered student's, but this is someone else.
            out.update(action="number_taken", match=_brief(by_number), via="number", face_differs=True)
            return out
    target = by_number or by_face
    if target:
        out.update(
            action="in_class" if target["id"] in roster else "join",
            match=_brief(target), via="number" if by_number else "face",
        )
        return out
    if face is None:
        out["action"] = "no_face"
    return out


def _brief(student: dict) -> dict:
    return {"id": student["id"], "student_no": student["student_no"], "name": student["name"]}


@app.get("/api/classes/{class_id}/pending")
async def class_pending(class_id: int) -> list[dict]:
    _require_class(class_id)
    rows = db.list_pending(class_id)
    roster = {s["id"] for s in db.list_students(class_id)}
    faces = {p["id"]: _unit(np.frombuffer(p["embedding"], dtype=np.float32))
             for p in rows if p["embedding"] is not None}
    out = []
    for p in rows:
        photo = p.pop("photo_jpeg")
        p.pop("embedding")
        p["photo_b64"] = base64.b64encode(photo).decode() if photo else ""
        p["has_face"] = bool(p["has_face"])
        p["existing_in_class"] = bool(p["existing_in_class"])
        face = faces.get(p["id"])
        p.update(_resolve_pending(p, face, roster))
        # Another waiting registration with the same number but not the same
        # face: two different people typed one student number.
        p["number_clash"] = None
        for other in rows:
            if other["id"] == p["id"] or other["student_no"] != p["student_no"]:
                continue
            twin = faces.get(other["id"])
            if face is None or twin is None or float(face @ twin) < DUPLICATE_FACE:
                p["number_clash"] = {"name": other["name"], "submitted_at": other["submitted_at"]}
                break
        # Two NEW people with one face, both waiting: accept only one of them.
        p["pending_match"] = None
        if face is not None and p["action"] == "new":
            for other in rows:
                twin = faces.get(other["id"])
                if (other["id"] != p["id"] and twin is not None
                        and other["student_no"] != p["student_no"]
                        and float(face @ twin) >= DUPLICATE_FACE):
                    p["pending_match"] = {"student_no": other["student_no"], "name": other["name"]}
                    break
        out.append(p)
    return out


@app.post("/api/pending/{pending_id}/approve")
async def approve_pending(pending_id: int) -> dict:
    """Accept a website registration into its class.

    A student already registered on this computer (same number, or same
    face) is added to the class with the face data already saved; nobody is
    stored twice, and nobody is listed twice in one class.
    """
    p = db.get_pending(pending_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    face = _unit(np.frombuffer(p["embedding"], dtype=np.float32)) if p["embedding"] is not None else None
    roster = {s["id"] for s in db.list_students(p["class_id"])}
    r = _resolve_pending(p, face, roster)
    m = r["match"]
    if r["action"] == "conflict":
        c = r["conflict_with"]
        raise HTTPException(status_code=409, detail={
            "code": "conflict",
            "message": (f"Student number {m['student_no']} belongs to {m['name']}, but this face is "
                        f"{c['name']} ({c['student_no']}). Reject it and check with the student."),
        })
    if r["action"] == "number_taken":
        raise HTTPException(status_code=409, detail={
            "code": "number_taken",
            "message": (f"Student number {m['student_no']} belongs to {m['name']}, and this is a "
                        "different person. Reject it and ask them to check their student number."),
        })
    if r["action"] == "in_class":
        raise HTTPException(status_code=409, detail={
            "code": "already_in_class",
            "message": f"{m['name']} ({m['student_no']}) is already in this class.",
        })
    if r["action"] == "join":
        db.add_student_to_class(p["class_id"], m["id"])
        db.delete_pending(pending_id)
        return {"result": "linked", "student_id": m["id"], "name": m["name"], "via": r["via"]}
    if r["action"] == "no_face":
        raise HTTPException(
            status_code=422,
            detail=p["problem"] or "No usable face in this registration. Reject it and ask the "
                                   "student to register again.",
        )
    student_id = db.add_student(p["student_no"], p["name"], face, p["class_id"])
    db.delete_pending(pending_id)
    return {"result": "added", "student_id": student_id, "name": p["name"]}


@app.delete("/api/pending/{pending_id}", status_code=204)
async def reject_pending(pending_id: int) -> None:
    db.delete_pending(pending_id)


@app.get("/api/classes/{class_id}/available-students")
async def available_students(class_id: int) -> list[dict]:
    """Students from the instructor's other classes who can be added here
    without registering their face again."""
    _require_class(class_id)
    return db.list_students_outside(class_id)


@app.get("/api/classes/{class_id}/students/summary")
async def class_student_summary(class_id: int) -> list[dict]:
    """Students page: each student's present / late / absent totals."""
    _require_class(class_id)
    return db.class_attendance_summary(class_id)


@app.get("/api/classes/{class_id}/students/{student_id}/history")
async def class_student_history(class_id: int, student_id: int) -> list[dict]:
    _require_class(class_id)
    return db.student_history(class_id, student_id)


@app.get("/api/classes/{class_id}/export.xlsx")
async def export_class_xlsx(class_id: int) -> Response:
    """Excel workbook: Summary, Attendance (one column per session) and
    Class Info sheets."""
    from urllib.parse import quote

    from app.data.export import class_workbook

    result = class_workbook(class_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Class not found")
    filename, data = result
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@app.put("/api/classes/{class_id}/students/{student_id}", status_code=204)
async def add_student_to_class(class_id: int, student_id: int) -> None:
    _require_class(class_id)
    if db.get_student_embedding(student_id) is None:
        raise HTTPException(status_code=404, detail="Student not found")
    db.add_student_to_class(class_id, student_id)


@app.delete("/api/classes/{class_id}/students/{student_id}")
async def remove_student_from_class(class_id: int, student_id: int) -> dict:
    """Remove from this roster. Face data is deleted only when the student is
    in no other class (`deleted` tells which happened)."""
    _require_class(class_id)
    return {"deleted": db.remove_student_from_class(class_id, student_id)}


# ── Students ──────────────────────────────────────────────────────────────────

@app.get("/api/students")
async def list_students(class_id: int | None = None) -> list[dict]:
    return db.list_students(class_id)


def _student_exists_error(existing: dict) -> HTTPException:
    """409 that lets the UI offer to reuse the student already on file."""
    return HTTPException(
        status_code=409,
        detail={
            "code": "student_exists",
            "message": (
                f"Student number {existing['student_no']} is already registered "
                f"as {existing['name']}."
            ),
            "student": existing,
        },
    )


# ── One face, one student ─────────────────────────────────────────────────────
# A face that matches a registered student this closely would also be taken
# for that student during attendance, so it cannot be a second student. This
# stops one person registering under several student numbers.
DUPLICATE_FACE = MATCH_THRESHOLD


def _unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _face_owner(embedding: np.ndarray, student_no: str) -> dict | None:
    """The registered student (under a DIFFERENT student number) whose face
    this is, or None. A match under the same number is the same student."""
    size = np.asarray(embedding).size
    known = [(sid, e) for sid, e in db.all_embeddings() if e.size == size]
    if not known:
        return None
    ids = [sid for sid, _ in known]
    mat = np.stack([_unit(e) for _, e in known])
    scores = mat @ _unit(embedding)
    for i in np.argsort(-scores):
        if scores[i] < DUPLICATE_FACE:
            break
        student = db.get_student(ids[i])
        if student and student["student_no"] != student_no:
            return {**student, "score": round(float(scores[i]), 3)}
    return None


def _face_exists_error(owner: dict) -> HTTPException:
    """409 for a face that is already registered under another number. The
    UI can offer to add that student to the class instead."""
    return HTTPException(
        status_code=409,
        detail={
            "code": "face_exists",
            "message": (
                f"This face is already registered as {owner['name']} "
                f"({owner['student_no']}). One person can only be registered once."
            ),
            "student": {k: owner[k] for k in ("id", "student_no", "name", "created_at")},
        },
    )


@app.post("/api/students", status_code=201)
async def create_student(body: StudentCreate) -> dict:
    if body.class_id is not None:
        _require_class(body.class_id)
    existing = db.find_student_by_no(body.student_no)
    if existing is not None:
        raise _student_exists_error(existing)
    try:
        raw = base64.b64decode(body.embedding_b64)
        embedding = np.frombuffer(raw, dtype=np.float32)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    owner = _face_owner(embedding, body.student_no)
    if owner is not None:
        raise _face_exists_error(owner)
    try:
        student_id = db.add_student(body.student_no, body.name, embedding, body.class_id)
        return {"id": student_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/students/{student_id}")
async def update_student(student_id: int, body: StudentUpdate) -> dict:
    """Fix a typo in a student's number or name (face data is unchanged)."""
    if db.get_student_embedding(student_id) is None:
        raise HTTPException(status_code=404, detail="Student not found")
    student_no = body.student_no.strip() if body.student_no is not None else None
    name = body.name.strip() if body.name is not None else None
    if student_no == "" or name == "":
        raise HTTPException(status_code=400, detail="Student number and name cannot be empty")
    if student_no is not None:
        existing = db.find_student_by_no(student_no)
        if existing is not None and existing["id"] != student_id:
            raise _student_exists_error(existing)
    db.update_student(student_id, student_no=student_no, name=name)
    return {"id": student_id}


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
    class_id: int | None = Body(None),
) -> dict:
    """Accept 1-5 image files, extract face embeddings, average and save."""
    if class_id is not None:
        _require_class(class_id)
    existing = db.find_student_by_no(student_no)
    if existing is not None:
        raise _student_exists_error(existing)
    mean, count = await _mean_embedding_from_uploads(files)
    owner = _face_owner(mean, student_no)
    if owner is not None:
        raise _face_exists_error(owner)
    try:
        student_id = db.add_student(student_no, name, mean, class_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"id": student_id, "samples": count}


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions(class_id: int | None = None) -> list[dict]:
    return db.list_sessions(class_id)


@app.post("/api/sessions", status_code=201)
async def create_session(body: SessionCreate) -> dict:
    if body.class_id is not None:
        _require_class(body.class_id)
    session_id = db.create_session(body.name, body.class_id)
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
    prof = perf.profile_params()  # fixed for this camera session

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
        face = FaceEngine.instance().largest_face(frame, "camera")
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
            emb = FaceEngine.instance().embed_largest(frame, "camera")
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
                emb = FaceEngine.instance().embed_largest(frame, "camera")
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
        preview_gap = 1.0 / prof["preview_fps"]
        last = 0.0
        next_preview = 0.0
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
                # Analysis sees every camera frame (blinks are short); the
                # preview is sent at the profile's rate.
                to_analyse.put(frame)
                # Evenly thin the camera's frames down to the profile's rate
                # (e.g. 4 of every 5 frames for 24 fps from a 30 fps camera).
                now = time.monotonic()
                if now >= next_preview - 0.004:
                    next_preview = max(next_preview + preview_gap, now - preview_gap)
                    frames.publish(_frame_to_jpeg(frame, prof["jpeg_q"]))
                frame = None
                gap = time.monotonic() - last
                if gap < min_gap:
                    time.sleep(min_gap - gap)
                last = time.monotonic()
        finally:
            cap.release()
            to_analyse.put(None)

    def _analysis_thread() -> None:
        n = 0
        while not stop_event.is_set():
            frame = to_analyse.take()
            if frame is None:
                continue
            # Low profile: continuous presence monitoring looks at every other
            # frame; challenges (liveness, enrolment) always see every frame.
            n += 1
            if mode == "monitor" and prof["monitor_every"] > 1 and n % prof["monitor_every"]:
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
                        checks = perf.get_settings()
                        spoof = None
                        if checks.get("antispoof"):
                            from app.core.antispoof import SpoofVote

                            spoof = SpoofVote(every=3 if prof["preview_fps"] >= 20 else 2)
                        liveness = LivenessChecker(
                            tracker, directional=msg.get("directional", True),
                            randomized=bool(checks.get("random_challenges", True)),
                            spoof=spoof,
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
      {"action": "start", "region": {left,top,width,height}, "class_id": int,
       "name": str, "missing_after": float}
      {"action": "stop"}
      {"action": "enroll_unknown", "index": int, "student_no": str, "name": str}
      {"action": "verify", "student_id": int}          # quick face re-check
      {"action": "challenge", "student_id": int}       # random action check
      {"action": "challenge_cancel"}

    Server sends:
      {"type": "frame",  "jpeg": str, "matches": [...], "unknowns": [...]}
      {"type": "roster", "students": [...]}
      {"type": "alert",  "message": str, "level": str}
      {"type": "challenge_started", "student_id", "name", "instructions", "chat_text"}
      {"type": "challenge", ...progress, "result": null | "passed" | "failed"}
      {"type": "challenge_cancelled", "student_id": int}
      {"type": "error",  "message": str}
    """
    await websocket.accept()

    from app.core.face_engine import FaceEngine
    from app.core.roster_monitor import RosterMonitor
    from app.core.stillness import StillnessWatch
    from app.core.tile_challenge import TileChallenge
    from app.core.tile_tracker import TileTracker
    from app.data import db

    stop_event = threading.Event()
    loop = asyncio.get_event_loop()
    result_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    session_id: int | None = None
    class_id: int | None = None
    roster_monitor: RosterMonitor | None = None
    tracker: TileTracker | None = None
    embeddings: list[tuple[int, np.ndarray]] = []
    names: dict[int, str] = {}
    unknowns_cache: list[dict] = []
    unknown_registry: dict[int, dict] = {}
    verify_student: int | None = None
    verify_deadline = 0.0
    # Random action check on one tile (at most one at a time) and the
    # still-tile watch that suggests one.
    challenge: TileChallenge | None = None
    challenge_on = threading.Event()
    to_challenge = _LatestFrame()
    stillness = StillnessWatch()
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
    prof = perf.profile_params()  # fixed for this monitoring session
    PREVIEW_FPS = prof["preview_fps"]
    PREVIEW_MAX_W = prof["preview_max_w"]
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
                    if challenge_on.is_set():
                        # Every preview frame: a blink is over in ~0.2 s.
                        to_challenge.put(frame)

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
                    ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, prof["jpeg_q"]])
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

                with state_lock:
                    ch = challenge
                    sw = stillness
                if ch is not None:
                    hit = next((m for m in matches if m[0] == ch.student_id), None)
                    if hit is not None:
                        ch.note_identified(hit[2])
                for sid in sw.update(frame, matches):
                    if ch is not None and ch.student_id == sid:
                        continue  # already being checked
                    msg_txt = (
                        f"{nms.get(sid, 'A student')}'s video has barely changed for "
                        f"{int(sw.still_after)} seconds — it may be a photo or a frozen feed. "
                        "Click their name to run a liveness check."
                    )
                    s_id = session_id
                    if s_id is not None:
                        db.log_event(s_id, sid, "suspected_still", msg_txt)
                    _push({"type": "alert", "message": msg_txt, "level": "warn"})
                sw.keep_only({m[0] for m in matches})

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
                suspects = sw.flagged()
                checking = ch.student_id if ch is not None else None
                for r in roster:
                    r["suspect"] = r["id"] in suspects
                    r["checking"] = r["id"] == checking
                _live.stats(roster, len(ulist))
                sig = (tuple((r["id"], r["state"], r["suspect"], r["checking"]) for r in roster),
                       tuple(sorted(live)))
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

    def _end_challenge(ch: TileChallenge) -> None:
        """Detach `ch` if it is still the current check, and free FaceMesh."""
        nonlocal challenge
        with state_lock:
            if challenge is ch:
                challenge = None
                challenge_on.clear()
        ch.close()

    def _challenge_thread(stop: threading.Event) -> None:
        last_sig = None
        last_push = 0.0
        try:
            while not stop.is_set() and not stop_event.is_set():
                if not challenge_on.wait(0.5):
                    continue
                frame = to_challenge.take(timeout=0.5)
                if frame is None:
                    continue
                with state_lock:
                    ch = challenge
                if ch is None:
                    continue
                st = ch.process(frame)
                now = time.monotonic()
                sig = (st["prompt"], st["step"], st["face_found"], st["small_face"],
                       st["result"], st["seconds_left"])
                if sig != last_sig or now - last_push >= 0.5:
                    last_sig, last_push = sig, now
                    _push({"type": "challenge", **st})
                if st["result"] is None:
                    continue
                ok = st["result"] == "passed"
                s_id = session_id
                if s_id is not None:
                    db.log_event(s_id, ch.student_id,
                                 "liveness_passed" if ok else "liveness_failed", ch.reason)
                if ok:
                    with state_lock:
                        stillness.clear(ch.student_id)
                _push({"type": "alert", "message": ch.reason, "level": "ok" if ok else "error"})
                _end_challenge(ch)
        except Exception as exc:  # noqa: BLE001
            _push({"type": "error", "message": f"Liveness check stopped: {exc}"})
            with state_lock:
                ch = challenge
            if ch is not None:
                _end_challenge(ch)

    def _cancel_challenge() -> int | None:
        with state_lock:
            ch = challenge
        if ch is None:
            return None
        _end_challenge(ch)
        return ch.student_id

    cap_thread: threading.Thread | None = None
    run_stop = threading.Event()

    async def _say(data: dict) -> bool:
        async with send_lock:
            return await _send_json(websocket, data)

    async def _recv_loop() -> None:
        nonlocal session_id, class_id, roster_monitor, tracker, embeddings, names, cap_thread
        nonlocal verify_student, verify_deadline, overlay, run_stop, challenge, stillness
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
                    # Only this class's roster is matched. Older frontends
                    # send no class_id and get every student, as before.
                    raw_cid = msg.get("class_id")
                    cid = int(raw_cid) if raw_cid is not None else None
                    if cid is not None and db.get_class(cid) is None:
                        await _say({"type": "error", "message": "That class no longer exists."})
                        continue

                    sid = db.create_session(session_name, cid)
                    roster = db.list_students(cid)
                    embs = db.all_embeddings(cid)
                    nms_map = {s["id"]: s["name"] for s in roster}
                    engine = FaceEngine.instance()
                    t = TileTracker(engine, lambda: embeddings)
                    rm = RosterMonitor(roster, _roster_event, missing_after=missing_after)

                    _cancel_challenge()
                    with state_lock:
                        session_id = sid
                        class_id = cid
                        embeddings = embs
                        names = nms_map
                        tracker = t
                        roster_monitor = rm
                        stillness = StillnessWatch()

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
                    threading.Thread(target=_challenge_thread, args=(run_stop,),
                                     daemon=True).start()
                    _live.start(session_name or "Meet session")
                    _live.stats(rm.status(), 0)
                    await _say({"type": "started", "session_id": sid})

                elif action == "stop":
                    run_stop.set()
                    _cancel_challenge()
                    _live.stop()
                    s_id = session_id
                    if s_id is not None:
                        db.end_session(s_id)
                    with state_lock:
                        session_id = None
                        tracker = None
                        roster_monitor = None
                    await _say({"type": "stopped"})

                elif action == "challenge":
                    raw_id = msg.get("student_id")
                    with state_lock:
                        active = session_id is not None and tracker is not None
                        name = names.get(int(raw_id)) if raw_id is not None else None
                    if not active:
                        await _say({"type": "error",
                                    "message": "Start monitoring before running a liveness check."})
                        continue
                    if name is None:
                        await _say({"type": "error",
                                    "message": "That student is not on this class roster."})
                        continue
                    _cancel_challenge()  # one check at a time
                    try:
                        # Loading FaceMesh takes a moment; keep the socket responsive.
                        ch = await asyncio.to_thread(TileChallenge, int(raw_id), name)
                    except Exception as exc:  # noqa: BLE001
                        await _say({"type": "error",
                                    "message": f"Could not start the liveness check: {exc}"})
                        continue
                    with state_lock:
                        challenge = ch
                        challenge_on.set()
                    await _say({
                        "type": "challenge_started", "student_id": ch.student_id,
                        "name": name, "instructions": ch.instructions(),
                        "chat_text": ch.chat_text(),
                    })

                elif action == "challenge_cancel":
                    sid_cancelled = _cancel_challenge()
                    if sid_cancelled is not None:
                        await _say({"type": "challenge_cancelled", "student_id": sid_cancelled})

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
                    existing = db.find_student_by_no(msg["student_no"])
                    if existing is not None:
                        await _say({
                            "type": "error",
                            "message": f"Student number {existing['student_no']} is "
                                       f"already registered as {existing['name']}.",
                        })
                        continue
                    try:
                        new_id = db.add_student(msg["student_no"], msg["name"], emb, class_id)
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
            _cancel_challenge()
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

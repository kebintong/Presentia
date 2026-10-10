"""Server mode: teachers use Presentia from their own browser.

This computer becomes the server. A separate, small web server runs on
127.0.0.1:7790 (the "gateway") and is the ONLY thing to put on the internet,
for example with Cloudflare Tunnel:

    cloudflared tunnel --url http://localhost:7790

The engine itself (127.0.0.1:7788) has no sign-in and must never be exposed.
The gateway only offers:

  /login, /logout            teacher sign-in (accounts made in Settings → Server mode)
  /app                       the teacher's page: share the meeting tab, watch the roster
  /api/me                    who is signed in and which classes they may monitor
  /ws/feed                   pictures of the teacher's shared tab (from their browser)
  /ws/monitor                monitoring of one of their classes (app.sidecar.run_monitor)

Every request needs a signed-in teacher (HttpOnly, SameSite=Strict cookie),
WebSockets must come from the gateway's own page (Origin check), wrong
passwords are rate limited, and a teacher can only monitor the classes they
were given and only their own shared tab.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core import diag
from app.core.passwords import DUMMY_HASH, verify_password
from app.core.tab_feed import TabFeed
from app.data import db

GATEWAY_PORT = 7790
COOKIE = "presentia_session"
SESSION_TTL = 12 * 3600          # s: signed in for a school day
MAX_RUNS = 3                     # classes monitored at once through the gateway (this PC's limit)
FAIL_WINDOW = 600.0              # s
FAIL_LIMIT = 5                   # wrong passwords per window, per address and per username


@dataclass
class _Session:
    token: str
    teacher_id: int
    username: str
    name: str
    expires: float
    feed: TabFeed = field(default_factory=TabFeed)


_lock = threading.Lock()
_sessions: dict[str, _Session] = {}
_failures: dict[str, list[float]] = {}
_runs = 0


def reset_state() -> None:
    """Sign everyone out (server mode turned off, or tests)."""
    global _runs
    with _lock:
        for s in _sessions.values():
            s.feed.stop()
        _sessions.clear()
        _failures.clear()
        _runs = 0


def active_runs() -> int:
    return _runs


def signed_in() -> int:
    now = time.time()
    with _lock:
        return sum(1 for s in _sessions.values() if s.expires > now)


# ── helpers ──────────────────────────────────────────────────────────────────

def _host(conn) -> str:
    return conn.headers.get("host", "")


def _is_local(host: str) -> bool:
    name = host.split(":")[0]
    return name in ("localhost", "127.0.0.1")


def _client(conn) -> str:
    # Through Cloudflare Tunnel every request comes from 127.0.0.1; the
    # visitor's own address is in this header.
    ip = conn.headers.get("cf-connecting-ip")
    return ip or (conn.client.host if conn.client else "?")


def _same_origin(conn) -> bool:
    origin = conn.headers.get("origin")
    host = _host(conn)
    return bool(origin) and origin in (f"https://{host}", f"http://{host}")


def _session(conn) -> _Session | None:
    token = conn.cookies.get(COOKIE)
    if not token:
        return None
    now = time.time()
    with _lock:
        s = _sessions.get(token)
        if s is None:
            return None
        if s.expires <= now:
            _sessions.pop(token, None)
            s.feed.stop()
            return None
    # The account may have been disabled or deleted meanwhile.
    t = db.get_teacher(s.teacher_id)
    if t is None or t["disabled"]:
        with _lock:
            _sessions.pop(token, None)
        s.feed.stop()
        return None
    s.name = t["name"]
    return s


def _classes_of(teacher_id: int) -> list[dict]:
    t = db.get_teacher(teacher_id)
    if t is None:
        return []
    out = []
    for cid in t["class_ids"]:
        c = db.get_class(cid)
        if c is not None:
            out.append({"id": c["id"], "name": c["name"], "section": c.get("section") or "",
                        "students": c.get("student_count", 0)})
    return out


def _too_many_failures(*keys: str) -> bool:
    now = time.time()
    with _lock:
        for k in keys:
            recent = [t for t in _failures.get(k, []) if now - t < FAIL_WINDOW]
            _failures[k] = recent
            if len(recent) >= FAIL_LIMIT:
                return True
    return False


def _note_failure(*keys: str) -> None:
    now = time.time()
    with _lock:
        if len(_failures) > 5000:   # many different names tried: forget old entries
            for k in [k for k, v in _failures.items() if not v or now - v[-1] >= FAIL_WINDOW]:
                del _failures[k]
        for k in keys:
            _failures.setdefault(k, []).append(now)


def _csp(request: Request) -> str:
    host = _host(request)
    return ("default-src 'none'; script-src 'unsafe-inline' blob:; worker-src blob:; "
            f"connect-src 'self' wss://{host} ws://{host}; style-src 'unsafe-inline'; "
            "img-src data: blob:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


gateway = FastAPI(title="Presentia server mode", docs_url=None, redoc_url=None, openapi_url=None)


@gateway.middleware("http")
async def _headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # same-origin (not no-referrer): with no-referrer browsers send "Origin: null"
    # on the sign-in form, and the Origin check would refuse it.
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers.setdefault("Content-Security-Policy", _csp(request))
    return response


# ── pages ────────────────────────────────────────────────────────────────────

@gateway.get("/")
async def home(request: Request) -> Response:
    return RedirectResponse("/app" if _session(request) else "/login", status_code=303)


@gateway.get("/login")
async def login_page(request: Request) -> Response:
    from app.core.teacher_page import login_html

    if _session(request):
        return RedirectResponse("/app", status_code=303)
    return HTMLResponse(login_html())


@gateway.post("/login")
async def login(request: Request) -> Response:
    from app.core.teacher_page import login_html

    origin = request.headers.get("origin")
    if origin and not _same_origin(request):
        return HTMLResponse(login_html("Please sign in from this page."), status_code=403)
    form = await request.form()
    username = str(form.get("username", "")).strip().lower()[:64]
    password = str(form.get("password", ""))[:256]
    where = _client(request)
    if _too_many_failures(f"ip:{where}", f"user:{username}"):
        return HTMLResponse(login_html("Too many wrong tries. Wait 10 minutes and try again."), status_code=429)
    row = db.teacher_login_row(username) if username else None
    # Check a password even for unknown usernames, so both take as long.
    ok = await asyncio.to_thread(verify_password, password, row["pw_hash"] if row else DUMMY_HASH)
    if not ok or row is None or row["disabled"]:
        _note_failure(f"ip:{where}", f"user:{username}")
        diag.log(f"Server mode: failed sign-in for {username!r} from {where}", "warning")
        return HTMLResponse(login_html("Wrong username or password."), status_code=401)
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = _Session(token, row["id"], row["username"], row["name"], time.time() + SESSION_TTL)
    db.touch_teacher_login(row["id"])
    diag.log(f"Server mode: {row['username']} signed in from {where}")
    response = RedirectResponse("/app", status_code=303)
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="strict",
                        secure=not _is_local(_host(request)), path="/")
    return response


@gateway.post("/logout")
async def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token and _same_origin(request):
        with _lock:
            s = _sessions.pop(token, None)
        if s is not None:
            s.feed.stop()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response


@gateway.get("/app")
async def app_page(request: Request) -> Response:
    from app.core.teacher_page import app_html

    if _session(request) is None:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(app_html())


@gateway.get("/api/me")
async def me(request: Request) -> Response:
    s = _session(request)
    if s is None:
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    return JSONResponse({"name": s.name, "username": s.username, "classes": _classes_of(s.teacher_id),
                         "feed": s.feed.status()})


# ── live connections ─────────────────────────────────────────────────────────

async def _refuse(websocket: WebSocket, code: int = 4401) -> None:
    await websocket.close(code=code)


@gateway.websocket("/ws/feed")
async def ws_feed(websocket: WebSocket) -> None:
    """The teacher's shared tab: binary JPEG pictures, {"type": "hello"} and {"type": "ended"}."""
    s = _session(websocket)
    if s is None or not _same_origin(websocket):
        await _refuse(websocket)
        return
    await websocket.accept()
    feed = s.feed
    cid = feed.connect()

    async def watch_stop() -> None:
        while True:
            await asyncio.sleep(0.5)
            if feed.should_stop(cid):
                try:
                    await websocket.send_text('{"type": "stop"}')
                except Exception:  # noqa: BLE001
                    pass
                return

    watcher = asyncio.create_task(watch_stop())
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                if len(data) > 4_000_000 or not await asyncio.to_thread(feed.push, cid, data):
                    break
                continue
            try:
                m = json.loads(msg.get("text") or "")
            except ValueError:
                continue
            if m.get("type") == "hello":
                feed.hello(cid, str(m.get("label") or ""), str(m.get("surface") or ""))
            elif m.get("type") == "ended":
                feed.ended(cid)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        watcher.cancel()
        feed.disconnect(cid)


@gateway.websocket("/ws/monitor")
async def ws_monitor(websocket: WebSocket) -> None:
    """Monitoring one of the teacher's classes (same messages as the engine's /ws/screen)."""
    global _runs
    s = _session(websocket)
    if s is None or not _same_origin(websocket):
        await _refuse(websocket)
        return
    with _lock:
        busy = _runs >= MAX_RUNS
        if not busy:
            _runs += 1
    if busy:
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"The Presentia server is already monitoring {MAX_RUNS} classes. Try again later."}))
        await websocket.close()
        return
    from app.sidecar import RemoteMonitor, run_monitor

    classes = {c["id"] for c in _classes_of(s.teacher_id)}
    diag.log(f"Server mode: {s.username} connected to monitoring")
    try:
        await run_monitor(websocket, RemoteMonitor(feed=s.feed, classes=classes, teacher=s.name))
    finally:
        with _lock:
            _runs -= 1


@gateway.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def not_found(path: str) -> Response:
    return JSONResponse({"detail": "Not found"}, status_code=404)


# ── turning it on and off (from the engine, Settings → Server mode) ──────────

class GatewayServer:
    def __init__(self) -> None:
        self._server = None
        self._thread: threading.Thread | None = None
        self.error = ""

    @property
    def running(self) -> bool:
        return bool(self._server is not None and self._server.started and self._thread and self._thread.is_alive())

    def start(self) -> None:
        import uvicorn

        if self.running:
            return
        self.error = ""
        config = uvicorn.Config(gateway, host="127.0.0.1", port=GATEWAY_PORT, log_level="warning",
                                lifespan="off", ws_max_size=8 * 1024 * 1024)
        server = uvicorn.Server(config)

        def _run() -> None:
            try:
                server.run()
            except BaseException as exc:  # noqa: BLE001 - e.g. the port is taken
                self.error = str(exc) or exc.__class__.__name__
                diag.log(f"Server mode: the gateway stopped: {exc!r}", "error")

        self._server = server
        self._thread = threading.Thread(target=_run, name="presentia-gateway", daemon=True)
        self._thread.start()
        for _ in range(50):
            if server.started or not self._thread.is_alive():
                break
            time.sleep(0.1)
        if not server.started and not self.error:
            self.error = f"Port {GATEWAY_PORT} could not be opened."
        if server.started:
            diag.log(f"Server mode: gateway listening on 127.0.0.1:{GATEWAY_PORT}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        reset_state()

    def status(self) -> dict:
        return {"running": self.running, "port": GATEWAY_PORT, "error": self.error,
                "signed_in": signed_in(), "monitoring": active_runs(), "max_classes": MAX_RUNS,
                "local_url": f"http://localhost:{GATEWAY_PORT}",
                "tunnel_command": f"cloudflared tunnel --url http://localhost:{GATEWAY_PORT}"}


SERVER = GatewayServer()

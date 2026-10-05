"""Online registration: talk to the Presentia website (web/ in this repo).

Students register on the website with a class's join code. This module lets
the desktop app publish a class there, download what students submitted,
and delete it from the website once it is saved locally. Face templates are
only ever computed here, on the instructor's computer.

HTTPS uses truststore and certifi (see TRUST_OPTIONS); everything else is
the standard library.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable

import cv2
import numpy as np

from app.core import diag
from app.data import db

# The website address. Set this to your deployed site (see web/README.md) so
# every install uses it without configuration; it can also be changed in the
# app (Students page → Online registration) or with PRESENTIA_CLOUD_URL.
DEFAULT_URL = "https://presentia.venki050524.workers.dev"

# Where diagnostic reports go. Deliberately NOT the registration website
# address above: that one can be changed in the app (or point at a school's
# own copy of the site), and reports must still reach the Presentia team.
# Change it here (or with PRESENTIA_REPORTS_URL) if reports move elsewhere.
REPORTS_URL = "https://presentia.venki050524.workers.dev"

TIMEOUT = 20  # seconds per request
PAGE = 10     # registrations downloaded per request
THUMB = 160   # px, the picture kept for the instructor to compare

_K_URL = "cloud_url"
_K_HOST_URL = "cloud_host_url"   # the site the credentials below belong to
_K_HOST_ID = "cloud_host_id"
_K_SECRET = "cloud_host_secret"


class CloudError(Exception):
    """A request to the website failed; `message` is safe to show."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ── settings ─────────────────────────────────────────────────────────────

def server_url() -> str:
    url = os.environ.get("PRESENTIA_CLOUD_URL") or db.get_setting(_K_URL) or DEFAULT_URL
    return url.strip().rstrip("/")


def set_server_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url:
        local = url.startswith(("http://127.0.0.1", "http://localhost"))
        if not (url.startswith("https://") or local):
            raise CloudError("The website address must start with https://")
    db.set_setting(_K_URL, url or None)
    return server_url()


def reports_url() -> str:
    return (os.environ.get("PRESENTIA_REPORTS_URL") or REPORTS_URL).strip().rstrip("/")


def share_link(code: str) -> str:
    base = server_url()
    return f"{base}/?code={code}" if base else ""


# ── HTTP ─────────────────────────────────────────────────────────────────

# HTTPS certificates are checked in this order. The first one that works is
# remembered. Verification is never switched off.
#
#  1. Windows' own check (truststore), the same one the browser uses. It
#     trusts certificates added by antivirus or a school network, and Windows
#     downloads missing root certificates on demand.
#  2. Presentia's built-in list (certifi, Mozilla's root list), for computers
#     where Windows cannot update its root certificates (common on managed
#     school laptops). Only tried when (1) rejects the certificate.
#  3. Python's own check, as a last resort.

def _system_ctx() -> ssl.SSLContext | None:
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        return None


def _bundled_ctx() -> ssl.SSLContext | None:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return None


def _python_ctx() -> ssl.SSLContext | None:
    return ssl.create_default_context()


TRUST_OPTIONS: list[tuple[str, Callable[[], ssl.SSLContext | None]]] = [
    ("Windows certificate check", _system_ctx),
    ("Presentia's built-in certificate list", _bundled_ctx),
    ("Python's certificate check", _python_ctx),
]
_ctx_cache: dict[str, ssl.SSLContext | None] = {}
_working: str | None = None   # the option that last succeeded


def _ctx(name: str) -> ssl.SSLContext | None:
    if name not in _ctx_cache:
        _ctx_cache[name] = dict(TRUST_OPTIONS)[name]()
    return _ctx_cache[name]


def trust_in_use() -> str | None:
    return _working


def _is_cert_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _urlopen(req: urllib.request.Request, timeout: float = TIMEOUT):
    """urlopen, trying the next certificate check only when the previous one
    rejected the site's certificate (nothing has been sent at that point, so
    retrying is safe even for POST)."""
    global _working
    names = [n for n, _ in TRUST_OPTIONS]
    if _working in names:
        names.remove(_working)
        names.insert(0, _working)
    if not req.full_url.startswith("https://"):
        return urllib.request.urlopen(req, timeout=timeout)
    last: BaseException | None = None
    for name in names:
        ctx = _ctx(name)
        if ctx is None:
            continue
        try:
            res = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.HTTPError:
            _working = name     # the connection worked; the site answered with an error
            raise
        except (urllib.error.URLError, OSError) as exc:
            if not _is_cert_error(exc):
                raise
            diag.record("cloud", f"{name} rejected the website's certificate: {getattr(exc, 'reason', exc)}",
                        "warning")
            last = exc
            continue
        if _working != name:
            if _working is not None or name != names[0]:
                diag.record("cloud", f"HTTPS now verified with {name}.", "warning")
            _working = name
        return res
    assert last is not None
    raise last


def _why(exc: BaseException) -> str:
    """Short reason a request never got an answer, for the error message."""
    reason = getattr(exc, "reason", exc)
    text = str(reason)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return "the website's security certificate could not be checked"
    if isinstance(reason, ssl.SSLError):
        return "a secure connection could not be made"
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text:
        return "the connection timed out"
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in text:
        return "the address could not be looked up (DNS)"
    if isinstance(reason, ConnectionRefusedError) or "refused" in text.lower():
        return "the connection was refused"
    return text[:120] or type(reason).__name__


def _request(
    method: str, path: str, body: dict | None = None, auth: bool = True,
    base: str | None = None, what: str = "the registration website",
) -> dict:
    """JSON request to the registration website, or to `base` (the report
    service) when given. `what` names the site in error messages."""
    base = base or server_url()
    if not base:
        raise CloudError("Set the registration website address first.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Presentia-Desktop")
    req.add_header("X-Presentia-App", "desktop")
    if auth:
        host_id, secret = _credentials()
        req.add_header("Authorization", f"Bearer {host_id}.{secret}")
    try:
        with _urlopen(req) as res:
            raw = res.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            info = json.loads(exc.read() or b"{}")
        except ValueError:
            info = {}
        if exc.code == 401 and auth:
            # The website no longer knows this install (e.g. its database was
            # reset): forget the credentials so the next call registers again.
            db.set_setting(_K_HOST_ID, None)
            db.set_setting(_K_SECRET, None)
        raise CloudError(
            info.get("message") or f"{what[0].upper() + what[1:]} answered with an error ({exc.code}).",
            exc.code, info.get("error", ""),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[cloud] {method} {path} failed: {exc!r}", file=sys.stderr, flush=True)
        diag.record("cloud", f"{method} {path}: {exc!r}")
        hint = ("Check the internet connection and the website address. Settings → Diagnostics "
                "shows more." if what == "the registration website" else "Check the internet connection, or copy "
                "or save the report instead.")
        raise CloudError(f"Could not reach {what} ({_why(exc)}). {hint}") from exc


def _credentials() -> tuple[str, str]:
    """This install's identity on the website, created on first use."""
    host_id = db.get_setting(_K_HOST_ID)
    secret = db.get_setting(_K_SECRET)
    if host_id and secret and db.get_setting(_K_HOST_URL) == server_url():
        return host_id, secret
    out = _request("POST", "/api/host/register", auth=False)
    db.set_setting(_K_HOST_ID, out["host_id"])
    db.set_setting(_K_SECRET, out["secret"])
    db.set_setting(_K_HOST_URL, server_url())
    return out["host_id"], out["secret"]


# ── classes ──────────────────────────────────────────────────────────────

def publish(cls: dict, open_: bool = True) -> str:
    """Make the class available on the website. Returns the join code, which
    changes in the rare case another teacher's class already uses it."""
    code = cls["join_code"]
    for _ in range(5):
        try:
            _request("PUT", f"/api/host/classes/{code}",
                     {"name": cls["name"], "section": cls.get("section", ""), "open": open_})
            return code
        except CloudError as exc:
            if exc.code != "code_taken":
                raise
            code = db.new_join_code()
            db.set_join_code(cls["id"], code)
    raise CloudError("Could not find a free join code. Try again.")


def unpublish(code: str) -> None:
    """Stop accepting registrations; the website deletes anything waiting."""
    _request("DELETE", f"/api/host/classes/{code}")


# ── downloading registrations ────────────────────────────────────────────

def _decode(b64: str) -> np.ndarray | None:
    try:
        arr = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    except ValueError:
        return None
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _thumbnail(img: np.ndarray) -> bytes:
    h, w = img.shape[:2]
    side = min(h, w)
    y, x = (h - side) // 2, (w - side) // 2
    small = cv2.resize(img[y:y + side, x:x + side], (THUMB, THUMB), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else b""


def _local_time(iso: str) -> str:
    """'2026-10-05T08:28:50.439Z' → local '2026-10-05 16:28:50'."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso[:19].replace("T", " ")


# embed(images) → (mean embedding or None, faces used, problem text)
Embedder = Callable[[list[np.ndarray]], tuple[np.ndarray | None, int, str]]


def pull(cls: dict, embed: Embedder) -> int:
    """Download every waiting registration for the class, build its face
    template, store it for the instructor to approve, and delete it from the
    website. Returns how many were received."""
    received = 0
    for _ in range(100):  # pages; a class rarely needs more than a few
        out = _request("GET", f"/api/host/classes/{cls['join_code']}/registrations?limit={PAGE}")
        regs = out.get("registrations", [])
        if not regs:
            break
        for reg in regs:
            images = [img for img in (_decode(p) for p in reg.get("photos", [])) if img is not None]
            emb, used, problem = embed(images) if images else (None, 0, "")
            if not images:
                problem = "The photos could not be read."
            if not reg.get("liveness", {}).get("passed", False):
                problem = (problem + " " if problem else "") + "The live face check was not completed."
            db.add_pending(
                cls["id"], reg["id"], reg["student_no"], reg["name"], emb,
                _thumbnail(images[0]) if images else None, used, problem.strip(),
                _local_time(reg.get("created_at", "")),
            )
            # Saved locally, so the website copy (photos included) can go.
            _request("DELETE", f"/api/host/registrations/{reg['id']}")
            received += 1
        if not out.get("remaining"):
            break
    return received


# ── diagnostic reports ───────────────────────────────────────────────────

REPORT_MAX = 200_000  # characters; the website refuses more


_K_INSTALL = "install_id"


def install_id() -> str:
    """A random ID for this install, so reports from one computer can be told
    apart. It says nothing about the computer or the person."""
    value = db.get_setting(_K_INSTALL)
    if not value:
        import secrets

        value = secrets.token_hex(8)
        db.set_setting(_K_INSTALL, value)
    return value


def send_report(summary: str, text: str, app_version: str) -> dict:
    """Upload a diagnostic report the user chose to send to the Presentia
    team (REPORTS_URL, whatever registration website is set). Returns
    {"id": ...}."""
    return _request(
        "POST", "/api/reports",
        {"summary": summary[:200], "text": text[-REPORT_MAX:], "app_version": app_version[:40],
         "install_id": install_id()},
        auth=False, base=reports_url(), what="the Presentia report service",
    )


# ── diagnostics (Settings → Diagnostics) ─────────────────────────────────

def _cert_summary(der: bytes | None) -> str:
    """Issuer and validity of the certificate the site presented."""
    if not der:
        return ""
    try:
        import tempfile

        pem = ssl.DER_cert_to_PEM_cert(der)
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(pem)
            path = f.name
        try:
            info = ssl._ssl._test_decode_cert(path)  # noqa: SLF001 - CPython helper, diagnostics only
        finally:
            os.unlink(path)
        issuer = ", ".join(v for part in info.get("issuer", ()) for k, v in part if k in ("organizationName", "commonName"))
        return f"issued by {issuer}; valid {info.get('notBefore', '?')} to {info.get('notAfter', '?')}"
    except Exception:  # noqa: BLE001
        return ""


def _timed(fn: Callable[[], str]) -> tuple[str, str, int]:
    """Run one check → (status, detail, milliseconds)."""
    import time

    t = time.perf_counter()
    try:
        detail = fn()
        status = "ok"
    except _Warn as w:
        status, detail = "warn", str(w)
    except Exception as exc:  # noqa: BLE001
        status, detail = "fail", f"{type(exc).__name__}: {exc}"
    return status, detail, round((time.perf_counter() - t) * 1000)


class _Warn(Exception):
    pass


def diagnose(timeout: float = 8.0) -> dict:
    """Check each step of reaching the website separately, so a failure
    points at its cause: address, proxy, DNS, connection, certificates,
    clock, or the website itself."""
    import time
    import urllib.parse
    from email.utils import parsedate_to_datetime

    checks: list[dict] = []
    hints: list[str] = []

    def add(key: str, title: str, fn: Callable[[], str]) -> str:
        status, detail, ms = _timed(fn)
        checks.append({"id": key, "title": title, "status": status, "detail": detail, "ms": ms})
        return status

    base = server_url()
    if not base:
        checks.append({"id": "url", "title": "Website address", "status": "fail",
                       "detail": "No website address is set.", "ms": 0})
        return {"url": "", "checks": checks, "hints": ["Enter the registration website address."],
                "trust_in_use": _working}
    parts = urllib.parse.urlsplit(base)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    checks.append({"id": "url", "title": "Website address", "status": "ok", "detail": base, "ms": 0})

    proxies = urllib.request.getproxies()
    proxy = proxies.get(parts.scheme) or proxies.get("https") or proxies.get("http")
    if proxy and urllib.request.proxy_bypass(host):
        proxy = None   # this address is excluded from the proxy
    checks.append({
        "id": "proxy", "title": "Proxy", "status": "warn" if proxy else "ok",
        "detail": f"Traffic goes through a proxy: {proxy}" if proxy else "No proxy (direct connection)", "ms": 0,
    })

    def dns() -> str:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return ", ".join(sorted({i[4][0] for i in infos})[:4])
    dns_status = add("dns", f"Look up {host}", dns)

    def tcp() -> str:
        with socket.create_connection((host, port), timeout=timeout):
            return f"Connected to port {port}"
    tcp_status = add("tcp", "Open a connection", tcp) if dns_status == "ok" else "skip"

    tls_ok: list[str] = []
    if parts.scheme == "https" and tcp_status == "ok":
        for name, _ in TRUST_OPTIONS:
            def tls(name: str = name) -> str:
                ctx = _ctx(name)
                if ctx is None:
                    raise _Warn("Not available in this build")
                with socket.create_connection((host, port), timeout=timeout) as sock:
                    with ctx.wrap_socket(sock, server_hostname=host) as s:
                        der = s.getpeercert(binary_form=True)
                        summary = _cert_summary(der)
                        tls_ok.append(name)
                        return f"Certificate accepted ({s.version()}){'; ' + summary if summary else ''}"
            add(f"tls:{name}", f"Secure connection: {name}", tls)

    server_time: list[datetime] = []

    def health() -> str:
        req = urllib.request.Request(base + "/api/health", headers={"User-Agent": "Presentia-Desktop"})
        with _urlopen(req, timeout) as res:
            date = res.headers.get("Date")
            if date:
                server_time.append(parsedate_to_datetime(date))
            body = json.loads(res.read() or b"{}")
        if not body.get("ok"):
            raise _Warn("Answered, but this does not look like a Presentia website")
        return f"Presentia website v{body.get('version', '?')} answered (HTTP 200)"
    health_status = add("health", "Presentia website answers", health)

    if server_time:
        skew = (datetime.now().astimezone() - server_time[0]).total_seconds()
        ok = abs(skew) < 300
        checks.append({
            "id": "clock", "title": "Computer clock", "status": "ok" if ok else "fail",
            "detail": ("Correct" if ok else f"{'Ahead' if skew > 0 else 'Behind'} by "
                       f"{abs(skew) / 3600:.1f} hours") + f" (this computer: {datetime.now():%Y-%m-%d %H:%M})",
            "ms": 0,
        })
        if not ok:
            hints.append("This computer's date or time is wrong, which makes security certificates look "
                         "invalid. Set the clock to update automatically (Windows Settings → Time & language).")
    else:
        checks.append({"id": "clock", "title": "Computer clock", "status": "skip",
                       "detail": f"Could not compare (this computer: {datetime.now():%Y-%m-%d %H:%M})", "ms": 0})

    rep = reports_url()
    if rep and rep != base:
        def reports() -> str:
            req = urllib.request.Request(rep + "/api/health", headers={"User-Agent": "Presentia-Desktop"})
            with _urlopen(req, timeout) as res:
                json.loads(res.read() or b"{}")
            return f"{rep} answered"
        add("reports", "Report service answers", reports)
    else:
        checks.append({"id": "reports", "title": "Report service", "status": "ok",
                       "detail": "Same website as registration", "ms": 0})

    host_id = db.get_setting(_K_HOST_ID)
    checks.append({
        "id": "host", "title": "This app's registration with the website", "status": "ok",
        "detail": "Registered" if host_id and db.get_setting(_K_HOST_URL) == base
        else "Not yet (happens the first time online registration is turned on)",
        "ms": 0,
    })

    # What to do about it.
    if dns_status == "fail":
        hints.append("The website's name could not be looked up: there is no internet connection, or the "
                     "network blocks it. Try another network (e.g. a phone hotspot).")
    elif tcp_status == "fail" and (not proxy or health_status != "ok"):
        hints.append("The website could not be reached on this network"
                     + (" (directly or through the proxy)" if proxy else "")
                     + ". A firewall, antivirus or the school network may block it; try another network "
                     "(e.g. a phone hotspot), or allow Presentia in the firewall/antivirus.")
    if parts.scheme == "https" and tcp_status == "ok":
        first = TRUST_OPTIONS[0][0]
        if not tls_ok:
            hints.append("No certificate check accepted the website. If the clock is right, something on "
                         "this network or computer (antivirus web shield, school firewall) is intercepting "
                         "secure connections. Try another network, or turn off HTTPS scanning in the antivirus.")
        elif first not in tls_ok:
            hints.append(f"Windows did not accept the website's certificate, but {tls_ok[0]} did, and "
                         "Presentia now uses that automatically. Running Windows Update may fix it for good.")
    if (health_status == "fail" and not hints and dns_status == "ok"):
        hints.append("The connection works but the website did not answer properly; see the last test. "
                     "Check the website address in Online registration.")
    if health_status == "ok" and not hints:
        hints.append("Everything works: Presentia can reach the registration website.")
    return {"url": base, "checks": checks, "hints": hints, "trust_in_use": _working}

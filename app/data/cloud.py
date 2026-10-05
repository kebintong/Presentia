"""Online registration: talk to the Presentia website (web/ in this repo).

Students register on the website with a class's join code. This module lets
the desktop app publish a class there, download what students submitted,
and delete it from the website once it is saved locally. Face templates are
only ever computed here, on the instructor's computer.

Only the standard library is used, so the frozen sidecar needs nothing new.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable

import cv2
import numpy as np

from app.data import db

# The website address. Set this to your deployed site (see web/README.md) so
# every install uses it without configuration; it can also be changed in the
# app (Students page → Online registration) or with PRESENTIA_CLOUD_URL.
DEFAULT_URL = ""

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


def share_link(code: str) -> str:
    base = server_url()
    return f"{base}/?code={code}" if base else ""


# ── HTTP ─────────────────────────────────────────────────────────────────

def _request(method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
    base = server_url()
    if not base:
        raise CloudError("Set the registration website address first.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Presentia-Desktop")
    if auth:
        host_id, secret = _credentials()
        req.add_header("Authorization", f"Bearer {host_id}.{secret}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
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
            info.get("message") or f"The website answered with an error ({exc.code}).",
            exc.code, info.get("error", ""),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CloudError(
            "Could not reach the registration website. Check the internet connection "
            "and the website address."
        ) from exc


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

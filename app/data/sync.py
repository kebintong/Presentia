"""Phone app sync: share this computer's classes, students, attendance and
waiting registrations with the teacher's Supabase account, where the
Presentia phone app reads and adds to them.

Optional: nothing leaves this computer until the teacher signs in (Settings →
Phone app, with a code sent by email). The database here stays the working
copy, so Presentia works the same offline. Every few seconds, while signed
in, run() uploads what changed here and applies what changed elsewhere
(supabase/migrations/0001_presentia.sql describes the rules: last change
wins, deletes are shared).

Waiting registrations can be accepted or declined on either device and the
first decision wins (claim()). Face templates are only ever made on this
computer; the phone's decisions are carried out here (run(apply=...)).
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core import diag
from app.data import db
from app.data.cloud import _urlopen, _why  # same HTTPS certificate handling

# The Supabase project the phone app uses (Project Settings → API). The
# publishable (anon) key is meant to be inside apps: row-level security
# decides what each teacher can see. Never put the service_role key here.
SUPABASE_URL = "https://xgyzufobwmmlansdbvmm.supabase.co"
SUPABASE_KEY = "sb_publishable_EXwjXnAGh1s1_IsDWdZuHA_vbNMXPbo"

INTERVAL = 5          # seconds between syncs while signed in
_OVERLAP = 60         # seconds re-read on each pull, for changes committed late
_BATCH = 200

_K_EMAIL, _K_USER, _K_REFRESH = "sync_email", "sync_user", "sync_refresh_token"
_K_CURSOR, _K_LAST = "sync_cursor", "sync_last"
_BLOBS = ("embedding", "photo_jpeg")


class SyncError(Exception):
    """`message` is safe to show; `status` is the HTTP status (0: no answer)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def config() -> tuple[str, str]:
    url = os.environ.get("PRESENTIA_SUPABASE_URL") or SUPABASE_URL
    key = os.environ.get("PRESENTIA_SUPABASE_KEY") or SUPABASE_KEY
    return url.strip().rstrip("/"), key.strip()


def signed_in() -> bool:
    return bool(config()[0] and db.get_setting(_K_REFRESH))


_access: dict = {"token": "", "until": 0.0}
_last_error = ""
_lock = threading.Lock()


def status() -> dict:
    return {
        "configured": bool(config()[0]),
        "signed_in": signed_in(),
        "email": db.get_setting(_K_EMAIL) or "",
        "last_sync": db.get_setting(_K_LAST) or "",
        "error": _last_error,
    }


# ── HTTP ─────────────────────────────────────────────────────────────────

def _http(method: str, path: str, body=None, auth: bool = True, prefer: str = ""):
    url, key = config()
    if not url:
        raise SyncError("The phone app is not set up in this copy of Presentia.")
    req = urllib.request.Request(url + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {_token() if auth else key}")
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    try:
        with _urlopen(req) as res:
            raw = res.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        try:
            info = json.loads(exc.read() or b"{}")
        except ValueError:
            info = {}
        text = info.get("msg") or info.get("error_description") or info.get("message") or ""
        raise SyncError(text or f"Presentia's cloud answered with an error ({exc.code}).", exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SyncError(f"Could not reach Presentia's cloud ({_why(exc)}).") from exc


def _save_session(out: dict) -> None:
    _access.update(token=out["access_token"], until=time.time() + int(out.get("expires_in", 3600)) - 60)
    db.set_setting(_K_REFRESH, out["refresh_token"])   # Supabase rotates it on every refresh


def _token() -> str:
    if _access["token"] and time.time() < _access["until"]:
        return _access["token"]
    refresh = db.get_setting(_K_REFRESH)
    if not refresh:
        raise SyncError("Sign in to use the phone app.", 401)
    try:
        out = _http("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh}, auth=False)
    except SyncError as exc:
        if exc.status in (400, 401):   # signed out elsewhere, or the account was removed
            _forget()
            raise SyncError("Signed out of the phone app. Sign in again.", 401) from exc
        raise
    _save_session(out)
    return _access["token"]


# ── signing in ───────────────────────────────────────────────────────────

def send_code(email: str) -> None:
    _http("POST", "/auth/v1/otp", {"email": email.strip(), "create_user": True}, auth=False)


def verify(email: str, code: str) -> dict:
    """Sign in with the emailed code, or with the sign-in link pasted whole
    (sent when the project's email template still has a link)."""
    link = urllib.parse.parse_qs(urllib.parse.urlsplit(code.strip()).query)
    if "token" in link:
        body = {"type": link.get("type", ["magiclink"])[0], "token_hash": link["token"][0]}
    else:
        body = {"type": "email", "email": email.strip(), "token": code.strip()}
    out = _http("POST", "/auth/v1/verify", body, auth=False)
    _save_session(out)
    db.set_setting(_K_EMAIL, out.get("user", {}).get("email") or email.strip())
    db.set_setting(_K_USER, out.get("user", {}).get("id", ""))
    db.set_setting(_K_CURSOR, None)
    with db._connect() as conn:  # noqa: SLF001
        for table in db.SYNC_TABLES:
            conn.execute(f"UPDATE {table} SET dirty = 1")   # upload everything to this account
    return status()


def _forget() -> None:
    _access.update(token="", until=0.0)
    for k in (_K_REFRESH, _K_EMAIL, _K_USER, _K_CURSOR, _K_LAST):
        db.set_setting(k, None)


def sign_out() -> None:
    """Stop syncing. Everything stays on this computer and in the account."""
    try:
        _http("POST", "/auth/v1/logout")
    except SyncError:
        pass
    _forget()


# ── registrations: the first decision wins ───────────────────────────────

def claim(uid: str, decision: str) -> None:
    """Record this computer's decision on a waiting registration, unless the
    phone decided first (SyncError 409). Not on the server yet: nothing to claim."""
    won = _http("PATCH", f"/rest/v1/pending_students?id=eq.{uid}&decision=is.null&deleted=is.false",
                {"decision": decision, "decided_by": "desktop", "decided_at": _utc_now()},
                prefer="return=representation")
    if won:
        return
    row = _http("GET", f"/rest/v1/pending_students?id=eq.{uid}&select=decision,decided_by,deleted")
    # shortcut: "desktop" is taken to be this computer; tell them apart if teachers use several.
    if row and row[0]["decided_by"] != "desktop" and (row[0]["decision"] or row[0]["deleted"]):
        by = "your phone" if row[0]["decided_by"] == "phone" else "another computer"
        raise SyncError(f"This registration was already {row[0]['decision'] or 'handled'} on {by}.", 409)


def release(uid: str, action: str, note: str) -> None:
    """The phone accepted, but it can't be done (e.g. the number is taken):
    put it back on both devices with the reason."""
    _http("PATCH", f"/rest/v1/pending_students?id=eq.{uid}",
          {"decision": None, "decided_by": None, "decided_at": None, "action": action, "action_note": note})


# ── sync ─────────────────────────────────────────────────────────────────

Preview = Callable[[dict], tuple[str, str]]       # pending row -> (action, note)
Apply = Callable[[dict, str], tuple[str, str] | None]   # (pending row, decision) -> None, or (action, note) if refused


def run(preview: Preview, apply: Apply) -> None:
    """Upload local changes, then apply the account's changes here."""
    global _last_error
    with _lock:
        try:
            _push(preview)
            _pull(apply)
        except SyncError as exc:
            _last_error = exc.message
            raise
        _last_error = ""
        db.set_setting(_K_LAST, datetime.now().isoformat(sep=" ", timespec="seconds"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _local_ts(remote: str) -> str:
    """Supabase's '2026-10-29T02:41:07.506+00:00' in this database's format."""
    dt = datetime.fromisoformat(remote).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _push(preview: Preview) -> None:
    with db._connect() as conn:  # noqa: SLF001
        for table, (columns, parents) in db.SYNC_TABLES.items():
            cols = ", ".join(f"p_{c}.uid AS {c}" if c in parents else f"t.{c}" for c in columns)
            joins = " ".join(f"LEFT JOIN {p} p_{c} ON p_{c}.id = t.{c}" for c, p in parents.items())
            rows = conn.execute(
                f"SELECT t.rowid AS _id, t.uid, t.updated_at, {cols} FROM {table} t {joins} WHERE t.dirty = 1"
            ).fetchall()
            for start in range(0, len(rows), _BATCH):
                batch = rows[start:start + _BATCH]
                body = []
                for r in batch:
                    item = {"id": r["uid"], "updated_at": r["updated_at"], "deleted": False}
                    for c in columns:
                        v = r[c]
                        item[c] = base64.b64encode(v).decode() if c in _BLOBS and v is not None else v
                    if table == "classes":
                        item["online"] = bool(item["online"])
                    if table == "pending_students":
                        item["action"], item["action_note"] = preview(db.get_pending(r["_id"]))
                    body.append(item)
                _http("POST", f"/rest/v1/{table}?on_conflict=id", body,
                      prefer="resolution=merge-duplicates,return=minimal")
                # Rows changed while uploading keep dirty = 1 for the next run.
                conn.executemany(f"UPDATE {table} SET dirty = 0 WHERE uid = ? AND updated_at = ?",
                                 [(r["uid"], r["updated_at"]) for r in batch])
                conn.commit()

        gone = conn.execute("SELECT tbl, uid FROM sync_deleted ORDER BY tbl").fetchall()
        for table in db.SYNC_TABLES:
            uids = [r["uid"] for r in gone if r["tbl"] == table]
            for start in range(0, len(uids), 100):   # ids go in the address; keep it short
                part = uids[start:start + 100]
                _http("PATCH", f"/rest/v1/{table}?id=in.({','.join(part)})",
                      {"deleted": True, "updated_at": _utc_now()})
                conn.executemany("DELETE FROM sync_deleted WHERE tbl = ? AND uid = ?",
                                 [(table, u) for u in part])
                conn.commit()


def _pull(apply: Apply) -> None:
    cursor = db.get_setting(_K_CURSOR)
    since = (datetime.fromisoformat(cursor) - timedelta(seconds=_OVERLAP)).isoformat() if cursor else "-infinity"
    changes = _http("POST", "/rest/v1/rpc/presentia_changes", {"since": since}) or {}
    newest = cursor
    decided: list[tuple[int, str]] = []
    with db._connect() as conn:  # noqa: SLF001
        for table, (columns, parents) in db.SYNC_TABLES.items():
            for row in changes.get(table, []):
                if not newest or datetime.fromisoformat(row["server_at"]) > datetime.fromisoformat(newest):
                    newest = row["server_at"]
                local = conn.execute(f"SELECT rowid AS id, updated_at FROM {table} WHERE uid = ?", (row["id"],)).fetchone()
                if row["deleted"]:
                    if local:
                        conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (local["id"],))
                        conn.execute("DELETE FROM sync_deleted WHERE tbl = ? AND uid = ?", (table, row["id"]))
                    continue
                if table == "pending_students":
                    # Only this computer adds registrations; from the phone come decisions.
                    if local and row["decision"] and row["decided_by"] == "phone":
                        decided.append((local["id"], row["decision"]))
                    continue
                if conn.execute("SELECT 1 FROM sync_deleted WHERE tbl = ? AND uid = ?",
                                (table, row["id"])).fetchone():
                    continue   # deleted here; that upload wins
                updated = _local_ts(row["updated_at"])
                if local and local["updated_at"] >= updated:
                    continue   # already have it, or a newer change here
                values = []
                for c in columns:
                    v = row.get(c)
                    if c in parents and v is not None:
                        parent = conn.execute(f"SELECT id FROM {parents[c]} WHERE uid = ?", (v,)).fetchone()
                        if parent is None:
                            break   # its parent was deleted here
                        v = parent["id"]
                    elif c in _BLOBS and v is not None:
                        v = base64.b64decode(v)
                    elif isinstance(v, bool):
                        v = int(v)
                    values.append(v)
                else:
                    try:
                        if local:
                            sets = ", ".join(f"{c} = ?" for c in columns)
                            conn.execute(f"UPDATE {table} SET {sets}, updated_at = ?, dirty = 0 WHERE rowid = ?",
                                         (*values, updated, local["id"]))
                        else:
                            conn.execute(
                                f"INSERT INTO {table} ({', '.join(columns)}, uid, updated_at, dirty) "
                                f"VALUES ({', '.join('?' * len(columns))}, ?, ?, 0)",
                                (*values, row["id"], updated),
                            )
                    except sqlite3.IntegrityError as exc:
                        # shortcut: the same student number or join code made separately on two
                        # computers is skipped; merge them if teachers use several desktops.
                        diag.record("sync", f"Skipped {table} {row['id']} from the account: {exc}", "warning")
    if newest:
        db.set_setting(_K_CURSOR, newest)
    for pending_id, decision in decided:
        p = db.get_pending(pending_id)
        if p is None:
            continue
        refused = apply(p, decision)
        if refused:
            release(p["uid"], *refused)

"""Password hashing for server-mode teacher accounts (scrypt, from the standard library)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if kind != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64),
                                n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


# Checked against when the username does not exist, so a wrong username takes
# as long as a wrong password (nothing to learn from the timing).
DUMMY_HASH = hash_password(secrets.token_urlsafe(12))


def password_problem(password: str) -> str:
    """Why a new password is not acceptable, or "" if it is."""
    if len(password) < 10:
        return "Use at least 10 characters."
    kinds = sum((any(c.islower() for c in password), any(c.isupper() for c in password),
                 any(c.isdigit() for c in password), any(not c.isalnum() for c in password)))
    if kinds < 2:
        return "Mix letters with capitals, numbers or symbols."
    return ""

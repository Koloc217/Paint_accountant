"""Optional sign-in: one user, `admin`, with a password. Off until turned on in Settings.

The password is stored as a salted PBKDF2 hash. A signed cookie keeps a device signed in
for a year; changing the password or turning sign-in off and on again signs everyone out.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time

from . import repository as repo

USER = "admin"
COOKIE = "pa_session"
COOKIE_MAX_AGE = 365 * 24 * 3600
MIN_PASSWORD = 4
_ITERATIONS = 200_000

# a few wrong passwords in a row from one address -> a pause
MAX_FAILS = 5
LOCK_SECONDS = 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, iterations, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(check.hex(), digest)
    except ValueError:
        return False


def enabled(conn: sqlite3.Connection) -> bool:
    return repo.get_setting(conn, "auth_enabled") == "1"


def _secret(conn: sqlite3.Connection) -> bytes:
    secret = repo.get_setting(conn, "auth_secret")
    if secret is None:
        secret = secrets.token_hex(32)
        with conn:
            repo.set_setting(conn, "auth_secret", secret)
    return bytes.fromhex(secret)


def _generation(conn: sqlite3.Connection) -> str:
    return repo.get_setting(conn, "auth_generation") or "0"


def _sign(conn: sqlite3.Connection, payload: str) -> str:
    return hmac.new(_secret(conn), payload.encode(), hashlib.sha256).hexdigest()


def make_token(conn: sqlite3.Connection) -> str:
    payload = f"{USER}.{_generation(conn)}.{int(time.time())}"
    return f"{payload}.{_sign(conn, payload)}"


def token_valid(conn: sqlite3.Connection, token: str | None) -> bool:
    if not token or token.count(".") != 3:
        return False
    payload, sig = token.rsplit(".", 1)
    user, generation, issued = payload.split(".")
    if user != USER or generation != _generation(conn) or not issued.isdigit():
        return False
    if time.time() - int(issued) > COOKIE_MAX_AGE:
        return False
    return hmac.compare_digest(sig, _sign(conn, payload))


def _new_generation(conn: sqlite3.Connection) -> None:
    repo.set_setting(conn, "auth_generation", str(int(_generation(conn)) + 1))


def set_password(conn: sqlite3.Connection, password: str) -> None:
    """Turn sign-in on (or change the password); devices signed in before are signed out."""
    with conn:
        repo.set_setting(conn, "auth_password", hash_password(password))
        repo.set_setting(conn, "auth_enabled", "1")
        _new_generation(conn)


def disable(conn: sqlite3.Connection) -> None:
    with conn:
        repo.set_setting(conn, "auth_enabled", None)
        repo.set_setting(conn, "auth_password", None)
        _new_generation(conn)


def check_password(conn: sqlite3.Connection, password: str) -> bool:
    return verify_password(password, repo.get_setting(conn, "auth_password"))


class Throttle:
    """Counts wrong passwords per address; after MAX_FAILS the address waits LOCK_SECONDS."""

    def __init__(self) -> None:
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def wait_seconds(self, who: str) -> int:
        with self._lock:
            count, since = self._fails.get(who, (0, 0.0))
            left = LOCK_SECONDS - (time.monotonic() - since)
            if count >= MAX_FAILS and left > 0:
                return int(left) + 1
            if count >= MAX_FAILS:
                self._fails.pop(who, None)
            return 0

    def failed(self, who: str) -> None:
        with self._lock:
            count, _ = self._fails.get(who, (0, 0.0))
            self._fails[who] = (count + 1, time.monotonic())

    def succeeded(self, who: str) -> None:
        with self._lock:
            self._fails.pop(who, None)

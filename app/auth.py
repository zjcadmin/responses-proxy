from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from threading import Lock


def generate_salt() -> str:
    return secrets.token_urlsafe(16)


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return base64.b64encode(digest).decode("ascii")


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


class SessionStore:
    def __init__(self) -> None:
        self._tokens: set[str] = set()
        self._lock = Lock()

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens.add(token)
        return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return token in self._tokens

    def destroy_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.discard(token)

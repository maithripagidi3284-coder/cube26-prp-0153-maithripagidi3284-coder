"""Small self-contained authentication service for the hackathon app.

Passwords use PBKDF2-HMAC-SHA256; sessions are signed bearer tokens.
No password is ever stored in plaintext.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import secrets
import time
from app import config

_ITERATIONS = 310_000


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return _b64(digest), _b64(salt)


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    try:
        salt = _unb64(stored_salt)
        expected = _unb64(stored_hash)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": int(time.time()) + config.AUTH_TOKEN_TTL_SECONDS}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(config.AUTH_SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_token(token: str) -> str | None:
    try:
        body, signature = token.split(".", 1)
        expected = _b64(hmac.new(config.AUTH_SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_unb64(body))
        if int(payload["exp"]) < int(time.time()):
            return None
        return str(payload["sub"])
    except Exception:
        return None

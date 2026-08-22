from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from common.security import hash_password, verify_password


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_session(
    username: str,
    password_version: int,
    secret: bytes,
    ttl_seconds: int,
) -> str:
    payload = {
        "exp": int(time.time()) + ttl_seconds,
        "pv": password_version,
        "sub": username,
    }
    encoded = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _b64encode(hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def parse_session(token: str, secret: bytes) -> dict[str, Any] | None:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            return None
        payload = json.loads(_b64decode(encoded))
        if not isinstance(payload, dict) or int(payload["exp"]) < int(time.time()):
            return None
        if not isinstance(payload.get("sub"), str):
            return None
        payload["pv"] = int(payload["pv"])
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


@dataclass(slots=True)
class Window:
    started_at: float
    attempts: int


class FixedWindowLimiter:
    def __init__(self, attempts: int, seconds: int) -> None:
        self.attempts = attempts
        self.seconds = seconds
        self._windows: dict[str, Window] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self._windows.get(key)
        if window is None or now - window.started_at >= self.seconds:
            self._windows[key] = Window(started_at=now, attempts=1)
            return True
        if window.attempts >= self.attempts:
            return False
        window.attempts += 1
        return True

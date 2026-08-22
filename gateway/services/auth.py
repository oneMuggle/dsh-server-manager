from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.requests import HTTPConnection

from common.storage.users import RegistryError, get_user

from ..runtime import config


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
    signature = _b64encode(
        hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    )
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


def resolve_user(request: HTTPConnection, token: str | None) -> dict | None:
    if not token:
        return None
    payload = parse_session(token, request.app.state.session_secret)
    if payload is None:
        return None
    try:
        user = get_user(config.user_database_path, payload["sub"])
    except RegistryError:
        return None
    if not user or user.get("disabled") is True:
        return None
    if int(user.get("password_version", 0)) != payload["pv"]:
        return None
    return user


def request_user(request: HTTPConnection) -> dict | None:
    return resolve_user(request, request.cookies.get(config.cookie_name))


def user_role(user: dict) -> Literal["admin", "user"]:
    return "admin" if user.get("role") == "admin" else "user"


def require_session(request: HTTPConnection, token: str | None) -> dict:
    user = resolve_user(request, token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(request: HTTPConnection, token: str | None) -> dict:
    user = require_session(request, token)
    if user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def make_session_token(request: HTTPConnection, user: dict) -> str:
    return make_session(
        str(user["username"]),
        int(user["password_version"]),
        request.app.state.session_secret,
        config.session_ttl_seconds,
    )


def set_session(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.cookie_name,
        value=token,
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def authenticated_response(
    request: HTTPConnection,
    user: dict,
    status_code: int = 200,
) -> JSONResponse:
    token = make_session_token(request, user)
    response = JSONResponse(
        {
            "username": user["username"],
            "role": user_role(user),
            "accessToken": token,
            "tokenType": "Bearer",
            "expiresIn": config.session_ttl_seconds,
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
    set_session(response, token)
    return response

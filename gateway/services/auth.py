from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.requests import HTTPConnection

from common.storage.users import RegistryError, get_user

from ..auth import make_session, parse_session
from ..runtime import config


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

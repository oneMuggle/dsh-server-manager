from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from common.security import hash_password

from ...runtime import config
from ...schemas.auth import AuthenticatedUser, Credentials
from ...services.auth import FixedWindowLimiter, authenticated_response
from ...services.supervisor import SupervisorError, create_user


router = APIRouter()
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
register_limiter = FixedWindowLimiter(attempts=5, seconds=300)


@router.post(
    "/registrations",
    status_code=201,
    response_model=AuthenticatedUser,
    summary="注册账号",
)
async def register(request: Request, credentials: Credentials) -> Response:
    if not config.registration_enabled:
        raise HTTPException(status_code=403, detail="注册功能已关闭")
    client = request.client.host if request.client else "unknown"
    if not register_limiter.allow(client):
        raise HTTPException(status_code=429, detail="注册请求过于频繁")
    if not USERNAME_PATTERN.fullmatch(credentials.username):
        raise HTTPException(
            status_code=422,
            detail="用户名须以小写字母开头，只能包含小写字母、数字、_ 和 -",
        )

    password_record = await asyncio.to_thread(hash_password, credentials.password)
    try:
        result = await create_user(
            config.control_socket_path,
            credentials.username,
            password_record,
        )
    except SupervisorError as error:
        status = 409 if error.code == "conflict" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

    user = {
        "username": result["username"],
        "password_version": result["password_version"],
        "role": result["role"],
    }
    return authenticated_response(request, user, status_code=201)

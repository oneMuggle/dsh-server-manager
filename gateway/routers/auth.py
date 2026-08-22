from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from common.security import hash_password, verify_password
from common.storage.users import RegistryError, get_user

from ..dependencies import ApiToken
from ..pages import auth_page
from ..runtime import config
from ..schemas.auth import AuthenticatedUser, Credentials, CurrentUser
from ..services.auth import (
    FixedWindowLimiter,
    authenticated_response,
    request_user,
    require_session,
    user_role,
)
from ..services.supervisor import SupervisorError, create_user


router = APIRouter()
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
login_ip_limiter = FixedWindowLimiter(attempts=30, seconds=60)
login_account_limiter = FixedWindowLimiter(attempts=10, seconds=60)
register_limiter = FixedWindowLimiter(attempts=5, seconds=300)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> Response:
    if request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("login"), headers={"Cache-Control": "no-store"})


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request) -> Response:
    if request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("register"), headers={"Cache-Control": "no-store"})


@router.post(
    "/api/gateway/v1/auth/register",
    status_code=201,
    response_model=AuthenticatedUser,
    tags=["认证"],
    summary="注册账号",
)
async def register(request: Request, credentials: Credentials) -> Response:
    if not register_limiter.allow(client_key(request)):
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


@router.post(
    "/api/gateway/v1/auth/login",
    response_model=AuthenticatedUser,
    tags=["认证"],
    summary="登录",
)
async def login(request: Request, credentials: Credentials) -> Response:
    client = client_key(request)
    account_key = f"{client}:{credentials.username}"
    if not login_ip_limiter.allow(client) or not login_account_limiter.allow(account_key):
        raise HTTPException(status_code=429, detail="登录请求过于频繁")
    try:
        user = get_user(config.user_database_path, credentials.username)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error

    valid = bool(user and user.get("disabled") is not True)
    if valid:
        valid = await asyncio.to_thread(
            verify_password,
            credentials.password,
            user.get("password", {}),
        )
    else:
        await asyncio.to_thread(hash_password, credentials.password)
    if not valid or user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    return authenticated_response(request, user)


@router.post(
    "/api/gateway/v1/auth/logout",
    status_code=204,
    tags=["认证"],
    summary="退出登录",
)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(config.cookie_name, path="/")
    return response


@router.get(
    "/api/gateway/v1/auth/me",
    response_model=CurrentUser,
    tags=["认证"],
    summary="查询当前账号",
)
async def me(request: Request, token: ApiToken) -> CurrentUser:
    user = require_session(request, token)
    return CurrentUser(username=str(user["username"]), role=user_role(user))

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import OAuth2PasswordRequestForm

from common.security import hash_password
from common.storage.users import RegistryError

from ..dependencies import ApiToken
from ..runtime import config
from ..schemas.auth import AuthenticatedUser, Credentials, CurrentUser, OAuthToken
from ..services.auth import (
    FixedWindowLimiter,
    authenticated_response,
    make_session_token,
    request_user,
    require_session,
    user_role,
    verify_credentials,
)
from ..services.supervisor import SupervisorError, create_user
from ..web.pages import auth_page


router = APIRouter()
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
login_ip_limiter = FixedWindowLimiter(attempts=30, seconds=60)
login_account_limiter = FixedWindowLimiter(attempts=10, seconds=60)
register_limiter = FixedWindowLimiter(attempts=5, seconds=300)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def authenticated_credentials(
    request: Request,
    username: str,
    password: str,
) -> dict:
    client = client_key(request)
    account_key = f"{client}:{username}"
    if not login_ip_limiter.allow(client) or not login_account_limiter.allow(account_key):
        raise HTTPException(status_code=429, detail="登录请求过于频繁")
    try:
        user = await verify_credentials(username, password)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> Response:
    if request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        auth_page("login", registration_enabled=config.registration_enabled),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request) -> Response:
    if not config.registration_enabled:
        raise HTTPException(status_code=404, detail="页面不存在")
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
    if not config.registration_enabled:
        raise HTTPException(status_code=403, detail="注册功能已关闭")
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
    user = await authenticated_credentials(
        request, credentials.username, credentials.password
    )
    return authenticated_response(request, user)


@router.post(
    "/api/gateway/v1/auth/token",
    response_model=OAuthToken,
    tags=["认证"],
    summary="使用账号密码获取 Bearer Token",
)
async def oauth_token(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
) -> OAuthToken:
    user = await authenticated_credentials(request, form.username, form.password)
    return OAuthToken(
        access_token=make_session_token(request, user),
        expires_in=config.session_ttl_seconds,
    )


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

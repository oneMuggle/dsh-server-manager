from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import aiohttp
from fastapi import FastAPI, HTTPException, Request, Security, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field

from .auth import (
    FixedWindowLimiter,
    hash_password,
    make_session,
    parse_session,
    verify_password,
)
from .config import Config
from .pages import admin_page, auth_page
from .proxy import cookie_from_websocket, proxy_http, proxy_websocket
from .registry import RegistryError, get_user, load_registry
from .supervisor_client import (
    SupervisorError,
    create_user,
    install_upload,
    list_upload_directories,
)

LOGGER = logging.getLogger("dsh.gateway")
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
config = Config.from_environment()
login_ip_limiter = FixedWindowLimiter(attempts=30, seconds=60)
login_account_limiter = FixedWindowLimiter(attempts=10, seconds=60)
register_limiter = FixedWindowLimiter(attempts=5, seconds=300)
session_cookie = APIKeyCookie(name=config.cookie_name, auto_error=False)
SessionCookie = Annotated[str | None, Security(session_cookie)]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=3, max_length=24)]
    password: Annotated[str, Field(min_length=8, max_length=256)]


class CurrentUser(BaseModel):
    username: str
    role: Literal["admin", "user"]


class RegisteredUser(BaseModel):
    username: str
    role: Literal["admin", "user"]
    disabled: bool
    created_at: str


class RegisteredUsers(BaseModel):
    users: list[RegisteredUser]


class DirectoryListing(BaseModel):
    directory: str
    directories: list[str]
    truncated: bool


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _resolve_user(token: str | None) -> dict | None:
    if not token:
        return None
    payload = parse_session(token, app.state.session_secret)
    if payload is None:
        return None
    try:
        user = get_user(config.registry_path, payload["sub"])
    except RegistryError:
        LOGGER.exception("failed to load registry")
        return None
    if not user or user.get("disabled") is True:
        return None
    if int(user.get("password_version", 0)) != payload["pv"]:
        return None
    return user


def _request_user(request: Request) -> dict | None:
    return _resolve_user(request.cookies.get(config.cookie_name))


def _user_role(user: dict) -> Literal["admin", "user"]:
    return "admin" if user.get("role") == "admin" else "user"


def _require_session(token: str | None) -> dict:
    user = _resolve_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def _require_admin(token: str | None) -> dict:
    user = _require_session(token)
    if _user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _set_session(response: Response, user: dict) -> None:
    response.set_cookie(
        key=config.cookie_name,
        value=make_session(
            str(user["username"]),
            int(user["password_version"]),
            app.state.session_secret,
            config.session_ttl_seconds,
        ),
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.session_secret = config.session_secret_path.read_bytes()
    application.state.http = aiohttp.ClientSession(auto_decompress=False)
    try:
        yield
    finally:
        await application.state.http.close()


app = FastAPI(
    title="DSH LAN Gateway",
    description="DeepSeek Harness 多用户局域网网关。受保护接口使用登录后生成的 HttpOnly Cookie。",
    version="0.2.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


@app.get("/api/gateway/v1/health", tags=["系统"], summary="健康检查")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> Response:
    if _request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("login"), headers={"Cache-Control": "no-store"})


@app.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request) -> Response:
    if _request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("register"), headers={"Cache-Control": "no-store"})


@app.post(
    "/api/gateway/v1/auth/register",
    status_code=201,
    response_model=CurrentUser,
    tags=["认证"],
    summary="注册账号",
)
async def register(request: Request, credentials: Credentials) -> Response:
    if not register_limiter.allow(_client_key(request)):
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
    response = JSONResponse(
        {"username": result["username"], "role": result["role"]},
        status_code=201,
    )
    _set_session(response, user)
    return response


@app.post(
    "/api/gateway/v1/auth/login",
    response_model=CurrentUser,
    tags=["认证"],
    summary="登录",
)
async def login(request: Request, credentials: Credentials) -> Response:
    client = _client_key(request)
    account_key = f"{client}:{credentials.username}"
    if not login_ip_limiter.allow(client) or not login_account_limiter.allow(account_key):
        raise HTTPException(status_code=429, detail="登录请求过于频繁")
    try:
        user = get_user(config.registry_path, credentials.username)
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

    response = JSONResponse(
        {"username": user["username"], "role": _user_role(user)}
    )
    _set_session(response, user)
    return response


@app.post(
    "/api/gateway/v1/auth/logout",
    status_code=204,
    tags=["认证"],
    summary="退出登录",
)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(config.cookie_name, path="/")
    return response


@app.get(
    "/api/gateway/v1/auth/me",
    response_model=CurrentUser,
    tags=["认证"],
    summary="查询当前账号",
)
async def me(token: SessionCookie) -> CurrentUser:
    user = _require_session(token)
    return CurrentUser(username=str(user["username"]), role=_user_role(user))


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def management_page(request: Request) -> Response:
    user = _request_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if _user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return HTMLResponse(admin_page(), headers={"Cache-Control": "no-store"})


@app.get(
    "/api/gateway/v1/admin/users",
    response_model=RegisteredUsers,
    tags=["管理"],
    summary="列出注册用户",
)
async def registered_users(token: SessionCookie) -> RegisteredUsers:
    _require_admin(token)
    try:
        registry = load_registry(config.registry_path)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error
    users = [
        RegisteredUser(
            username=str(user["username"]),
            role=_user_role(user),
            disabled=user.get("disabled") is True,
            created_at=str(user.get("created_at", "")),
        )
        for user in registry["users"].values()
        if isinstance(user, dict)
    ]
    users.sort(key=lambda user: (user.created_at, user.username))
    return RegisteredUsers(users=users)


@app.get(
    "/api/gateway/v1/files/directories",
    response_model=DirectoryListing,
    tags=["文件"],
    summary="列出当前用户工作区目录",
)
async def upload_directories(
    token: SessionCookie,
    directory: str = ".",
) -> DirectoryListing:
    user = _require_session(token)
    if len(directory) > 512:
        raise HTTPException(status_code=400, detail="目标目录过长")
    try:
        result = await list_upload_directories(
            config.control_socket_path,
            str(user["username"]),
            directory,
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error
    return DirectoryListing.model_validate(result)


@app.post(
    "/api/gateway/v1/files/upload",
    tags=["文件"],
    summary="上传文件到当前用户工作区",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_file(
    request: Request,
    token: SessionCookie,
    directory: str = ".",
    name: str = "",
) -> Response:
    user = _require_session(token)
    if not name:
        raise HTTPException(status_code=400, detail="缺少文件名")
    if len(directory) > 512 or len(name) > 255:
        raise HTTPException(status_code=400, detail="目标目录或文件名过长")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from error
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="无效的 Content-Length")
        if declared_size > config.max_upload_bytes:
            raise HTTPException(status_code=413, detail="单个文件不能超过 100 MB")

    spool_name = f"{secrets.token_hex(16)}.upload"
    spool_path = config.upload_spool_path / spool_name
    descriptor = os.open(
        spool_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > config.max_upload_bytes:
                raise HTTPException(status_code=413, detail="单个文件不能超过 100 MB")
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise OSError("failed to write upload spool")
                remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        descriptor = -1
        spool_path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        result = await install_upload(
            config.control_socket_path,
            str(user["username"]),
            spool_name,
            directory,
            name,
            size,
        )
    except SupervisorError as error:
        spool_path.unlink(missing_ok=True)
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

    return JSONResponse(
        {
            "directory": result["directory"],
            "name": result["filename"],
            "size": result["size"],
        }
    )


async def _proxy_authenticated_request(request: Request) -> Response:
    user = _request_user(request)
    if not user:
        if request.method in {"GET", "HEAD"}:
            return RedirectResponse("/login", status_code=303)
        raise HTTPException(status_code=401, detail="未登录")
    return await proxy_http(
        request,
        app.state.http,
        int(user["port"]),
        config.max_transform_bytes,
        is_admin=_user_role(user) == "admin",
    )


@app.api_route(
    "/",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def main_page(request: Request) -> Response:
    """Authenticated main entry; the DSH Web application is the main interface."""
    return await _proxy_authenticated_request(request)


@app.api_route(
    "/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def harness_proxy(request: Request, path: str) -> Response:
    return await _proxy_authenticated_request(request)


@app.websocket("/{path:path}")
async def harness_websocket(websocket: WebSocket, path: str) -> None:
    token = cookie_from_websocket(websocket, config.cookie_name)
    user = _resolve_user(token)
    if not user:
        await websocket.close(code=4401, reason="authentication required")
        return
    await proxy_websocket(websocket, app.state.http, int(user["port"]))

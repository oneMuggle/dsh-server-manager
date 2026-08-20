from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated

import aiohttp
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .auth import (
    FixedWindowLimiter,
    hash_password,
    make_session,
    parse_session,
    verify_password,
)
from .config import Config
from .pages import auth_page
from .proxy import cookie_from_websocket, proxy_http, proxy_websocket
from .registry import RegistryError, get_user
from .supervisor_client import SupervisorError, create_user

LOGGER = logging.getLogger("dsh.gateway")
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
config = Config.from_environment()
login_ip_limiter = FixedWindowLimiter(attempts=30, seconds=60)
login_account_limiter = FixedWindowLimiter(attempts=10, seconds=60)
register_limiter = FixedWindowLimiter(attempts=5, seconds=300)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=3, max_length=24)]
    password: Annotated[str, Field(min_length=8, max_length=256)]


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
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/api/gateway/v1/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    if _request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("login"), headers={"Cache-Control": "no-store"})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> Response:
    if _request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("register"), headers={"Cache-Control": "no-store"})


@app.post("/api/gateway/v1/auth/register", status_code=201)
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
    }
    response = JSONResponse({"username": result["username"]}, status_code=201)
    _set_session(response, user)
    return response


@app.post("/api/gateway/v1/auth/login")
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

    response = JSONResponse({"username": user["username"]})
    _set_session(response, user)
    return response


@app.post("/api/gateway/v1/auth/logout", status_code=204)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(config.cookie_name, path="/")
    return response


@app.get("/api/gateway/v1/auth/me")
async def me(request: Request) -> dict[str, str]:
    user = _request_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"username": str(user["username"])}


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
    )


@app.api_route(
    "/",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def main_page(request: Request) -> Response:
    """Authenticated main entry; the DSH Web application is the main interface."""
    return await _proxy_authenticated_request(request)


@app.api_route(
    "/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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

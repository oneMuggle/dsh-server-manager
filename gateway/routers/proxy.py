from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import RedirectResponse, Response

from ..proxy import cookie_from_websocket, proxy_http, proxy_websocket
from ..runtime import config
from ..services.auth import request_user, resolve_user, user_role


router = APIRouter()


async def _proxy_authenticated_request(request: Request) -> Response:
    user = request_user(request)
    if not user:
        if request.method in {"GET", "HEAD"}:
            return RedirectResponse("/login", status_code=303)
        raise HTTPException(status_code=401, detail="未登录")
    return await proxy_http(
        request,
        request.app.state.http,
        int(user["port"]),
        config.max_transform_bytes,
        is_admin=user_role(user) == "admin",
    )


@router.api_route(
    "/",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def main_page(request: Request) -> Response:
    """Authenticated main entry; the DSH Web application is the main interface."""
    return await _proxy_authenticated_request(request)


@router.api_route(
    "/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def harness_proxy(request: Request, path: str) -> Response:
    return await _proxy_authenticated_request(request)


@router.websocket("/{path:path}")
async def harness_websocket(websocket: WebSocket, path: str) -> None:
    token = cookie_from_websocket(websocket, config.cookie_name)
    user = resolve_user(websocket, token)
    if not user:
        await websocket.close(code=4401, reason="authentication required")
        return
    await proxy_websocket(websocket, websocket.app.state.http, int(user["port"]))

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from http.cookies import SimpleCookie
from typing import Any

import aiohttp
from fastapi import Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from starlette.websockets import WebSocketDisconnect

from .upload_ui import ADMIN_WIDGET, LOGOUT_WIDGET, UPLOAD_WIDGET

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
REQUEST_STRIP = HOP_BY_HOP | {
    "cookie",
    "host",
    "origin",
    "referer",
    "content-length",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-proto",
    "forwarded",
}
WEBSOCKET_STRIP = REQUEST_STRIP | {
    "sec-websocket-accept",
    "sec-websocket-extensions",
    "sec-websocket-key",
    "sec-websocket-protocol",
    "sec-websocket-version",
}
RESPONSE_STRIP = HOP_BY_HOP | {"set-cookie"}

UUID_SHIM = b"""<script>
if (typeof crypto.randomUUID !== 'function') {
  crypto.randomUUID = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  };
}
</script>"""

CONNECTION_MODULE = "/plugins/@deepseek-ai/dsh-client-connection/client.js"
LOOPBACK_CHECK = (
    b"isLoopback: pageLocation === void 0 || "
    b"isLoopbackHostname(pageLocation.hostname)"
)


def _upstream_url(port: int, path: str, query: str) -> str:
    suffix = f"?{query}" if query else ""
    return f"http://127.0.0.1:{port}{path}{suffix}"


def _request_headers(headers: Any, port: int, websocket: bool = False) -> dict[str, str]:
    blocked = WEBSOCKET_STRIP if websocket else REQUEST_STRIP
    result = {name: value for name, value in headers.items() if name.lower() not in blocked}
    result["Host"] = f"127.0.0.1:{port}"
    if not websocket:
        result["Accept-Encoding"] = "identity"
    return result


def _response_headers(response: aiohttp.ClientResponse, transformed: bool) -> dict[str, str]:
    blocked = set(RESPONSE_STRIP)
    if transformed:
        blocked.update({"content-length", "content-encoding", "etag", "last-modified"})
    headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in blocked
    }
    if transformed:
        headers["Cache-Control"] = "no-store"
    return headers


async def _request_body(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        if chunk:
            yield chunk


def _patch_html(body: bytes, is_admin: bool = False) -> bytes:
    body = body.replace(b"<head>", b"<head>" + UUID_SHIM, 1)
    widgets = UPLOAD_WIDGET + LOGOUT_WIDGET + (ADMIN_WIDGET if is_admin else b"")
    body = body.replace(b"<body>", b"<body>" + widgets, 1)
    body = body.replace(
        CONNECTION_MODULE.encode() + b"?rev=",
        CONNECTION_MODULE.encode() + b"?lan=2&rev=",
        1,
    )
    return body


def _patch_connection_module(body: bytes) -> bytes:
    return body.replace(LOOPBACK_CHECK, b"isLoopback: true", 1)


async def _read_limited(
    content: aiohttp.StreamReader,
    limit: int,
) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    async for chunk in content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def proxy_http(
    request: Request,
    session: aiohttp.ClientSession,
    port: int,
    max_transform_bytes: int,
    is_admin: bool = False,
) -> Response:
    url = _upstream_url(port, request.url.path, request.url.query)
    try:
        upstream = await session.request(
            request.method,
            url,
            headers=_request_headers(request.headers, port),
            data=_request_body(request),
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None),
        )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return Response("DSH 正在启动，请稍后刷新。", status_code=503)

    content_type = upstream.headers.get("Content-Type", "").lower()
    is_html = "text/html" in content_type
    is_connection_module = request.url.path == CONNECTION_MODULE
    transformed = is_html or is_connection_module

    if transformed:
        try:
            body = await _read_limited(upstream.content, max_transform_bytes)
            if body is None:
                return Response("upstream response is too large", status_code=502)
            if is_html:
                body = _patch_html(body, is_admin=is_admin)
            if is_connection_module:
                body = _patch_connection_module(body)
            return Response(
                content=body,
                status_code=upstream.status,
                headers=_response_headers(upstream, transformed=True),
            )
        finally:
            upstream.release()

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                yield chunk
        finally:
            upstream.release()

    return StreamingResponse(
        stream(),
        status_code=upstream.status,
        headers=_response_headers(upstream, transformed=False),
    )


async def proxy_websocket(
    websocket: WebSocket,
    session: aiohttp.ClientSession,
    port: int,
) -> None:
    query = websocket.url.query
    url = _upstream_url(port, websocket.url.path, query)
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    protocol_list = [item.strip() for item in protocols.split(",") if item.strip()]

    try:
        upstream = await session.ws_connect(
            url,
            headers=_request_headers(websocket.headers, port, websocket=True),
            protocols=protocol_list,
            timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=10),
            autoclose=True,
            autoping=True,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await websocket.close(code=1013, reason="DSH is starting")
        return

    await websocket.accept(subprotocol=upstream.protocol)

    async def browser_to_upstream() -> None:
        try:
            while True:
                message = await websocket.receive()
                message_type = message["type"]
                if message_type == "websocket.disconnect":
                    await upstream.close(code=message.get("code", 1000))
                    return
                if message.get("text") is not None:
                    await upstream.send_str(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send_bytes(message["bytes"])
        except WebSocketDisconnect:
            await upstream.close()

    async def upstream_to_browser() -> None:
        async for message in upstream:
            if message.type == aiohttp.WSMsgType.TEXT:
                await websocket.send_text(message.data)
            elif message.type == aiohttp.WSMsgType.BINARY:
                await websocket.send_bytes(message.data)
            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                break
        await websocket.close(code=upstream.close_code or 1000)

    tasks = {
        asyncio.create_task(browser_to_upstream()),
        asyncio.create_task(upstream_to_browser()),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*done, *pending, return_exceptions=True)
    await upstream.close()


def cookie_from_websocket(websocket: WebSocket, name: str) -> str | None:
    raw = websocket.headers.get("cookie")
    if not raw:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except Exception:
        return None
    morsel = cookies.get(name)
    return morsel.value if morsel else None

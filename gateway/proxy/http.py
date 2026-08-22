from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import aiohttp
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from .headers import request_headers, response_headers, upstream_url
from .transforms import (
    CONNECTION_MODULE,
    patch_connection_module,
    patch_html,
    read_limited,
)


async def _request_body(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        if chunk:
            yield chunk


async def proxy_http(
    request: Request,
    session: aiohttp.ClientSession,
    port: int,
    max_transform_bytes: int,
    is_admin: bool = False,
) -> Response:
    url = upstream_url(port, request.url.path, request.url.query)
    try:
        upstream = await session.request(
            request.method,
            url,
            headers=request_headers(request.headers, port),
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
            body = await read_limited(upstream.content, max_transform_bytes)
            if body is None:
                return Response("upstream response is too large", status_code=502)
            if is_html:
                body = patch_html(body, is_admin=is_admin)
            if is_connection_module:
                body = patch_connection_module(body)
            return Response(
                content=body,
                status_code=upstream.status,
                headers=response_headers(upstream, transformed=True),
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
        headers=response_headers(upstream, transformed=False),
    )

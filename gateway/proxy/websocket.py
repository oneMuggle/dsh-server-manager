from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie

import aiohttp
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from .headers import request_headers, upstream_url


async def proxy_websocket(
    websocket: WebSocket,
    session: aiohttp.ClientSession,
    port: int,
) -> None:
    url = upstream_url(port, websocket.url.path, websocket.url.query)
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    protocol_list = [item.strip() for item in protocols.split(",") if item.strip()]

    try:
        upstream = await session.ws_connect(
            url,
            headers=request_headers(websocket.headers, port, websocket=True),
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

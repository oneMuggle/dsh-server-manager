from __future__ import annotations

from typing import Any

import aiohttp


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


def upstream_url(port: int, path: str, query: str) -> str:
    suffix = f"?{query}" if query else ""
    return f"http://127.0.0.1:{port}{path}{suffix}"


def request_headers(
    headers: Any,
    port: int,
    websocket: bool = False,
) -> dict[str, str]:
    blocked = WEBSOCKET_STRIP if websocket else REQUEST_STRIP
    result = {
        name: value
        for name, value in headers.items()
        if name.lower() not in blocked
    }
    result["Host"] = f"127.0.0.1:{port}"
    if not websocket:
        result["Accept-Encoding"] = "identity"
    return result


def response_headers(
    response: aiohttp.ClientResponse,
    transformed: bool,
) -> dict[str, str]:
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

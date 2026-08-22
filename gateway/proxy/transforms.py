from __future__ import annotations

import aiohttp

from ..web.fragments import ADMIN_WIDGET, LOGOUT_WIDGET, UPLOAD_WIDGET


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


def patch_html(body: bytes, is_admin: bool = False) -> bytes:
    body = body.replace(b"<head>", b"<head>" + UUID_SHIM, 1)
    widgets = UPLOAD_WIDGET + LOGOUT_WIDGET + (ADMIN_WIDGET if is_admin else b"")
    body = body.replace(b"<body>", b"<body>" + widgets, 1)
    body = body.replace(
        CONNECTION_MODULE.encode() + b"?rev=",
        CONNECTION_MODULE.encode() + b"?lan=2&rev=",
        1,
    )
    return body


def patch_connection_module(body: bytes) -> bytes:
    return body.replace(LOOPBACK_CHECK, b"isLoopback: true", 1)


async def read_limited(
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

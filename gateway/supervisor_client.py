from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class SupervisorError(RuntimeError):
    def __init__(self, message: str, code: str = "supervisor_error") -> None:
        super().__init__(message)
        self.code = code


async def _request(
    socket_path: Path,
    request: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path), timeout=5
        )
    except (OSError, asyncio.TimeoutError) as error:
        raise SupervisorError("user service is unavailable", "unavailable") from error

    try:
        writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            raise SupervisorError("user service closed the request", "unavailable")
        response = json.loads(line)
        if not response.get("ok"):
            raise SupervisorError(
                str(response.get("message", "unable to create user")),
                str(response.get("code", "supervisor_error")),
            )
        return response
    except (asyncio.TimeoutError, json.JSONDecodeError) as error:
        raise SupervisorError("invalid response from user service", "unavailable") from error
    finally:
        writer.close()
        await writer.wait_closed()


async def create_user(
    socket_path: Path,
    username: str,
    password_record: dict[str, Any],
) -> dict[str, Any]:
    return await _request(
        socket_path,
        {
            "action": "create_user",
            "username": username,
            "password": password_record,
        },
        timeout=130,
    )


async def install_upload(
    socket_path: Path,
    username: str,
    spool_name: str,
    directory: str,
    filename: str,
    size: int,
) -> dict[str, Any]:
    return await _request(
        socket_path,
        {
            "action": "install_upload",
            "username": username,
            "spool_name": spool_name,
            "directory": directory,
            "filename": filename,
            "size": size,
        },
        timeout=130,
    )

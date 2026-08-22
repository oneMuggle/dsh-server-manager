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
            asyncio.open_unix_connection(socket_path, limit=1024 * 1024), timeout=5
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
        {"action": "create_user", "username": username, "password": password_record},
        timeout=130,
    )


async def update_user(
    socket_path: Path,
    username: str,
    *,
    password_record: dict[str, Any] | None = None,
    disabled: bool | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {"action": "update_user", "username": username}
    if password_record is not None:
        request["password"] = password_record
    if disabled is not None:
        request["disabled"] = disabled
    return await _request(socket_path, request, timeout=30)


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


async def list_upload_directories(
    socket_path: Path,
    username: str,
    directory: str,
) -> dict[str, Any]:
    return await _request(
        socket_path,
        {"action": "list_upload_directories", "username": username, "directory": directory},
        timeout=15,
    )


async def import_online_plugin(
    socket_path: Path,
    package_name: str,
    requested_version: str,
    created_by: str,
) -> dict[str, Any]:
    return await _request(
        socket_path,
        {
            "action": "plugin_online_import",
            "package_name": package_name,
            "requested_version": requested_version,
            "created_by": created_by,
        },
        timeout=15,
    )


async def discover_plugin_versions(
    socket_path: Path,
    package_name: str,
) -> dict[str, Any]:
    return await _request(
        socket_path,
        {"action": "plugin_discover_versions", "package_name": package_name},
        timeout=70,
    )


async def import_offline_plugin(
    socket_path: Path,
    spool_name: str,
    created_by: str,
) -> dict[str, Any]:
    return await _request(
        socket_path,
        {
            "action": "plugin_offline_import",
            "spool_name": spool_name,
            "created_by": created_by,
        },
        timeout=15,
    )


async def assign_plugin(
    socket_path: Path,
    *,
    plugin_action: str,
    plugin_id: int,
    plugin_version_id: int | None,
    scope: dict[str, Any],
    enabled_after_install: bool,
    apply_to_future_users: bool,
    created_by: str,
) -> dict[str, Any]:
    return await _request(
        socket_path,
        {
            "action": "plugin_assignment",
            "plugin_action": plugin_action,
            "plugin_id": plugin_id,
            "plugin_version_id": plugin_version_id,
            "scope": scope,
            "enabled_after_install": enabled_after_install,
            "apply_to_future_users": apply_to_future_users,
            "created_by": created_by,
        },
        timeout=15,
    )

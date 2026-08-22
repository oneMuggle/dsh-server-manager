from __future__ import annotations

import asyncio
import json
import logging
import os
import pwd
import socket
import struct
from pathlib import Path
from typing import Any

from common.contracts.control import (
    AssignPluginRequest,
    CreateUserRequest,
    DiscoverPluginVersionsRequest,
    ImportOfflinePluginRequest,
    ImportOnlinePluginRequest,
    InstallUploadRequest,
    ListUploadDirectoriesRequest,
    UpdateUserRequest,
    error_response,
    parse_control_request,
    success_response,
)
from common.errors import ControlRequestError

from ..plugins import PluginOperationError
from ..tenants.firewall import FirewallError


class ControlServer:
    def __init__(
        self,
        *,
        owner: Any,
        gateway_user: str,
        fatal: asyncio.Event,
        logger: logging.Logger | None = None,
    ) -> None:
        self.owner = owner
        self.gateway_user = gateway_user
        self.fatal = fatal
        self.logger = logger or logging.getLogger("dsm.supervisor")
        self.server: asyncio.AbstractServer | None = None

    def _authorized_peer(self, writer: asyncio.StreamWriter) -> bool:
        transport_socket = writer.get_extra_info("socket")
        if transport_socket is None or not hasattr(socket, "SO_PEERCRED"):
            return False
        credentials = transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("3i", credentials)
        return uid == pwd.getpwnam(self.gateway_user).pw_uid

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            if not self._authorized_peer(writer):
                response = error_response("forbidden", "forbidden")
            else:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                request = parse_control_request(json.loads(line))
                action = request.action
                try:
                    if isinstance(request, CreateUserRequest):
                        result = await self.owner.create_user(request.username, request.password)
                    elif isinstance(request, UpdateUserRequest):
                        result = await self.owner.update_user(
                            request.username,
                            password=request.password,
                            disabled=request.disabled,
                        )
                    elif isinstance(request, InstallUploadRequest):
                        result = await self.owner.install_upload(
                            request.username,
                            request.spool_name,
                            request.directory,
                            request.filename,
                            request.size,
                        )
                    elif isinstance(request, ListUploadDirectoriesRequest):
                        result = await self.owner.list_upload_directories(
                            request.username, request.directory
                        )
                    elif isinstance(request, ImportOnlinePluginRequest):
                        result = await self.owner.import_online_plugin(
                            request.package_name,
                            request.requested_version,
                            request.created_by,
                        )
                    elif isinstance(request, DiscoverPluginVersionsRequest):
                        result = await self.owner.discover_plugin_versions(request.package_name)
                    elif isinstance(request, ImportOfflinePluginRequest):
                        result = await self.owner.import_offline_plugin(
                            request.spool_name, request.created_by
                        )
                    elif isinstance(request, AssignPluginRequest):
                        result = await self.owner.assign_plugin(request.model_dump())
                    else:
                        raise ValueError("unknown action")
                    response = success_response(**result)
                except FileExistsError:
                    response = error_response("conflict", "用户名已存在")
                except LookupError:
                    response = error_response("not_found", "用户不存在")
                except PermissionError:
                    response = error_response("conflict", "管理员账号不能停用")
                except (TypeError, ValueError, PluginOperationError) as error:
                    response = error_response("bad_request", str(error))
                except FirewallError:
                    self.logger.exception("firewall update failed; stopping container")
                    self.fatal.set()
                    response = error_response("supervisor_error", "租户隔离更新失败")
                except Exception:
                    self.logger.exception("control action %s failed", action)
                    response = error_response("supervisor_error", "操作失败")
        except (asyncio.TimeoutError, json.JSONDecodeError, ControlRequestError):
            response = error_response("bad_request", "invalid request")
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self, runtime_root: Path, socket_path: Path) -> None:
        runtime_root.mkdir(parents=True, exist_ok=True)
        gateway_account = pwd.getpwnam(self.gateway_user)
        os.chown(runtime_root, 0, gateway_account.pw_gid)
        os.chmod(runtime_root, 0o770)
        socket_path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self.handle, socket_path)
        os.chown(socket_path, 0, gateway_account.pw_gid)
        os.chmod(socket_path, 0o660)

    async def stop(self, socket_path: Path) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        socket_path.unlink(missing_ok=True)

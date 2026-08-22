from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Any

from common.storage.assignments import apply_plugin_defaults
from common.storage.plugin_catalog import get_plugin_version
from common.storage.plugin_jobs import create_job, unfinished_job_ids
from common.storage.users import (
    RegistryError,
    add_user,
    get_user,
    replace_registry,
)
from common.security import hash_password

from .config import SupervisorConfig
from .control import ControlServer
from .gateway_process import start_gateway, stop_gateway
from .plugins import SharedPluginManager, validate_package_name, validate_requested_version
from .state import initialize_gateway_state, load_user_registry
from .tenants import TenantManager
from .tenants.accounts import (
    ADMIN_USERNAME,
    admin_password_from_environment as _admin_password_from_environment,
    network_rules as _network_rules,
    new_user_record as _new_user_record,
    normalize_user_roles as _normalize_user_roles,
    provision_os_user as _provision_os_user,
    valid_password_record as _valid_password_record,
)
from .tenants.firewall import FirewallError, apply_firewall
from .tenants.uploads import (
    install_uploaded_file as _install_uploaded_file,
    list_upload_directories as _list_upload_directories,
)

LOGGER = logging.getLogger("dsm.supervisor")

USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")


class Supervisor:
    def __init__(self, config: SupervisorConfig | None = None) -> None:
        self.config = config or SupervisorConfig.from_environment()
        self.tenants = TenantManager(
            users_root=self.config.users_root,
            harness_cli=self.config.harness_cli,
        )
        self.registry_lock = asyncio.Lock()
        self.fatal = asyncio.Event()
        self.control = ControlServer(
            owner=self,
            gateway_user=self.config.gateway_user,
            fatal=self.fatal,
            logger=LOGGER,
        )
        self.gateway: asyncio.subprocess.Process | None = None
        self.background_tasks: set[asyncio.Task[None]] = set()
        self.plugins = SharedPluginManager(
            database_path=self.config.registry_path,
            state_root=self.config.state_root,
            users_root=self.config.users_root,
            harness_cli=self.config.harness_cli,
            restart_tenant=self.tenants.restart,
        )

    def _schedule_plugin_reconcile(self, username: str) -> None:
        async def reconcile() -> None:
            if not await self.tenants.wait_ready(username):
                LOGGER.error("tenant %s was not ready for plugin reconciliation", username)
                return
            try:
                await self.plugins.reconcile_user(username)
            except Exception:
                LOGGER.exception("failed to reconcile plugins for %s", username)

        task = asyncio.create_task(reconcile(), name=f"plugins-{username}")
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def create_user(self, username: str, password: dict[str, Any]) -> dict[str, Any]:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError("invalid username")
        if not _valid_password_record(password):
            raise ValueError("invalid password record")

        async with self.registry_lock:
            registry = load_user_registry(self.config)
            if username in registry["users"]:
                raise FileExistsError("username already exists")
            user = _new_user_record(registry, username, password)
            _provision_os_user(user, self.config.users_root)
            candidate = {"version": 1, "users": {**registry["users"], username: user}}
            apply_firewall(
                _network_rules(candidate),
                gateway_port=self.config.gateway_port,
            )
            try:
                add_user(self.config.registry_path, user)
            except RegistryError as error:
                raise RuntimeError("unable to persist user") from error
            self.tenants.start(user)

        ready = await self.tenants.wait_ready(username)
        if ready:
            apply_plugin_defaults(self.config.registry_path, username)
            self._schedule_plugin_reconcile(username)
        return {
            "username": username,
            "password_version": 1,
            "role": user["role"],
            "ready": ready,
        }

    async def import_online_plugin(
        self, package_name: str, requested_version: str, created_by: str
    ) -> dict[str, Any]:
        package_name = validate_package_name(package_name)
        requested_version = validate_requested_version(requested_version)
        job_id = secrets.token_hex(16)
        create_job(
            self.config.registry_path,
            job_id=job_id,
            kind="online_import",
            created_by=created_by,
            package_name=package_name,
            requested_version=requested_version,
        )
        await self.plugins.enqueue(job_id)
        return {"job_id": job_id, "status": "queued"}

    async def discover_plugin_versions(self, package_name: str) -> dict[str, Any]:
        return await self.plugins.discover_versions(package_name)

    async def import_offline_plugin(
        self, spool_name: str, created_by: str
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}\.plugin\.tgz", spool_name):
            raise ValueError("无效的离线插件上传文件")
        if not (self.config.upload_root / spool_name).is_file():
            raise ValueError("离线插件上传文件不存在")
        job_id = secrets.token_hex(16)
        create_job(
            self.config.registry_path,
            job_id=job_id,
            kind="offline_import",
            created_by=created_by,
            scope={"spoolName": spool_name},
        )
        await self.plugins.enqueue(job_id)
        return {"job_id": job_id, "status": "queued"}

    async def assign_plugin(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("plugin_action", ""))
        if action not in {"install", "uninstall", "enable", "disable"}:
            raise ValueError("无效的插件操作")
        plugin_id = int(request.get("plugin_id", 0))
        version_id_value = request.get("plugin_version_id")
        version_id = int(version_id_value) if version_id_value is not None else None
        if action != "uninstall" and version_id is None:
            raise ValueError("请选择插件版本")
        if version_id is not None:
            version = get_plugin_version(self.config.registry_path, version_id)
            if version is None or int(version["plugin_id"]) != plugin_id:
                raise ValueError("插件版本不存在")

        scope = request.get("scope")
        if not isinstance(scope, dict):
            raise ValueError("无效的用户范围")
        registry = load_user_registry(self.config)
        scope_type = str(scope.get("type", ""))
        if scope_type == "all":
            include_disabled = bool(scope.get("includeDisabledUsers", False))
            usernames = [
                username
                for username, user in registry["users"].items()
                if include_disabled or user.get("disabled") is not True
            ]
        elif scope_type == "users":
            values = scope.get("usernames")
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError("无效的用户列表")
            usernames = sorted(set(values))
            if any(username not in registry["users"] for username in usernames):
                raise ValueError("用户列表中包含不存在的用户")
        else:
            raise ValueError("无效的用户范围")
        if not usernames:
            raise ValueError("没有符合条件的用户")

        job_id = secrets.token_hex(16)
        persisted_scope = {
            "type": scope_type,
            "includeDisabledUsers": bool(scope.get("includeDisabledUsers", False)),
            "enabledAfterInstall": bool(request.get("enabled_after_install", True)),
            "applyToFutureUsers": bool(request.get("apply_to_future_users", False)),
        }
        create_job(
            self.config.registry_path,
            job_id=job_id,
            kind="assign",
            created_by=str(request.get("created_by", "admin")),
            action=action,
            plugin_id=plugin_id,
            plugin_version_id=version_id,
            scope=persisted_scope,
            usernames=usernames,
        )
        await self.plugins.enqueue(job_id)
        return {"job_id": job_id, "status": "queued", "target_count": len(usernames)}

    async def install_upload(
        self,
        username: str,
        spool_name: str,
        directory: str,
        filename: str,
        size: int,
    ) -> dict[str, Any]:
        try:
            user = get_user(self.config.registry_path, username)
        except RegistryError as error:
            raise RuntimeError("unable to read user database") from error
        if not isinstance(user, dict) or user.get("disabled") is True:
            raise ValueError("用户不存在或已停用")
        return await asyncio.to_thread(
            _install_uploaded_file,
            user,
            spool_name,
            directory,
            filename,
            size,
            users_root=self.config.users_root,
            upload_root=self.config.upload_root,
            gateway_user=self.config.gateway_user,
            max_upload_bytes=self.config.max_upload_bytes,
        )

    async def list_upload_directories(
        self,
        username: str,
        directory: str,
    ) -> dict[str, Any]:
        try:
            user = get_user(self.config.registry_path, username)
        except RegistryError as error:
            raise RuntimeError("unable to read user database") from error
        if not isinstance(user, dict) or user.get("disabled") is True:
            raise ValueError("用户不存在或已停用")
        return await asyncio.to_thread(
            _list_upload_directories,
            user,
            directory,
            users_root=self.config.users_root,
        )

    async def start(self) -> None:
        initialize_gateway_state(self.config, LOGGER)
        registry = load_user_registry(self.config)
        registry_changed = _normalize_user_roles(registry)
        if ADMIN_USERNAME not in registry["users"]:
            password = _admin_password_from_environment()
            password_record = hash_password(password)
            admin = _new_user_record(registry, ADMIN_USERNAME, password_record)
            registry = {
                "version": 1,
                "users": {**registry["users"], ADMIN_USERNAME: admin},
            }
            registry_changed = True
            LOGGER.info("created bootstrap administrator account %s", ADMIN_USERNAME)
        for user in registry["users"].values():
            _provision_os_user(user, self.config.users_root)
        apply_firewall(
            _network_rules(registry),
            gateway_port=self.config.gateway_port,
        )
        if registry_changed:
            try:
                replace_registry(self.config.registry_path, registry)
            except RegistryError as error:
                raise RuntimeError("unable to persist user database") from error

        self.plugins.initialize()
        for user in registry["users"].values():
            self.plugins.prepare_user_profile(user)
        await self.plugins.start(unfinished_job_ids(self.config.registry_path))

        await self.control.start(
            self.config.runtime_root,
            self.config.control_socket,
        )

        for user in registry["users"].values():
            if user.get("disabled") is not True:
                self.tenants.start(user)
                self._schedule_plugin_reconcile(str(user["username"]))

        self.gateway = await start_gateway(self.config)
        LOGGER.info("gateway listening on 0.0.0.0:%s", self.config.gateway_port)

    async def stop(self) -> None:
        await self.control.stop(self.config.control_socket)
        await stop_gateway(self.gateway)
        await self.plugins.stop()
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await self.tenants.stop()

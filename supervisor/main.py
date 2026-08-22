from __future__ import annotations

import asyncio
import logging
import os
import pwd
import re
import secrets
import signal
import stat
from pathlib import Path
from typing import Any

from common.storage.assignments import apply_plugin_defaults
from common.storage.plugin_catalog import get_plugin_version
from common.storage.plugin_jobs import create_job, unfinished_job_ids
from common.storage.users import (
    RegistryError,
    add_user,
    get_user,
    initialize_registry,
    load_registry,
    replace_registry,
)
from common.security import hash_password

from .firewall import apply_firewall
from .ipc_server import ControlServer
from .plugins import SharedPluginManager, validate_package_name, validate_requested_version
from .tenants import TenantManager
from .users import (
    admin_password_from_environment as _admin_password_from_environment,
    network_rules as _network_rules,
    new_user_record as _new_user_record,
    normalize_user_roles as _normalize_user_roles,
    provision_os_user as _provision_os_user,
    valid_password_record as _valid_password_record,
)
from .uploads import (
    install_uploaded_file as _install_uploaded_file,
    list_upload_directories as _list_upload_directories,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dsh.supervisor")

STATE_ROOT = Path(os.environ.get("DSH_STATE_ROOT", "/var/lib/dsh"))
GATEWAY_ROOT = STATE_ROOT / "gateway"
USERS_ROOT = STATE_ROOT / "users"
REGISTRY_PATH = GATEWAY_ROOT / "users.db"
LEGACY_REGISTRY_PATH = GATEWAY_ROOT / "users.json"
SECRET_PATH = GATEWAY_ROOT / "session-secret"
UPLOAD_ROOT = GATEWAY_ROOT / "uploads"
RUNTIME_ROOT = Path("/run/dsh")
CONTROL_SOCKET = RUNTIME_ROOT / "control.sock"
GATEWAY_USER = "dsh-gateway"
GATEWAY_PORT = 8080
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
HARNESS_CLI = "/opt/deepseek-harness/apps/cli/lib/bin.js"
UPLOAD_SPOOL_PATTERN = re.compile(r"^[0-9a-f]{32}\.upload$")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
ADMIN_USERNAME = "admin"


def _load_registry() -> dict[str, Any]:
    try:
        return load_registry(REGISTRY_PATH)
    except RegistryError as error:
        raise RuntimeError("invalid user database") from error


def _ensure_gateway_state() -> None:
    gateway = pwd.getpwnam(GATEWAY_USER)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    # A bind-mounted host directory belongs to the host user. Preserve that
    # ownership and its existing read bits; the gateway only needs traversal.
    state_mode = stat.S_IMODE(STATE_ROOT.stat().st_mode)
    os.chmod(STATE_ROOT, state_mode | stat.S_IXOTH)
    GATEWAY_ROOT.mkdir(parents=True, exist_ok=True)
    os.chown(GATEWAY_ROOT, 0, gateway.pw_gid)
    os.chmod(GATEWAY_ROOT, 0o750)
    USERS_ROOT.mkdir(parents=True, exist_ok=True)
    os.chown(USERS_ROOT, 0, 0)
    os.chmod(USERS_ROOT, 0o755)

    migrated = initialize_registry(REGISTRY_PATH, LEGACY_REGISTRY_PATH)
    if migrated:
        LOGGER.info("migrated legacy user registry from %s", LEGACY_REGISTRY_PATH)
    os.chown(REGISTRY_PATH, 0, gateway.pw_gid)
    os.chmod(REGISTRY_PATH, 0o640)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_urlsafe(48), encoding="ascii")
    os.chown(SECRET_PATH, 0, gateway.pw_gid)
    os.chmod(SECRET_PATH, 0o640)
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    os.chown(UPLOAD_ROOT, gateway.pw_uid, gateway.pw_gid)
    os.chmod(UPLOAD_ROOT, 0o700)
    for entry in UPLOAD_ROOT.iterdir():
        try:
            if re.fullmatch(r"[0-9a-f]{32}\.plugin\.tgz", entry.name):
                continue
            if entry.is_file() or entry.is_symlink():
                entry.unlink()
        except FileNotFoundError:
            pass


class Supervisor:
    def __init__(self) -> None:
        self.tenants = TenantManager()
        self.registry_lock = asyncio.Lock()
        self.fatal = asyncio.Event()
        self.control = ControlServer(
            owner=self,
            gateway_user=GATEWAY_USER,
            fatal=self.fatal,
            logger=LOGGER,
        )
        self.gateway: asyncio.subprocess.Process | None = None
        self.background_tasks: set[asyncio.Task[None]] = set()
        self.plugins = SharedPluginManager(
            database_path=REGISTRY_PATH,
            state_root=STATE_ROOT,
            users_root=USERS_ROOT,
            harness_cli=HARNESS_CLI,
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
            registry = _load_registry()
            if username in registry["users"]:
                raise FileExistsError("username already exists")
            user = _new_user_record(registry, username, password)
            _provision_os_user(user)
            candidate = {"version": 1, "users": {**registry["users"], username: user}}
            apply_firewall(_network_rules(candidate), gateway_port=GATEWAY_PORT)
            try:
                add_user(REGISTRY_PATH, user)
            except RegistryError as error:
                raise RuntimeError("unable to persist user") from error
            self.tenants.start(user)

        ready = await self.tenants.wait_ready(username)
        if ready:
            apply_plugin_defaults(REGISTRY_PATH, username)
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
            REGISTRY_PATH,
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
        if not (UPLOAD_ROOT / spool_name).is_file():
            raise ValueError("离线插件上传文件不存在")
        job_id = secrets.token_hex(16)
        create_job(
            REGISTRY_PATH,
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
            version = get_plugin_version(REGISTRY_PATH, version_id)
            if version is None or int(version["plugin_id"]) != plugin_id:
                raise ValueError("插件版本不存在")

        scope = request.get("scope")
        if not isinstance(scope, dict):
            raise ValueError("无效的用户范围")
        registry = _load_registry()
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
            REGISTRY_PATH,
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
            user = get_user(REGISTRY_PATH, username)
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
        )

    async def list_upload_directories(
        self,
        username: str,
        directory: str,
    ) -> dict[str, Any]:
        try:
            user = get_user(REGISTRY_PATH, username)
        except RegistryError as error:
            raise RuntimeError("unable to read user database") from error
        if not isinstance(user, dict) or user.get("disabled") is True:
            raise ValueError("用户不存在或已停用")
        return await asyncio.to_thread(_list_upload_directories, user, directory)

    async def start(self) -> None:
        _ensure_gateway_state()
        registry = _load_registry()
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
            _provision_os_user(user)
        apply_firewall(_network_rules(registry), gateway_port=GATEWAY_PORT)
        if registry_changed:
            try:
                replace_registry(REGISTRY_PATH, registry)
            except RegistryError as error:
                raise RuntimeError("unable to persist user database") from error

        self.plugins.initialize()
        for user in registry["users"].values():
            self.plugins.prepare_user_profile(user)
        await self.plugins.start(unfinished_job_ids(REGISTRY_PATH))

        await self.control.start(RUNTIME_ROOT, CONTROL_SOCKET)

        for user in registry["users"].values():
            if user.get("disabled") is not True:
                self.tenants.start(user)
                self._schedule_plugin_reconcile(str(user["username"]))

        self.gateway = await asyncio.create_subprocess_exec(
            "runuser", "-u", GATEWAY_USER, "--",
            "env", "-i",
            "PYTHONPATH=/opt/dsh-lan-deployment",
            f"DSH_STATE_ROOT={STATE_ROOT}",
            f"DSH_CONTROL_SOCKET={CONTROL_SOCKET}",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "/opt/venv/bin/python",
            "-m", "uvicorn",
            "gateway.main:app",
            "--host", "0.0.0.0",
            "--port", str(GATEWAY_PORT),
            "--workers", "1",
            "--no-access-log",
            cwd="/opt/dsh-lan-deployment",
            start_new_session=True,
        )
        LOGGER.info("gateway listening on 0.0.0.0:%s", GATEWAY_PORT)

    async def stop(self) -> None:
        await self.control.stop(CONTROL_SOCKET)
        if self.gateway and self.gateway.returncode is None:
            try:
                os.killpg(self.gateway.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.gateway.wait(), timeout=8)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.gateway.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        await self.plugins.stop()
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await self.tenants.stop()


async def main() -> int:
    if os.geteuid() != 0:
        LOGGER.error("supervisor must run as root")
        return 1
    supervisor = Supervisor()
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stopped.set)
    try:
        await supervisor.start()
        assert supervisor.gateway is not None
        gateway_exit = asyncio.create_task(supervisor.gateway.wait())
        stop_wait = asyncio.create_task(stopped.wait())
        fatal_wait = asyncio.create_task(supervisor.fatal.wait())
        done, pending = await asyncio.wait(
            {gateway_exit, stop_wait, fatal_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if gateway_exit in done and not stopped.is_set():
            LOGGER.error("gateway exited unexpectedly with status %s", gateway_exit.result())
            return int(gateway_exit.result() or 1)
        if fatal_wait in done:
            return 1
        return 0
    except FirewallError:
        LOGGER.exception("tenant network isolation is unavailable")
        return 1
    except RuntimeError as error:
        LOGGER.error("startup failed: %s", error)
        return 1
    finally:
        await supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

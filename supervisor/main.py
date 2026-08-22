from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import pwd
import re
import secrets
import signal
import socket
import stat
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from common.storage.users import (
    RegistryError,
    add_user,
    get_user,
    initialize_registry,
    load_registry,
    replace_registry,
)
from gateway.auth import hash_password
from gateway.plugins import (
    apply_plugin_defaults,
    create_job,
    get_plugin_version,
    unfinished_job_ids,
)

from .firewall import FirewallError, TenantNetwork, apply_firewall
from .plugins import (
    PluginOperationError,
    SharedPluginManager,
    validate_package_name,
    validate_requested_version,
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
FIRST_UID = 20000
FIRST_PORT = 3101
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
HARNESS_CLI = "/opt/deepseek-harness/apps/cli/lib/bin.js"
UPLOAD_SPOOL_PATTERN = re.compile(r"^[0-9a-f]{32}\.upload$")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_ENV = "DSH_ADMIN_PASSWORD"


def _load_registry() -> dict[str, Any]:
    try:
        return load_registry(REGISTRY_PATH)
    except RegistryError as error:
        raise RuntimeError("invalid user database") from error


def _normalize_user_roles(registry: dict[str, Any]) -> bool:
    users = registry["users"]
    changed = False
    for username, user in users.items():
        if not isinstance(user, dict):
            raise RuntimeError("invalid user registry entry")
        expected = "admin" if username == ADMIN_USERNAME else "user"
        if user.get("role") != expected:
            user["role"] = expected
            changed = True
    return changed


def _admin_password_from_environment() -> str:
    password = os.environ.get(ADMIN_PASSWORD_ENV)
    if password is None:
        raise RuntimeError(
            f"{ADMIN_PASSWORD_ENV} is required when the admin account does not exist"
        )
    if not 8 <= len(password) <= 256:
        raise RuntimeError(f"{ADMIN_PASSWORD_ENV} must contain 8 to 256 characters")
    return password


def _new_user_record(
    registry: dict[str, Any],
    username: str,
    password: dict[str, Any],
) -> dict[str, Any]:
    used_uids = {entry.pw_uid for entry in pwd.getpwall()}
    used_uids.update(int(user["uid"]) for user in registry["users"].values())
    used_ports = {int(user["port"]) for user in registry["users"].values()}
    uid = next(value for value in range(FIRST_UID, 60000) if value not in used_uids)
    port = next(value for value in range(FIRST_PORT, 65536) if value not in used_ports)
    return {
        "username": username,
        "os_user": f"dsh-{username}",
        "uid": uid,
        "port": port,
        "role": "admin" if username == ADMIN_USERNAME else "user",
        "password": password,
        "password_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "disabled": False,
    }


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


def _provision_os_user(user: dict[str, Any]) -> None:
    username = str(user["os_user"])
    uid = int(user["uid"])
    tenant_root = USERS_ROOT / str(user["username"])
    home = tenant_root / "home"
    workspace = home / "workspace"
    state = tenant_root / "state"

    try:
        existing = pwd.getpwnam(username)
        if existing.pw_uid != uid:
            raise RuntimeError(f"OS user {username} has unexpected UID")
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--uid", str(uid),
                "--user-group",
                "--home-dir", str(home),
                "--no-create-home",
                "--shell", "/usr/sbin/nologin",
                username,
            ],
            check=True,
        )

    group_id = pwd.getpwnam(username).pw_gid
    tenant_root.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    os.chown(tenant_root, 0, 0)
    os.chmod(tenant_root, 0o711)
    for directory in (home, workspace, state):
        os.chown(directory, uid, group_id)
        os.chmod(directory, 0o700)


def _network_rules(registry: dict[str, Any]) -> list[TenantNetwork]:
    return [
        TenantNetwork(uid=int(user["uid"]), port=int(user["port"]))
        for user in registry["users"].values()
        if user.get("disabled") is not True
    ]


def _valid_password_record(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("algorithm") != "scrypt":
        return False
    try:
        return (
            len(str(value["salt"])) >= 16
            and len(str(value["hash"])) >= 32
            and int(value["n"]) == 1 << 14
            and int(value["r"]) == 8
            and int(value["p"]) == 1
            and int(value["dklen"]) == 64
        )
    except (KeyError, TypeError, ValueError):
        return False


def _upload_directory_parts(value: str) -> tuple[str, ...]:
    if value in {"", "."}:
        return ()
    if len(value) > 512 or "\\" in value:
        raise ValueError("无效的目标目录")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("目标目录必须相对于 workspace")
    parts = path.parts
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ValueError("目标目录不能包含隐藏目录、. 或 ..")
    if any(len(part.encode("utf-8")) > 255 for part in parts):
        raise ValueError("目标目录名称过长")
    return parts


def _validate_upload_filename(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or value.startswith(".")
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or len(value.encode("utf-8")) > 255
    ):
        raise ValueError("无效的文件名")
    return value


def _open_owned_directory(
    parent_fd: int,
    name: str,
    uid: int,
    gid: int,
    create: bool = True,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise ValueError("目标目录不存在")
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.chown(name, uid, gid, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("目标目录不是安全的普通目录") from error
        raise
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid:
        os.close(descriptor)
        raise ValueError("目标目录不属于当前用户")
    return descriptor


def _list_upload_directories(
    user: dict[str, Any],
    directory: str,
) -> dict[str, Any]:
    parts = _upload_directory_parts(directory)
    uid = int(user["uid"])
    gid = pwd.getpwnam(str(user["os_user"])).pw_gid
    tenant_root = USERS_ROOT / str(user["username"])
    workspace_fd = -1
    target_fd = -1
    root_fd = os.open(
        tenant_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        home_fd = _open_owned_directory(root_fd, "home", uid, gid, create=False)
    finally:
        os.close(root_fd)
    try:
        workspace_fd = _open_owned_directory(
            home_fd,
            "workspace",
            uid,
            gid,
            create=False,
        )
    finally:
        os.close(home_fd)

    try:
        directory_fd = workspace_fd
        target_fd = directory_fd
        for part in parts:
            next_fd = _open_owned_directory(
                directory_fd,
                part,
                uid,
                gid,
                create=False,
            )
            if directory_fd != workspace_fd:
                os.close(directory_fd)
            directory_fd = next_fd
            target_fd = directory_fd

        names: list[str] = []
        with os.scandir(target_fd) as entries:
            for entry in entries:
                if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if metadata.st_uid == uid:
                    names.append(entry.name)
        names.sort(key=str.casefold)
        truncated = len(names) > 2000
        if truncated:
            names = names[:2000]
    finally:
        if target_fd >= 0 and target_fd != workspace_fd:
            os.close(target_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)

    return {
        "directory": "." if not parts else "/".join(parts),
        "directories": names,
        "truncated": truncated,
    }


def _install_uploaded_file(
    user: dict[str, Any],
    spool_name: str,
    directory: str,
    filename: str,
    expected_size: int,
) -> dict[str, Any]:
    if not UPLOAD_SPOOL_PATTERN.fullmatch(spool_name):
        raise ValueError("无效的上传临时文件")
    if expected_size < 0 or expected_size > MAX_UPLOAD_BYTES:
        raise ValueError("上传文件大小无效")
    parts = _upload_directory_parts(directory)
    filename = _validate_upload_filename(filename)
    uid = int(user["uid"])
    gid = pwd.getpwnam(str(user["os_user"])).pw_gid
    gateway_uid = pwd.getpwnam(GATEWAY_USER).pw_uid

    source_path = UPLOAD_ROOT / spool_name
    source_fd = os.open(
        source_path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    workspace_fd = -1
    target_fd = -1
    temporary_name = f".upload-{secrets.token_hex(12)}.tmp"
    try:
        source_metadata = os.fstat(source_fd)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or source_metadata.st_uid != gateway_uid
            or source_metadata.st_size != expected_size
        ):
            raise ValueError("上传临时文件校验失败")

        tenant_root = USERS_ROOT / str(user["username"])
        root_fd = os.open(
            tenant_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            home_fd = _open_owned_directory(root_fd, "home", uid, gid)
        finally:
            os.close(root_fd)
        try:
            workspace_fd = _open_owned_directory(home_fd, "workspace", uid, gid)
        finally:
            os.close(home_fd)

        directory_fd = workspace_fd
        target_fd = directory_fd
        for part in parts:
            next_fd = _open_owned_directory(directory_fd, part, uid, gid)
            if directory_fd != workspace_fd:
                os.close(directory_fd)
            directory_fd = next_fd
            target_fd = directory_fd

        destination_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=target_fd,
        )
        try:
            while True:
                chunk = os.read(source_fd, 64 * 1024)
                if not chunk:
                    break
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(destination_fd, remaining)
                    if written == 0:
                        raise OSError("failed to write uploaded file")
                    remaining = remaining[written:]
            os.fchmod(destination_fd, 0o600)
            os.fchown(destination_fd, uid, gid)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=target_fd,
            dst_dir_fd=target_fd,
        )
        os.fsync(target_fd)
    except BaseException:
        if target_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=target_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if target_fd >= 0 and target_fd != workspace_fd:
            os.close(target_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)
        os.close(source_fd)
        source_path.unlink(missing_ok=True)

    normalized_directory = "." if not parts else "/".join(parts)
    return {
        "directory": normalized_directory,
        "filename": filename,
        "size": expected_size,
    }


async def _http_ready(port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=1
        )
        writer.write(
            f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), timeout=2)
        writer.close()
        await writer.wait_closed()
        return status.startswith(b"HTTP/1.1 200") or status.startswith(b"HTTP/1.0 200")
    except (OSError, asyncio.TimeoutError):
        return False


class TenantManager:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.ready: dict[str, asyncio.Event] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.restart_requests: set[str] = set()
        self.stopping = False

    def start(self, user: dict[str, Any]) -> None:
        username = str(user["username"])
        if username in self.tasks:
            return
        self.ready[username] = asyncio.Event()
        self.tasks[username] = asyncio.create_task(
            self._run(user), name=f"tenant-{username}"
        )

    async def wait_ready(self, username: str, timeout: float = 120) -> bool:
        try:
            await asyncio.wait_for(self.ready[username].wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self, user: dict[str, Any]) -> None:
        username = str(user["username"])
        os_user = str(user["os_user"])
        port = int(user["port"])
        tenant_root = USERS_ROOT / username
        home = tenant_root / "home"
        workspace = home / "workspace"
        state = tenant_root / "state"
        delay = 1

        while not self.stopping:
            LOGGER.info("starting DSH tenant %s on loopback port %s", username, port)
            process = await asyncio.create_subprocess_exec(
                "runuser", "-u", os_user, "--",
                "env", "-i",
                f"HOME={home}",
                f"DSH_HOME={state}",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "/usr/local/bin/node",
                HARNESS_CLI,
                "web",
                "--no-open",
                "--port", str(port),
                cwd=workspace,
                start_new_session=True,
            )
            self.processes[username] = process

            try:
                for _ in range(120):
                    if process.returncode is not None or self.stopping:
                        break
                    if await _http_ready(port):
                        self.ready[username].set()
                        delay = 1
                        break
                    await asyncio.sleep(1)

                code = await process.wait()
            except asyncio.CancelledError:
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=8)
                    except asyncio.TimeoutError:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        await process.wait()
                raise
            finally:
                self.processes.pop(username, None)
            self.ready[username].clear()
            if self.stopping:
                return
            if username in self.restart_requests:
                self.restart_requests.discard(username)
                delay = 1
                continue
            LOGGER.error("DSH tenant %s exited with status %s; restarting", username, code)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def restart(self, username: str) -> bool:
        process = self.processes.get(username)
        ready = self.ready.get(username)
        if process is None or ready is None or process.returncode is not None:
            return False
        ready.clear()
        self.restart_requests.add(username)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            self.restart_requests.discard(username)
            return False
        return await self.wait_ready(username)

    async def stop(self) -> None:
        self.stopping = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


class Supervisor:
    def __init__(self) -> None:
        self.tenants = TenantManager()
        self.registry_lock = asyncio.Lock()
        self.fatal = asyncio.Event()
        self.server: asyncio.AbstractServer | None = None
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

    def _authorized_peer(self, writer: asyncio.StreamWriter) -> bool:
        transport_socket = writer.get_extra_info("socket")
        if transport_socket is None or not hasattr(socket, "SO_PEERCRED"):
            return False
        credentials = transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("3i", credentials)
        return uid == pwd.getpwnam(GATEWAY_USER).pw_uid

    async def handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            if not self._authorized_peer(writer):
                response = {"ok": False, "code": "forbidden", "message": "forbidden"}
            else:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                request = json.loads(line)
                action = request.get("action")
                try:
                    if action == "create_user":
                        result = await self.create_user(
                            str(request.get("username", "")), request.get("password")
                        )
                    elif action == "install_upload":
                        result = await self.install_upload(
                            str(request.get("username", "")),
                            str(request.get("spool_name", "")),
                            str(request.get("directory", "")),
                            str(request.get("filename", "")),
                            int(request.get("size", -1)),
                        )
                    elif action == "list_upload_directories":
                        result = await self.list_upload_directories(
                            str(request.get("username", "")),
                            str(request.get("directory", ".")),
                        )
                    elif action == "plugin_online_import":
                        result = await self.import_online_plugin(
                            str(request.get("package_name", "")),
                            str(request.get("requested_version", "latest")),
                            str(request.get("created_by", "admin")),
                        )
                    elif action == "plugin_discover_versions":
                        result = await self.discover_plugin_versions(
                            str(request.get("package_name", ""))
                        )
                    elif action == "plugin_offline_import":
                        result = await self.import_offline_plugin(
                            str(request.get("spool_name", "")),
                            str(request.get("created_by", "admin")),
                        )
                    elif action == "plugin_assignment":
                        result = await self.assign_plugin(request)
                    else:
                        raise ValueError("unknown action")
                    response = {"ok": True, **result}
                except FileExistsError:
                    response = {
                        "ok": False,
                        "code": "conflict",
                        "message": "用户名已存在",
                    }
                except (TypeError, ValueError, PluginOperationError) as error:
                    response = {
                        "ok": False,
                        "code": "bad_request",
                        "message": str(error),
                    }
                except FirewallError:
                    LOGGER.exception("firewall update failed; stopping container")
                    self.fatal.set()
                    response = {
                        "ok": False,
                        "code": "supervisor_error",
                        "message": "租户隔离更新失败",
                    }
                except Exception:
                    LOGGER.exception("control action %s failed", action)
                    response = {
                        "ok": False,
                        "code": "supervisor_error",
                        "message": "操作失败",
                    }
        except (asyncio.TimeoutError, json.JSONDecodeError):
            response = {"ok": False, "code": "bad_request", "message": "invalid request"}
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

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

        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        gateway_account = pwd.getpwnam(GATEWAY_USER)
        os.chown(RUNTIME_ROOT, 0, gateway_account.pw_gid)
        os.chmod(RUNTIME_ROOT, 0o770)
        CONTROL_SOCKET.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self.handle_control, CONTROL_SOCKET)
        os.chown(CONTROL_SOCKET, 0, gateway_account.pw_gid)
        os.chmod(CONTROL_SOCKET, 0o660)

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
        if self.server:
            self.server.close()
            await self.server.wait_closed()
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
        CONTROL_SOCKET.unlink(missing_ok=True)


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

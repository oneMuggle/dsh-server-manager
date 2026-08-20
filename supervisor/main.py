from __future__ import annotations

import asyncio
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
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .firewall import FirewallError, TenantNetwork, apply_firewall

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dsh.supervisor")

STATE_ROOT = Path(os.environ.get("DSH_STATE_ROOT", "/var/lib/dsh"))
GATEWAY_ROOT = STATE_ROOT / "gateway"
USERS_ROOT = STATE_ROOT / "users"
REGISTRY_PATH = GATEWAY_ROOT / "users.json"
SECRET_PATH = GATEWAY_ROOT / "session-secret"
RUNTIME_ROOT = Path("/run/dsh")
CONTROL_SOCKET = RUNTIME_ROOT / "control.sock"
GATEWAY_USER = "dsh-gateway"
GATEWAY_PORT = 8080
FIRST_UID = 20000
FIRST_PORT = 3101
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
HARNESS_CLI = "/opt/deepseek-harness/apps/cli/lib/bin.js"


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o640)
        gateway = pwd.getpwnam(GATEWAY_USER)
        os.chown(temporary_name, 0, gateway.pw_gid)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _load_registry() -> dict[str, Any]:
    try:
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "users": {}}
    if value.get("version") != 1 or not isinstance(value.get("users"), dict):
        raise RuntimeError("invalid user registry")
    return value


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

    if not REGISTRY_PATH.exists():
        _atomic_json_write(REGISTRY_PATH, {"version": 1, "users": {}})
    else:
        os.chown(REGISTRY_PATH, 0, gateway.pw_gid)
        os.chmod(REGISTRY_PATH, 0o640)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_urlsafe(48), encoding="ascii")
    os.chown(SECRET_PATH, 0, gateway.pw_gid)
    os.chmod(SECRET_PATH, 0o640)


def _provision_os_user(user: dict[str, Any]) -> None:
    username = str(user["os_user"])
    uid = int(user["uid"])
    tenant_root = USERS_ROOT / str(user["username"])
    home = tenant_root / "home"
    workspace = tenant_root / "workspace"

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
    for directory in (tenant_root, home, workspace):
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
        workspace = tenant_root / "workspace"
        delay = 1

        while not self.stopping:
            LOGGER.info("starting DSH tenant %s on loopback port %s", username, port)
            process = await asyncio.create_subprocess_exec(
                "runuser", "-u", os_user, "--",
                "env", "-i",
                f"HOME={home}",
                f"DSH_HOME={home}",
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
            LOGGER.error("DSH tenant %s exited with status %s; restarting", username, code)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

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

    async def create_user(self, username: str, password: dict[str, Any]) -> dict[str, Any]:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError("invalid username")
        if not _valid_password_record(password):
            raise ValueError("invalid password record")

        async with self.registry_lock:
            registry = _load_registry()
            if username in registry["users"]:
                raise FileExistsError("username already exists")
            used_uids = {entry.pw_uid for entry in pwd.getpwall()}
            used_uids.update(int(user["uid"]) for user in registry["users"].values())
            used_ports = {int(user["port"]) for user in registry["users"].values()}
            uid = next(value for value in range(FIRST_UID, 60000) if value not in used_uids)
            port = next(value for value in range(FIRST_PORT, 65536) if value not in used_ports)
            user = {
                "username": username,
                "os_user": f"dsh-{username}",
                "uid": uid,
                "port": port,
                "password": password,
                "password_version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "disabled": False,
            }
            _provision_os_user(user)
            candidate = {"version": 1, "users": {**registry["users"], username: user}}
            apply_firewall(_network_rules(candidate), gateway_port=GATEWAY_PORT)
            _atomic_json_write(REGISTRY_PATH, candidate)
            self.tenants.start(user)

        ready = await self.tenants.wait_ready(username)
        return {
            "username": username,
            "password_version": 1,
            "ready": ready,
        }

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
                if request.get("action") != "create_user":
                    response = {"ok": False, "code": "bad_request", "message": "unknown action"}
                else:
                    try:
                        result = await self.create_user(
                            str(request.get("username", "")), request.get("password")
                        )
                        response = {"ok": True, **result}
                    except FileExistsError:
                        response = {
                            "ok": False,
                            "code": "conflict",
                            "message": "用户名已存在",
                        }
                    except ValueError as error:
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
                        LOGGER.exception("unable to create tenant")
                        response = {
                            "ok": False,
                            "code": "supervisor_error",
                            "message": "创建用户失败",
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
        for user in registry["users"].values():
            _provision_os_user(user)
        apply_firewall(_network_rules(registry), gateway_port=GATEWAY_PORT)

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

        self.gateway = await asyncio.create_subprocess_exec(
            "runuser", "-u", GATEWAY_USER, "--",
            "env",
            "PYTHONPATH=/opt/dsh-lan-deployment",
            f"DSH_STATE_ROOT={STATE_ROOT}",
            f"DSH_CONTROL_SOCKET={CONTROL_SOCKET}",
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
    finally:
        await supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

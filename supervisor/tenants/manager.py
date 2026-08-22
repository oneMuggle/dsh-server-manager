from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("dsm.supervisor")
async def http_ready(port: int) -> bool:
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
    def __init__(self, *, users_root: Path, harness_cli: str) -> None:
        self.users_root = users_root
        self.harness_cli = harness_cli
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
        tenant_root = self.users_root / username
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
                self.harness_cli,
                "web",
                "--patch", str(state / "dsm-builtins.patch.yml"),
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
                    if await http_ready(port):
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

    async def stop_user(self, username: str) -> bool:
        task = self.tasks.pop(username, None)
        if task is None:
            self.ready.pop(username, None)
            self.processes.pop(username, None)
            self.restart_requests.discard(username)
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.ready.pop(username, None)
        self.processes.pop(username, None)
        self.restart_requests.discard(username)
        return True

    async def stop(self) -> None:
        self.stopping = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

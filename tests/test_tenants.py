from __future__ import annotations

import asyncio
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from supervisor.tenants import TenantManager


class TenantManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_signals_the_running_tenant_process_group(self) -> None:
        class ReadyTenantManager(TenantManager):
            async def wait_ready(self, username: str, timeout: float = 120) -> bool:
                return username == "alice"

        manager = ReadyTenantManager(
            users_root=Path("/unused"),
            harness_cli="/unused/dsh",
        )
        manager.processes["alice"] = SimpleNamespace(returncode=None, pid=12345)
        manager.ready["alice"] = asyncio.Event()

        signals: list[tuple[int, signal.Signals]] = []
        with patch("os.killpg", side_effect=lambda pid, value: signals.append((pid, value))):
            restarted = await manager.restart("alice")

        self.assertTrue(restarted)
        self.assertEqual(signals, [(12345, signal.SIGTERM)])

    async def test_start_is_idempotent_for_the_same_user(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class WaitingTenantManager(TenantManager):
            async def _run(self, user: dict) -> None:
                entered.set()
                await release.wait()

        manager = WaitingTenantManager(
            users_root=Path("/unused"),
            harness_cli="/unused/dsh",
        )
        user = {"username": "alice", "os_user": "dsh-alice", "port": 3101}
        manager.start(user)
        manager.start(user)
        await entered.wait()

        self.assertEqual(list(manager.tasks), ["alice"])
        release.set()
        await manager.stop()

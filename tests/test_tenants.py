from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from supervisor.tenants import TenantManager


class TenantManagerTests(unittest.IsolatedAsyncioTestCase):
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

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from common.security import hash_password
from common.storage.users import add_user, get_user, initialize_registry, update_user
from gateway.config import Config
from supervisor.application import Supervisor
from supervisor.config import SupervisorConfig
from supervisor.gateway_process import gateway_command, start_gateway


def _stored_user(username: str, *, role: str = "user") -> dict:
    return {
        "username": username,
        "os_user": f"dsh-{username}",
        "uid": 20000 if username == "admin" else 20001,
        "port": 3101 if username == "admin" else 3102,
        "role": role,
        "password": hash_password("original-password"),
        "password_version": 1,
        "created_at": "2026-08-22T00:00:00+00:00",
        "disabled": False,
    }


class GatewayConfigTests(unittest.TestCase):
    def test_registration_and_cookie_flags_use_strict_booleans(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "DSM_REGISTRATION_ENABLED": "TrUe",
                "DSM_COOKIE_SECURE": "false",
            },
            clear=True,
        ):
            config = Config.from_environment()
        self.assertTrue(config.registration_enabled)
        self.assertFalse(config.cookie_secure)

        with patch.dict(
            "os.environ",
            {"DSM_REGISTRATION_ENABLED": "yes"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "DSM_REGISTRATION_ENABLED"):
                Config.from_environment()

    def test_registration_defaults_to_disabled(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(Config.from_environment().registration_enabled)


class SupervisorConfigTests(unittest.TestCase):
    def test_environment_derives_all_state_paths_from_one_root(self) -> None:
        with patch.dict(
            "os.environ",
            {"DSM_STATE_ROOT": "/srv/dsh-state"},
            clear=True,
        ):
            config = SupervisorConfig.from_environment()

        self.assertEqual(config.state_root, Path("/srv/dsh-state"))
        self.assertEqual(config.gateway_root, Path("/srv/dsh-state/gateway"))
        self.assertEqual(config.users_root, Path("/srv/dsh-state/users"))
        self.assertEqual(config.registry_path, Path("/srv/dsh-state/gateway/users.db"))
        self.assertEqual(config.upload_root, Path("/srv/dsh-state/gateway/uploads"))
        self.assertEqual(config.control_socket, Path("/run/dsh/control.sock"))
        self.assertEqual(
            getattr(config, "install_root", None),
            Path("/opt/dsh-server-manager"),
        )
        self.assertEqual(config.gateway_port, 8080)
        self.assertEqual(config.max_upload_bytes, 100 * 1024 * 1024)


class GatewayProcessTests(unittest.IsolatedAsyncioTestCase):
    def test_gateway_receives_only_dsm_manager_environment(self) -> None:
        arguments = gateway_command(SupervisorConfig.from_environment())
        self.assertIn("DSM_STATE_ROOT=/var/lib/dsh", arguments)
        self.assertIn("DSM_CONTROL_SOCKET=/run/dsh/control.sock", arguments)
        self.assertFalse(any(value.startswith("DSH_STATE_ROOT=") for value in arguments))

    async def test_gateway_starts_from_the_dsm_installation_root(self) -> None:
        config = SupervisorConfig.from_environment()
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def capture(
            *arguments: object,
            **options: object,
        ) -> object:
            calls.append((arguments, options))
            return object()

        with patch(
            "supervisor.gateway_process.asyncio.create_subprocess_exec",
            side_effect=capture,
        ):
            await start_gateway(config)

        arguments, options = calls[0]
        self.assertIn("PYTHONPATH=/opt/dsh-server-manager", arguments)
        self.assertEqual(options["cwd"], "/opt/dsh-server-manager")


class SupervisorUserUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "users.db"
        initialize_registry(database)
        add_user(database, _stored_user("admin", role="admin"))
        add_user(database, _stored_user("alice"))
        self.supervisor = Supervisor.__new__(Supervisor)
        self.supervisor.config = SimpleNamespace(
            registry_path=database,
            gateway_port=8080,
        )
        self.supervisor.registry_lock = asyncio.Lock()
        self.supervisor.tenants = SimpleNamespace(
            stop_user=AsyncMock(return_value=True),
            start=Mock(),
        )
        self.supervisor._schedule_plugin_reconcile = Mock()

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_password_reset_increments_version_and_disabling_stops_tenant(self) -> None:
        replacement = hash_password("replacement-password")
        with patch("supervisor.application.apply_firewall") as firewall:
            result = await self.supervisor.update_user(
                "alice", password=replacement, disabled=True
            )

        stored = get_user(self.supervisor.config.registry_path, "alice")
        self.assertEqual(stored["password"], replacement)
        self.assertEqual(stored["password_version"], 2)
        self.assertTrue(stored["disabled"])
        self.supervisor.tenants.stop_user.assert_awaited_once_with("alice")
        firewall.assert_called_once()
        self.assertEqual(result["password_version"], 2)

    async def test_enabling_starts_tenant_and_reconciles_plugins(self) -> None:
        alice = get_user(self.supervisor.config.registry_path, "alice")
        alice["disabled"] = True
        update_user(self.supervisor.config.registry_path, alice)

        with patch("supervisor.application.apply_firewall"):
            result = await self.supervisor.update_user("alice", disabled=False)

        self.assertFalse(result["disabled"])
        self.supervisor.tenants.start.assert_called_once()
        self.supervisor._schedule_plugin_reconcile.assert_called_once_with("alice")

    async def test_fixed_admin_cannot_be_disabled(self) -> None:
        with self.assertRaises(PermissionError):
            await self.supervisor.update_user("admin", disabled=True)
        self.supervisor.tenants.stop_user.assert_not_awaited()

    async def test_missing_user_is_reported(self) -> None:
        with self.assertRaises(LookupError):
            await self.supervisor.update_user("missing", disabled=True)


if __name__ == "__main__":
    unittest.main()

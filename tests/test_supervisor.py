from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from gateway.config import Config
from supervisor.config import SupervisorConfig
from supervisor.gateway_process import gateway_command, start_gateway


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


if __name__ == "__main__":
    unittest.main()

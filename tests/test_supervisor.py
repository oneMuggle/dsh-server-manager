from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from supervisor.config import SupervisorConfig


class SupervisorConfigTests(unittest.TestCase):
    def test_environment_derives_all_state_paths_from_one_root(self) -> None:
        with patch.dict(
            "os.environ",
            {"DSH_STATE_ROOT": "/srv/dsh-state"},
            clear=True,
        ):
            config = SupervisorConfig.from_environment()

        self.assertEqual(config.state_root, Path("/srv/dsh-state"))
        self.assertEqual(config.gateway_root, Path("/srv/dsh-state/gateway"))
        self.assertEqual(config.users_root, Path("/srv/dsh-state/users"))
        self.assertEqual(config.registry_path, Path("/srv/dsh-state/gateway/users.db"))
        self.assertEqual(config.upload_root, Path("/srv/dsh-state/gateway/uploads"))
        self.assertEqual(config.control_socket, Path("/run/dsh/control.sock"))
        self.assertEqual(config.gateway_port, 8080)
        self.assertEqual(config.max_upload_bytes, 100 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()

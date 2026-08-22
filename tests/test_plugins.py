from __future__ import annotations

import asyncio
import tempfile
import unittest
import os
import pwd
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gateway.plugin_ui import plugin_admin_page
from gateway.web import MANAGEMENT_STATIC_ROOT
from common.storage.assignments import (
    apply_plugin_defaults,
    set_plugin_default,
    set_user_plugin,
)
from common.storage.plugin_catalog import list_plugins, register_plugin_version
from common.storage.plugin_jobs import create_job, get_job
from common.storage.users import add_user, initialize_registry, replace_registry
from supervisor.plugins import PluginOperationError, SharedPluginManager
from supervisor.plugins.assignments import restore_user_assignment
from supervisor.plugins.commands import PluginCommandRunner
from supervisor.plugins.installer import installed_package
from supervisor.plugins.packages import SharedPackageRepository
from supervisor.plugins.profile import apply_profile
from supervisor.plugins.registry import (
    parse_version_metadata,
    validate_package_name,
    validate_requested_version,
)


def user(username: str, uid: int, port: int) -> dict:
    return {
        "username": username,
        "os_user": f"dsh-{username}",
        "uid": uid,
        "port": port,
        "role": "user",
        "password": {"algorithm": "test"},
        "password_version": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "disabled": False,
    }


class PluginStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "users.db"
        initialize_registry(self.database)
        add_user(self.database, user("alice", 20000, 3101))
        add_user(self.database, user("bob", 20001, 3102))
        self.plugin_id, self.version_id = register_plugin_version(
            self.database,
            package_name="@example/shared-plugin",
            version="1.2.3",
            source_type="offline",
            artifact_sha256="a" * 64,
            release_path="/var/lib/dsh/plugins/releases/example",
            resolver_alias="@dsh-managed/p-example-1.2.3",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_shared_version_and_per_user_state_are_reported(self) -> None:
        set_user_plugin(
            self.database, "alice", self.plugin_id, self.version_id, True
        )
        plugins = list_plugins(self.database)
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["packageName"], "@example/shared-plugin")
        self.assertEqual(plugins[0]["assignedUsers"], 1)
        self.assertEqual(plugins[0]["enabledUsers"], 1)
        self.assertEqual(plugins[0]["versions"][0]["id"], self.version_id)

    def test_default_policy_is_applied_without_copying_a_release(self) -> None:
        set_plugin_default(
            self.database,
            self.plugin_id,
            self.version_id,
            assigned=True,
            enabled=False,
        )
        self.assertEqual(apply_plugin_defaults(self.database, "bob"), 1)
        plugin = list_plugins(self.database)[0]
        bob = next(item for item in plugin["users"] if item["username"] == "bob")
        self.assertFalse(bob["enabled"])
        self.assertEqual(bob["state"], "pending")

    def test_user_registry_rewrite_preserves_plugin_assignments(self) -> None:
        set_user_plugin(
            self.database, "alice", self.plugin_id, self.version_id, True
        )
        replace_registry(
            self.database,
            {"version": 1, "users": {"alice": user("alice", 20000, 3101)}},
        )
        plugin = list_plugins(self.database)[0]
        self.assertEqual([item["username"] for item in plugin["users"]], ["alice"])

    def test_job_persists_target_snapshot_and_result(self) -> None:
        create_job(
            self.database,
            job_id="job-1",
            kind="assign",
            action="install",
            created_by="admin",
            plugin_id=self.plugin_id,
            plugin_version_id=self.version_id,
            scope={"type": "all"},
            usernames=["alice", "bob"],
        )
        job = get_job(self.database, "job-1")
        self.assertEqual(job["scope"], {"type": "all"})
        self.assertEqual([item["username"] for item in job["items"]], ["alice", "bob"])


class PluginInputTests(unittest.TestCase):
    def test_scoped_and_unscoped_npm_names_are_accepted(self) -> None:
        self.assertEqual(validate_package_name("@scope/my-plugin"), "@scope/my-plugin")
        self.assertEqual(validate_package_name("my-plugin"), "my-plugin")
        self.assertEqual(validate_requested_version("latest"), "latest")
        self.assertEqual(validate_requested_version("1.2.3-beta.1"), "1.2.3-beta.1")

    def test_shell_like_package_specs_are_rejected(self) -> None:
        for value in ("file:plugin.tgz", "name@1.0.0", "../plugin", "x;id"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_package_name(value)

    def test_registry_metadata_parser_deduplicates_and_keeps_latest(self) -> None:
        self.assertEqual(
            parse_version_metadata(
                {"versions": ["1.0.0", "1.0.0"], "version": "1.1.0"}
            ),
            {"versions": ["1.0.0", "1.1.0"], "latest": "1.1.0"},
        )

    def test_focused_plugin_runtime_helpers_are_public(self) -> None:
        self.assertTrue(callable(installed_package))
        self.assertTrue(callable(apply_profile))
        self.assertTrue(callable(restore_user_assignment))

    def test_admin_page_exposes_online_offline_and_batch_controls(self) -> None:
        page = plugin_admin_page()
        script = (MANAGEMENT_STATIC_ROOT / "plugins.js").read_text()
        self.assertIn("/api/gateway/v1/admin/plugins/registry-versions", script)
        self.assertIn("/api/gateway/v1/admin/plugins/online", script)
        self.assertIn("/api/gateway/v1/admin/plugins/offline", script)
        self.assertIn("/api/gateway/v1/admin/plugin-jobs", script)
        self.assertIn("所有启用用户", page)
        self.assertIn("退出登录", page)
        self.assertIn("/api/gateway/v1/auth/logout", script)
        self.assertIn("发现版本", page)
        self.assertIn("导入所选版本", page)


class PluginVersionDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_install_result_is_a_plugin_operation_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / "state"
            commands = PluginCommandRunner(state_root / "plugins")
            repository = SharedPackageRepository(
                database_path=root / "users.db",
                state_root=state_root,
                commands=commands,
            )
            staging = root / "staging"
            staging.mkdir()
            (staging / "package.json").write_text(
                '{"dependencies":{}}\n',
                encoding="utf-8",
            )

            with (
                patch.object(commands, "prepare_staging", return_value=staging),
                patch.object(commands, "pnpm_add", new=AsyncMock()),
                self.assertRaises(PluginOperationError),
            ):
                await repository.import_online("job-1", "example-plugin", "1.0.0")

    async def test_manager_composes_package_operations_as_a_collaborator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            async def restart(_: str) -> bool:
                return True

            manager = SharedPluginManager(
                database_path=root / "users.db",
                state_root=root / "state",
                users_root=root / "users",
                harness_cli="/unused",
                restart_tenant=restart,
            )

            self.assertIsInstance(manager.commands, PluginCommandRunner)
            self.assertIsInstance(manager.packages, SharedPackageRepository)
            self.assertEqual(manager.packages.releases, root / "state/plugins/releases")

    async def test_registry_versions_and_latest_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            async def restart(_: str) -> bool:
                return True

            manager = SharedPluginManager(
                database_path=root / "users.db",
                state_root=root / "state",
                users_root=root / "users",
                harness_cli="/unused",
                restart_tenant=restart,
            )
            staging = root / "staging"
            staging.mkdir()
            process = AsyncMock(
                return_value=(
                    '{"versions":["0.9.0","1.0.0-beta.1","1.0.0"],'
                    '"version":"1.0.0"}'
                )
            )
            with (
                patch.object(
                    manager.commands,
                    "prepare_staging",
                    return_value=staging,
                ),
                patch.object(manager.commands, "registry_arguments", return_value=[]),
                patch.object(manager.commands, "run", process),
            ):
                result = await manager.discover_versions("@scope/example-plugin")

            self.assertEqual(result["packageName"], "@scope/example-plugin")
            self.assertEqual(result["versions"], ["0.9.0", "1.0.0-beta.1", "1.0.0"])
            self.assertEqual(result["latest"], "1.0.0")
            command = process.await_args.args[0]
            self.assertEqual(command[:3], ["pnpm", "view", "@scope/example-plugin"])
            self.assertIn("versions", command)
            self.assertIn("--json", command)


class ProfileValidationTests(unittest.TestCase):
    def test_import_only_bundle_does_not_require_commonjs_root_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "users.db"
            initialize_registry(database)
            record = user("alice", os.getuid(), 3101)
            record["os_user"] = pwd.getpwuid(os.getuid()).pw_name
            add_user(database, record)

            state_root = root / "state"
            users_root = state_root / "users"
            profile = users_root / "alice" / "state" / "profiles" / "web"
            package = (
                users_root
                / "alice"
                / "state"
                / "profiles"
                / "node_modules"
                / "@example"
                / "import-only-bundle"
            )
            package.mkdir(parents=True)
            profile.mkdir(parents=True)
            (package / "package.json").write_text(
                """{
  "name": "@example/import-only-bundle",
  "version": "1.0.0",
  "type": "module",
  "exports": {".": {"import": "./lib/index.js"}},
  "dsh": {"bundle": {"patch": "./cordis.patch.yml"}}
}
""",
                encoding="utf-8",
            )
            (package / "cordis.patch.yml").write_text("[]\n", encoding="utf-8")
            plugin_id, version_id = register_plugin_version(
                database,
                package_name="@example/import-only-bundle",
                version="1.0.0",
                source_type="registry",
                artifact_sha256="c" * 64,
                release_path=str(root / "release"),
                resolver_alias="@dsh-managed/import-only-bundle",
            )
            set_user_plugin(database, "alice", plugin_id, version_id, True)

            async def restart(_: str) -> bool:
                return True

            async def run_process(argv: list[str], **_: object) -> str:
                if "-e" in argv:
                    raise PluginOperationError("ERR_PACKAGE_PATH_NOT_EXPORTED")
                return ""

            manager = SharedPluginManager(
                database_path=database,
                state_root=state_root,
                users_root=users_root,
                harness_cli="/unused",
                restart_tenant=restart,
            )
            with patch.object(manager.commands, "run", side_effect=run_process):
                try:
                    asyncio.run(manager._validate_profile(record))
                except PluginOperationError as error:
                    self.fail(f"import-only bundle was rejected: {error}")


class SharedPackageLinkTests(unittest.TestCase):
    def test_user_gets_only_a_symlink_to_the_shared_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "users.db"
            initialize_registry(database)
            account = pwd.getpwuid(os.getuid())
            record = user("alice", os.getuid(), 3101)
            record["os_user"] = account.pw_name
            add_user(database, record)

            state_root = root / "state"
            users_root = state_root / "users"
            modules = users_root / "alice" / "state" / "profiles" / "node_modules"
            modules.mkdir(parents=True)
            release = state_root / "plugins" / "releases" / "example" / "1.0.0"
            package = release / "node_modules" / "example-plugin"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                '{"name":"example-plugin","version":"1.0.0"}\n',
                encoding="utf-8",
            )
            plugin_id, version_id = register_plugin_version(
                database,
                package_name="example-plugin",
                version="1.0.0",
                source_type="offline",
                artifact_sha256="b" * 64,
                release_path=str(release),
                resolver_alias="@dsh-managed/example-plugin-1.0.0",
            )
            set_user_plugin(database, "alice", plugin_id, version_id, True)

            async def restart(_: str) -> bool:
                return True

            manager = SharedPluginManager(
                database_path=database,
                state_root=state_root,
                users_root=users_root,
                harness_cli="/unused",
                restart_tenant=restart,
            )
            self.assertTrue(manager._sync_package_links(record))
            link = modules / "example-plugin"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), package.resolve())

            set_user_plugin(database, "alice", plugin_id, version_id, False)
            self.assertTrue(manager._sync_package_links(record))
            self.assertFalse(link.exists())


if __name__ == "__main__":
    unittest.main()

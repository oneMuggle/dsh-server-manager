from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from .assignments import restore_user_assignment
from .builtins import discover_builtin_catalog, write_builtin_overlay
from .commands import PluginCommandRunner
from .errors import PluginOperationError
from .packages import SharedPackageRepository
from .profile import apply_profile, sync_package_links

from common.storage.assignments import (
    get_user_plugin,
    mark_user_plugins_ready,
    remove_user_plugin,
    set_plugin_default,
    set_user_plugin,
)
from common.storage.builtin_plugins import (
    effective_builtin_states,
    get_user_builtin_plugin,
    remove_user_builtin_plugin,
    replace_builtin_catalog,
    set_builtin_default,
    set_user_builtin_plugin,
)
from common.storage.plugin_catalog import (
    get_plugin_version,
)
from common.storage.plugin_jobs import get_job, set_job_item_status, set_job_status
from common.storage.users import get_user


class SharedPluginManager:
    def __init__(
        self,
        *,
        database_path: Path,
        state_root: Path,
        users_root: Path,
        harness_cli: str,
        restart_tenant: Callable[[str], Awaitable[bool]],
    ) -> None:
        self.database_path = database_path
        self.state_root = state_root
        self.users_root = users_root
        self.harness_cli = harness_cli
        self.restart_tenant = restart_tenant
        self.root = state_root / "plugins"
        self.commands = PluginCommandRunner(self.root, database_path)
        self.packages = SharedPackageRepository(
            database_path=database_path,
            state_root=state_root,
            commands=self.commands,
        )
        self.releases = self.packages.releases
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker: asyncio.Task[None] | None = None
        self.user_locks: dict[str, asyncio.Lock] = {}

    def initialize(self) -> None:
        self.packages.initialize()
        replace_builtin_catalog(
            self.database_path,
            discover_builtin_catalog(self.harness_cli),
        )

    async def start(self, queued_job_ids: list[str]) -> None:
        self.worker = asyncio.create_task(self._worker(), name="plugin-worker")
        for job_id in queued_job_ids:
            await self.queue.put(job_id)

    async def stop(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    async def enqueue(self, job_id: str) -> None:
        await self.queue.put(job_id)

    async def discover_versions(self, package_name: str) -> dict[str, Any]:
        return await self.packages.discover_versions(package_name)

    def registry_configuration(self) -> dict[str, object]:
        return self.commands.registry_configuration()

    def update_registry(self, registry_url: str | None) -> dict[str, object]:
        return self.commands.update_registry(registry_url)

    def prepare_user_profile(self, user: dict[str, Any]) -> bool:
        """Apply persisted policy before a tenant process starts."""
        builtin_changed = self._apply_builtin_profile(user)
        try:
            changed = apply_profile(self, user, PluginOperationError)
        except PluginOperationError as error:
            if "尚未初始化" in str(error):
                return builtin_changed
            raise
        mark_user_plugins_ready(self.database_path, str(user["username"]))
        return changed or builtin_changed

    async def reconcile_user(self, username: str) -> bool:
        """Apply defaults/persisted policy after a new profile has been created."""
        lock = self.user_locks.setdefault(username, asyncio.Lock())
        async with lock:
            user = get_user(self.database_path, username)
            if user is None:
                raise PluginOperationError("用户不存在")
            changed = self._apply_profile(user)
            await self._validate_profile(user)
            if changed and user.get("disabled") is not True:
                if not await self.restart_tenant(username):
                    raise PluginOperationError("插件配置已写入，但用户服务重启失败")
            mark_user_plugins_ready(self.database_path, username)
            return changed

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self._run_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                set_job_status(
                    self.database_path,
                    job_id,
                    "failed",
                    error=str(error) or "插件任务失败",
                )
            finally:
                self.queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        job = get_job(self.database_path, job_id)
        if job is None:
            return
        set_job_status(self.database_path, job_id, "running")
        if job["kind"] == "online_import":
            result = await self.packages.import_online(
                job_id,
                str(job["package_name"]),
                str(job["requested_version"] or "latest"),
            )
            set_job_status(self.database_path, job_id, "completed", result=result)
            return
        if job["kind"] == "offline_import":
            spool_name = str(job["scope"].get("spoolName", ""))
            result = await self.packages.import_offline(job_id, spool_name)
            set_job_status(self.database_path, job_id, "completed", result=result)
            return
        if job["kind"] == "assign":
            await self._run_assignment_job(job)
            return
        raise PluginOperationError("未知插件任务")

    async def _run_assignment_job(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        items = job["items"]
        failures = 0
        completed = 0
        for item in items:
            username = str(item["username"])
            if item["status"] == "completed":
                completed += 1
                continue
            set_job_item_status(self.database_path, job_id, username, "running")
            try:
                await self._apply_user_action(job, username)
            except Exception as error:
                failures += 1
                set_job_item_status(
                    self.database_path, job_id, username, "failed", str(error)
                )
            else:
                completed += 1
                set_job_item_status(self.database_path, job_id, username, "completed")
        status = "completed" if failures == 0 else ("failed" if completed == 0 else "partial_failed")
        set_job_status(
            self.database_path,
            job_id,
            status,
            result={"completed": completed, "failed": failures},
        )
        if bool(job["scope"].get("applyToFutureUsers")):
            action = str(job["action"])
            builtin_entry_id = job["scope"].get("builtinEntryId")
            if builtin_entry_id:
                set_builtin_default(
                    self.database_path,
                    str(builtin_entry_id),
                    action == "enable",
                )
            else:
                version_id = int(job["plugin_version_id"] or 0)
                set_plugin_default(
                    self.database_path,
                    int(job["plugin_id"]),
                    version_id,
                    assigned=action != "uninstall",
                    enabled=action == "enable" or (
                        action == "install"
                        and bool(job["scope"].get("enabledAfterInstall", True))
                    ),
                )

    async def _apply_user_action(self, job: dict[str, Any], username: str) -> None:
        builtin_entry_id = job["scope"].get("builtinEntryId")
        if builtin_entry_id:
            await self._apply_builtin_action(job, username, str(builtin_entry_id))
            return
        lock = self.user_locks.setdefault(username, asyncio.Lock())
        async with lock:
            user = get_user(self.database_path, username)
            if user is None:
                raise PluginOperationError("用户不存在")
            plugin_id = int(job["plugin_id"])
            action = str(job["action"])
            previous = get_user_plugin(self.database_path, username, plugin_id)
            version_id = int(
                job["plugin_version_id"]
                if action == "install"
                else (previous or {}).get("plugin_version_id", 0)
            )
            version = get_plugin_version(self.database_path, version_id) if version_id else None
            if action in {"install", "enable"} and version is None:
                raise PluginOperationError("插件版本不存在")
            scope = job["scope"]
            try:
                if action == "install":
                    enabled = bool(scope.get("enabledAfterInstall", True))
                    set_user_plugin(
                        self.database_path, username, plugin_id, version_id, enabled, state="pending"
                    )
                elif action == "enable":
                    if previous is None:
                        raise PluginOperationError("用户尚未安装该插件")
                    set_user_plugin(
                        self.database_path, username, plugin_id, version_id, True, state="pending"
                    )
                elif action == "disable":
                    if previous is None:
                        raise PluginOperationError("用户尚未安装该插件")
                    set_user_plugin(
                        self.database_path, username, plugin_id, version_id, False, state="pending"
                    )
                elif action == "uninstall":
                    remove_user_plugin(self.database_path, username, plugin_id)
                else:
                    raise PluginOperationError("未知插件操作")

                changed = self._apply_profile(user)
                await self._validate_profile(user)
                if changed and user.get("disabled") is not True:
                    if not await self.restart_tenant(username):
                        raise PluginOperationError("插件配置已写入，但用户服务重启失败")
                current = get_user_plugin(self.database_path, username, plugin_id)
                if current is not None:
                    set_user_plugin(
                        self.database_path,
                        username,
                        plugin_id,
                        int(current["plugin_version_id"]),
                        bool(current["enabled"]),
                        state="ready",
                    )
            except Exception:
                restore_user_assignment(
                    self.database_path,
                    username,
                    plugin_id,
                    previous,
                )
                self._apply_profile(user)
                raise

    async def _apply_builtin_action(
        self,
        job: dict[str, Any],
        username: str,
        entry_id: str,
    ) -> None:
        lock = self.user_locks.setdefault(username, asyncio.Lock())
        async with lock:
            user = get_user(self.database_path, username)
            if user is None:
                raise PluginOperationError("用户不存在")
            action = str(job["action"])
            if action not in {"enable", "disable"}:
                raise PluginOperationError("官方内置插件只允许启用或停用")
            previous = get_user_builtin_plugin(self.database_path, username, entry_id)
            try:
                set_user_builtin_plugin(
                    self.database_path,
                    username,
                    entry_id,
                    action == "enable",
                )
                changed = self._apply_profile(user)
                await self._validate_profile(user)
                if changed and user.get("disabled") is not True:
                    if not await self.restart_tenant(username):
                        raise PluginOperationError("插件配置已写入，但用户服务重启失败")
            except Exception:
                if previous is None:
                    remove_user_builtin_plugin(self.database_path, username, entry_id)
                else:
                    set_user_builtin_plugin(
                        self.database_path, username, entry_id, previous
                    )
                self._apply_profile(user)
                raise

    def _sync_package_links(self, user: dict[str, Any]) -> bool:
        return sync_package_links(self, user, PluginOperationError)

    def _apply_profile(self, user: dict[str, Any]) -> bool:
        shared_changed = apply_profile(self, user, PluginOperationError)
        builtin_changed = self._apply_builtin_profile(user)
        return shared_changed or builtin_changed

    def _apply_builtin_profile(self, user: dict[str, Any]) -> bool:
        username = str(user["username"])
        state_root = self.users_root / username / "state"
        path = state_root / "dsm-builtins.patch.yml"
        before = path.read_bytes() if path.is_file() else None
        overlay = write_builtin_overlay(
            state_root,
            effective_builtin_states(self.database_path, username),
        )
        return before != overlay.read_bytes()

    async def _validate_profile(self, user: dict[str, Any]) -> None:
        username = str(user["username"])
        home = self.users_root / username / "home"
        state = self.users_root / username / "state"
        workspace = home / "workspace"
        command = [
            "runuser",
            "-u",
            str(user["os_user"]),
            "--",
            "env",
            "-i",
            f"HOME={home}",
            f"DSH_HOME={state}",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "/usr/local/bin/node",
            self.harness_cli,
            "--profile",
            "web",
            "--patch",
            str(state / "dsm-builtins.patch.yml"),
            "--dump-config",
        ]
        await self.commands.run(command, cwd=workspace, timeout=60)

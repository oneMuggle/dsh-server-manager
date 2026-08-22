from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import signal
import stat
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from common.storage.users import get_user
from gateway.plugins import (
    enabled_aliases,
    enabled_package_links,
    get_job,
    get_plugin_version,
    get_user_plugin,
    list_plugin_versions,
    mark_user_plugins_ready,
    register_plugin_version,
    remove_user_plugin,
    set_job_item_status,
    set_job_status,
    set_plugin_default,
    set_user_plugin,
)


PACKAGE_PATTERN = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$"
)
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
MANAGED_SCOPE = "@dsh-managed"
INSTALLER_USER = "dsh-plugin-installer"
MAX_PROCESS_OUTPUT = 32 * 1024
MAX_REGISTRY_METADATA_OUTPUT = 1024 * 1024
MAX_DISCOVERED_VERSIONS = 10000


class PluginOperationError(RuntimeError):
    pass


def validate_package_name(value: str) -> str:
    if len(value) > 214 or not PACKAGE_PATTERN.fullmatch(value):
        raise ValueError("无效的 npm 插件名称")
    return value


def validate_requested_version(value: str) -> str:
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("无效的插件版本或 dist-tag")
    return value


def _bounded_output(
    stdout: bytes, stderr: bytes, limit: int = MAX_PROCESS_OUTPUT
) -> str:
    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
    if len(combined) > limit:
        combined = combined[-limit:]
        prefix = "…输出过长，仅保留末尾内容…\n"
    else:
        prefix = ""
    return prefix + combined.decode("utf-8", errors="replace").strip()


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
        self.artifacts = self.root / "artifacts"
        self.releases = self.root / "releases"
        self.staging = self.root / "staging"
        self.store = self.root / "pnpm-store"
        self.installer_home = self.root / "installer-home"
        self.resolver_root = Path("/opt/deepseek-harness/node_modules") / MANAGED_SCOPE
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker: asyncio.Task[None] | None = None
        self.user_locks: dict[str, asyncio.Lock] = {}

    def initialize(self) -> None:
        account = pwd.getpwnam(INSTALLER_USER)
        for directory in (
            self.root,
            self.artifacts,
            self.releases,
            self.staging,
            self.store,
            self.installer_home,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        for directory in (self.staging, self.store, self.installer_home):
            os.chown(directory, account.pw_uid, account.pw_gid)
            os.chmod(directory, 0o750)
        self.resolver_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.resolver_root, 0o755)
        for version in list_plugin_versions(self.database_path):
            self._ensure_resolver(version)

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

    def prepare_user_profile(self, user: dict[str, Any]) -> bool:
        """Apply persisted policy before a tenant process starts."""
        try:
            changed = self._apply_profile(user)
        except PluginOperationError as error:
            if "尚未初始化" in str(error):
                return False
            raise
        mark_user_plugins_ready(self.database_path, str(user["username"]))
        return changed

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
            result = await self._import_online(
                job_id,
                str(job["package_name"]),
                str(job["requested_version"] or "latest"),
            )
            set_job_status(self.database_path, job_id, "completed", result=result)
            return
        if job["kind"] == "offline_import":
            spool_name = str(job["scope"].get("spoolName", ""))
            result = await self._import_offline(job_id, spool_name)
            set_job_status(self.database_path, job_id, "completed", result=result)
            return
        if job["kind"] == "assign":
            await self._run_assignment_job(job)
            return
        raise PluginOperationError("未知插件任务")

    async def _run_process(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout: float = 300,
        as_installer: bool = False,
        extra_env: list[str] | None = None,
        max_success_output: int = MAX_PROCESS_OUTPUT,
    ) -> str:
        env = [
            f"HOME={self.installer_home if as_installer else cwd}",
            "PATH=/usr/local/bin:/usr/sbin:/usr/bin:/bin",
            "COREPACK_ENABLE_DOWNLOAD_PROMPT=0",
        ]
        if as_installer:
            env.append("NPM_CONFIG_UPDATE_NOTIFIER=false")
        env.extend(extra_env or [])
        command = ["env", "-i", *env, *argv]
        if as_installer:
            command = ["runuser", "-u", INSTALLER_USER, "--", *command]
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise PluginOperationError("插件命令执行超时") from error
        if process.returncode != 0:
            output = _bounded_output(stdout, stderr)
            raise PluginOperationError(output or f"插件命令退出码 {process.returncode}")
        return _bounded_output(stdout, b"", max_success_output)

    def _prepare_staging(self, job_id: str) -> Path:
        path = self.staging / job_id
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(mode=0o750)
        account = pwd.getpwnam(INSTALLER_USER)
        os.chown(path, account.pw_uid, account.pw_gid)
        package_json = path / "package.json"
        package_json.write_text(
            json.dumps({"name": f"dsh-shared-plugin-{job_id}", "private": True}) + "\n",
            encoding="utf-8",
        )
        os.chown(package_json, account.pw_uid, account.pw_gid)
        return path

    def _registry_arguments(self, staging: Path) -> list[str]:
        args: list[str] = []
        registry = os.environ.get("DSH_PLUGIN_NPM_REGISTRY", "").strip()
        token = os.environ.get("DSH_PLUGIN_NPM_TOKEN", "").strip()
        if registry:
            parsed = urlsplit(registry)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise PluginOperationError("DSH_PLUGIN_NPM_REGISTRY 必须是 HTTP(S) URL")
            args.extend(["--registry", registry])
        npmrc = staging / ".npmrc"
        if token:
            token_registry = registry or "https://registry.npmjs.org/"
            parsed = urlsplit(token_registry)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise PluginOperationError("无法为无效 registry 配置认证")
            auth_path = parsed.path.rstrip("/") + "/"
            npmrc.write_text(
                f"registry={token_registry}\n//{parsed.netloc}{auth_path}:_authToken={token}\n",
                encoding="utf-8",
            )
            account = pwd.getpwnam(INSTALLER_USER)
            os.chown(npmrc, account.pw_uid, account.pw_gid)
            os.chmod(npmrc, 0o600)
        return args

    async def discover_versions(self, package_name: str) -> dict[str, Any]:
        package_name = validate_package_name(package_name)
        staging = self._prepare_staging(f"discover-{secrets.token_hex(8)}")
        npmrc = staging / ".npmrc"
        try:
            registry_args = self._registry_arguments(staging)
            output = await self._run_process(
                [
                    "pnpm",
                    "view",
                    package_name,
                    "versions",
                    "version",
                    "--json",
                    *registry_args,
                ],
                cwd=staging,
                timeout=60,
                as_installer=True,
                max_success_output=MAX_REGISTRY_METADATA_OUTPUT,
            )
            try:
                metadata = json.loads(output)
            except json.JSONDecodeError as error:
                raise PluginOperationError("registry 返回了无效的版本元数据") from error
            if not isinstance(metadata, dict):
                raise PluginOperationError("registry 未返回插件版本列表")
            raw_versions = metadata.get("versions")
            if isinstance(raw_versions, str):
                raw_versions = [raw_versions]
            if not isinstance(raw_versions, list) or not raw_versions:
                raise PluginOperationError("registry 未返回插件版本列表")
            if len(raw_versions) > MAX_DISCOVERED_VERSIONS:
                raise PluginOperationError("registry 返回的插件版本数量过多")
            versions: list[str] = []
            for value in raw_versions:
                if not isinstance(value, str):
                    raise PluginOperationError("registry 返回了无效的插件版本")
                try:
                    version = validate_requested_version(value)
                except ValueError as error:
                    raise PluginOperationError("registry 返回了无效的插件版本") from error
                if version not in versions:
                    versions.append(version)
            latest_value = metadata.get("version")
            if not isinstance(latest_value, str):
                raise PluginOperationError("registry 未返回 latest 版本")
            try:
                latest = validate_requested_version(latest_value)
            except ValueError as error:
                raise PluginOperationError("registry 返回了无效的 latest 版本") from error
            if latest not in versions:
                versions.append(latest)
            return {
                "packageName": package_name,
                "versions": versions,
                "latest": latest,
            }
        finally:
            npmrc.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)

    async def _pnpm_add(self, staging: Path, spec: str, *, offline: bool) -> None:
        args = [
            "pnpm",
            "--store-dir",
            str(self.store),
            "add",
            "--save-exact",
            "--ignore-scripts",
            "--package-import-method=copy",
            "--reporter=append-only",
        ]
        if offline:
            args.append("--offline")
        args.extend(self._registry_arguments(staging))
        args.append(spec)
        npmrc = staging / ".npmrc"
        try:
            await self._run_process(args, cwd=staging, as_installer=True)
        finally:
            npmrc.unlink(missing_ok=True)

    def _installed_package(self, staging: Path, expected_name: str | None) -> tuple[str, str, Path]:
        project = json.loads((staging / "package.json").read_text(encoding="utf-8"))
        dependencies = project.get("dependencies")
        if not isinstance(dependencies, dict) or len(dependencies) != 1:
            raise PluginOperationError("插件安装结果必须只包含一个直接依赖")
        package_name = next(iter(dependencies))
        validate_package_name(package_name)
        if expected_name is not None and package_name != expected_name:
            raise PluginOperationError("registry 返回的包名与请求不一致")
        package_link = staging / "node_modules" / package_name
        try:
            package_dir = package_link.resolve(strict=True)
        except OSError as error:
            raise PluginOperationError("无法解析已安装插件") from error
        if not package_dir.is_relative_to(staging.resolve()):
            raise PluginOperationError("插件解析结果逃逸共享 release 目录")
        manifest = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
        if manifest.get("name") != package_name or not isinstance(manifest.get("version"), str):
            raise PluginOperationError("插件 package.json 名称或版本无效")
        try:
            validate_requested_version(str(manifest["version"]))
        except ValueError as error:
            raise PluginOperationError("插件 package.json 版本无效") from error
        declared = manifest.get("dsh", {}).get("bundle", {}).get("patch")
        if not isinstance(declared, str) or not declared:
            raise PluginOperationError("该 npm 包没有声明 dsh.bundle.patch")
        patch_path = (package_dir / declared).resolve()
        if not patch_path.is_relative_to(package_dir) or not patch_path.is_file():
            raise PluginOperationError("插件声明的 Bundle patch 无效")
        return package_name, str(manifest["version"]), package_link

    def _publish(
        self,
        staging: Path,
        package_name: str,
        version: str,
        source_type: str,
        digest: str,
    ) -> dict[str, Any]:
        name_key = hashlib.sha256(package_name.encode()).hexdigest()[:12]
        version_key = re.sub(r"[^0-9A-Za-z._-]", "-", version)[:32]
        release = self.releases / name_key / f"{version_key}-{digest[:12]}"
        release.parent.mkdir(parents=True, exist_ok=True)
        if release.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, release)
        for root, directories, files in os.walk(release):
            for name in directories:
                path = Path(root) / name
                if not path.is_symlink():
                    os.chown(path, 0, 0)
                    os.chmod(path, 0o555)
            for name in files:
                path = Path(root) / name
                if not path.is_symlink():
                    os.chown(path, 0, 0)
                    os.chmod(path, 0o444)
        os.chown(release, 0, 0)
        os.chmod(release, 0o555)
        alias = f"{MANAGED_SCOPE}/p-{name_key}-{version_key.lower()}-{digest[:8]}"
        plugin_id, version_id = register_plugin_version(
            self.database_path,
            package_name=package_name,
            version=version,
            source_type=source_type,
            artifact_sha256=digest,
            release_path=str(release),
            resolver_alias=alias,
        )
        record = get_plugin_version(self.database_path, version_id)
        assert record is not None
        self._ensure_resolver(record)
        return {
            "pluginId": plugin_id,
            "versionId": version_id,
            "packageName": package_name,
            "version": version,
            "sha256": digest,
            "resolverAlias": alias,
        }

    def _ensure_resolver(self, version: dict[str, Any]) -> None:
        alias = str(version["resolver_alias"])
        if not alias.startswith(f"{MANAGED_SCOPE}/"):
            raise PluginOperationError("无效的共享插件解析别名")
        release = Path(str(version["release_path"]))
        target = release / "node_modules" / str(version["package_name"])
        if not target.exists():
            raise PluginOperationError(f"共享插件文件不存在：{version['package_name']}")
        link = self.resolver_root / alias.split("/", 1)[1]
        temporary = link.with_name(f".{link.name}.tmp-{os.getpid()}")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, link)

    async def _import_online(self, job_id: str, package_name: str, requested: str) -> dict[str, Any]:
        validate_package_name(package_name)
        validate_requested_version(requested)
        staging = self._prepare_staging(job_id)
        try:
            await self._pnpm_add(staging, f"{package_name}@{requested}", offline=False)
            installed_name, version, _ = self._installed_package(staging, package_name)
            lockfile = staging / "pnpm-lock.yaml"
            digest = hashlib.sha256(lockfile.read_bytes()).hexdigest()
            return self._publish(staging, installed_name, version, "registry", digest)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    async def _import_offline(self, job_id: str, spool_name: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}\.plugin\.tgz", spool_name):
            raise PluginOperationError("无效的离线插件上传文件")
        spool = self.state_root / "gateway" / "uploads" / spool_name
        if not spool.is_file():
            raise PluginOperationError("离线插件上传文件不存在")
        digest = hashlib.sha256(spool.read_bytes()).hexdigest()
        artifact = self.artifacts / f"{digest}.tgz"
        if not artifact.exists():
            shutil.copyfile(spool, artifact)
            os.chmod(artifact, 0o444)
        staging = self._prepare_staging(job_id)
        try:
            await self._pnpm_add(staging, f"file:{artifact}", offline=True)
            package_name, version, _ = self._installed_package(staging, None)
            result = self._publish(staging, package_name, version, "offline", digest)
            spool.unlink(missing_ok=True)
            return result
        except Exception as error:
            shutil.rmtree(staging, ignore_errors=True)
            spool.unlink(missing_ok=True)
            raise PluginOperationError(
                f"离线导入失败；请确认 tgz 已内置全部依赖：{error}"
            ) from error

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
                if previous is None:
                    remove_user_plugin(self.database_path, username, plugin_id)
                else:
                    set_user_plugin(
                        self.database_path,
                        username,
                        plugin_id,
                        int(previous["plugin_version_id"]),
                        bool(previous["enabled"]),
                        state=str(previous["state"]),
                        error=previous.get("last_error"),
                    )
                self._apply_profile(user)
                raise

    def _sync_package_links(self, user: dict[str, Any]) -> bool:
        username = str(user["username"])
        uid = int(user["uid"])
        account = pwd.getpwnam(str(user["os_user"]))
        modules_root = self.users_root / username / "state" / "profiles" / "node_modules"
        release_root = self.releases.resolve()

        def ensure_owned_directory(path: Path) -> None:
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                path.mkdir(mode=0o700)
                os.chown(path, uid, account.pw_gid)
                return
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise PluginOperationError(f"插件模块目录不是安全的普通目录：{path}")
            if metadata.st_uid != uid:
                raise PluginOperationError(f"插件模块目录所有者不正确：{path}")

        ensure_owned_directory(modules_root)
        desired: dict[str, Path] = {}
        for item in enabled_package_links(self.database_path, username):
            package_name = validate_package_name(item["package_name"])
            release = Path(item["release_path"]).resolve(strict=True)
            if not release.is_relative_to(release_root):
                raise PluginOperationError("共享插件 release 路径无效")
            target = release / "node_modules" / package_name
            if not target.resolve(strict=True).is_relative_to(release):
                raise PluginOperationError(f"共享插件包路径无效：{package_name}")
            desired[package_name] = target

        changed = False
        known_names = {str(item["package_name"]) for item in list_plugin_versions(self.database_path)}
        for package_name in known_names - desired.keys():
            link = modules_root.joinpath(*package_name.split("/"))
            try:
                metadata = link.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                resolved = link.resolve(strict=False)
                if resolved.is_relative_to(release_root):
                    link.unlink()
                    changed = True

        for package_name, target in desired.items():
            parts = package_name.split("/")
            parent = modules_root.joinpath(*parts[:-1])
            if parent != modules_root:
                ensure_owned_directory(parent)
            link = parent / parts[-1]
            replace = False
            try:
                metadata = link.lstat()
            except FileNotFoundError:
                replace = True
            else:
                if not stat.S_ISLNK(metadata.st_mode):
                    raise PluginOperationError(f"插件包名与现有模块冲突：{package_name}")
                resolved = link.resolve(strict=False)
                expected = target.resolve(strict=True)
                if resolved == expected:
                    continue
                if not resolved.is_relative_to(release_root):
                    raise PluginOperationError(f"插件包名与现有模块链接冲突：{package_name}")
                replace = True
            if replace:
                temporary = parent / f".{parts[-1]}.dsh-managed-{os.getpid()}"
                temporary.unlink(missing_ok=True)
                temporary.symlink_to(target)
                os.replace(temporary, link)
                changed = True
        return changed

    def _apply_profile(self, user: dict[str, Any]) -> bool:
        username = str(user["username"])
        profile_dir = self.users_root / username / "state" / "profiles" / "web"
        manifest_path = profile_dir / "package.json"
        if not manifest_path.is_file():
            raise PluginOperationError("用户 web profile 尚未初始化")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dsh = manifest.setdefault("dsh", {})
        profile = dsh.setdefault("profile", {})
        bundles = profile.setdefault("bundles", [])
        if not isinstance(bundles, list) or not all(isinstance(value, str) for value in bundles):
            raise PluginOperationError("用户 Bundle 列表无效")
        desired = enabled_aliases(self.database_path, username)
        retained = [value for value in bundles if not value.startswith(f"{MANAGED_SCOPE}/")]
        updated = [*retained, *desired]
        links_changed = self._sync_package_links(user)
        manifest_changed = updated != bundles
        if manifest_changed:
            profile["bundles"] = updated
            temporary = manifest_path.with_name(f".package.json.tmp-{os.getpid()}")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(manifest, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                account = pwd.getpwnam(str(user["os_user"]))
                os.chown(temporary, int(user["uid"]), account.pw_gid)
                os.chmod(temporary, 0o644)
                os.replace(temporary, manifest_path)
            finally:
                temporary.unlink(missing_ok=True)
        return manifest_changed or links_changed

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
            "--dump-config",
        ]
        await self._run_process(command, cwd=workspace, timeout=60)

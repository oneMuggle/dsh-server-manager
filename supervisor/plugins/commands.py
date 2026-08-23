from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import signal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from common.storage.settings import get_plugin_npm_registry, set_plugin_npm_registry

from .errors import PluginOperationError
from .installer import MAX_PROCESS_OUTPUT, bounded_output


INSTALLER_USER = "dsh-plugin-installer"
DEFAULT_NPM_REGISTRY = "https://registry.npmjs.org/"


def normalize_registry_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 2048:
        raise PluginOperationError("npm Registry 地址不能为空且不能超过 2048 个字符")
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as error:
        raise PluginOperationError("npm Registry 包含无效端口") from error
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or any(character.isspace() for character in parsed.netloc)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PluginOperationError(
            "npm Registry 必须是不含账号、查询参数或片段的 HTTP(S) URL"
        )
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


class PluginCommandRunner:
    def __init__(self, plugins_root: Path, database_path: Path | None = None) -> None:
        self.staging = plugins_root / "staging"
        self.store = plugins_root / "pnpm-store"
        self.installer_home = plugins_root / "installer-home"
        self.database_path = database_path
        environment_registry = os.environ.get("DSM_PLUGIN_NPM_REGISTRY", "").strip()
        self.default_registry = normalize_registry_url(
            environment_registry or DEFAULT_NPM_REGISTRY
        )
        self.default_source = "environment" if environment_registry else "default"
        self.registry_token = os.environ.get("DSM_PLUGIN_NPM_TOKEN", "").strip()

    def initialize(self) -> None:
        account = pwd.getpwnam(INSTALLER_USER)
        for directory in (self.staging, self.store, self.installer_home):
            directory.mkdir(parents=True, exist_ok=True)
            os.chown(directory, account.pw_uid, account.pw_gid)
            os.chmod(directory, 0o750)

    async def run(
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
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
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
            output = bounded_output(stdout, stderr)
            raise PluginOperationError(output or f"插件命令退出码 {process.returncode}")
        return bounded_output(stdout, b"", max_success_output)

    def prepare_staging(self, job_id: str) -> Path:
        path = self.staging / job_id
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(mode=0o750)
        account = pwd.getpwnam(INSTALLER_USER)
        os.chown(path, account.pw_uid, account.pw_gid)
        package_json = path / "package.json"
        package_json.write_text(
            json.dumps({"name": f"dsh-shared-plugin-{job_id}", "private": True})
            + "\n",
            encoding="utf-8",
        )
        os.chown(package_json, account.pw_uid, account.pw_gid)
        return path

    def registry_configuration(self) -> dict[str, object]:
        configured = (
            get_plugin_npm_registry(self.database_path)
            if self.database_path is not None
            else None
        )
        registry = normalize_registry_url(configured) if configured else self.default_registry
        return {
            "registryUrl": registry,
            "source": "configured" if configured else self.default_source,
            "defaultRegistryUrl": self.default_registry,
            "tokenConfigured": bool(self.registry_token),
        }

    def update_registry(self, registry_url: str | None) -> dict[str, object]:
        if self.database_path is None:
            raise PluginOperationError("插件设置存储尚未初始化")
        normalized = normalize_registry_url(registry_url) if registry_url else None
        set_plugin_npm_registry(self.database_path, normalized)
        return self.registry_configuration()

    def registry_arguments(self, staging: Path) -> list[str]:
        registry = str(self.registry_configuration()["registryUrl"])
        arguments = ["--registry", registry]
        npmrc = staging / ".npmrc"
        if self.registry_token:
            parsed = urlsplit(registry)
            auth_path = parsed.path.rstrip("/") + "/"
            npmrc.write_text(
                f"registry={registry}\n"
                f"//{parsed.netloc}{auth_path}:_authToken={self.registry_token}\n",
                encoding="utf-8",
            )
            account = pwd.getpwnam(INSTALLER_USER)
            os.chown(npmrc, account.pw_uid, account.pw_gid)
            os.chmod(npmrc, 0o600)
        return arguments

    async def pnpm_add(self, staging: Path, spec: str, *, offline: bool) -> None:
        arguments = [
            "pnpm",
            "--store-dir",
            str(self.store),
            "add",
            "--save-exact",
            "--dangerously-allow-all-builds",
            "--package-import-method=copy",
            "--reporter=append-only",
        ]
        if offline:
            arguments.append("--offline")
        arguments.extend(self.registry_arguments(staging))
        arguments.append(spec)
        npmrc = staging / ".npmrc"
        try:
            await self.run(
                arguments,
                cwd=staging,
                as_installer=True,
            )
        finally:
            npmrc.unlink(missing_ok=True)

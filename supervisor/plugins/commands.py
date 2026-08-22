from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import signal
from pathlib import Path
from urllib.parse import urlsplit

from .errors import PluginOperationError
from .installer import MAX_PROCESS_OUTPUT, bounded_output


INSTALLER_USER = "dsh-plugin-installer"


class PluginCommandRunner:
    def __init__(self, plugins_root: Path) -> None:
        self.staging = plugins_root / "staging"
        self.store = plugins_root / "pnpm-store"
        self.installer_home = plugins_root / "installer-home"

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

    def registry_arguments(self, staging: Path) -> list[str]:
        arguments: list[str] = []
        registry = os.environ.get("DSH_PLUGIN_NPM_REGISTRY", "").strip()
        token = os.environ.get("DSH_PLUGIN_NPM_TOKEN", "").strip()
        if registry:
            parsed = urlsplit(registry)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise PluginOperationError(
                    "DSH_PLUGIN_NPM_REGISTRY 必须是 HTTP(S) URL"
                )
            arguments.extend(["--registry", registry])
        npmrc = staging / ".npmrc"
        if token:
            token_registry = registry or "https://registry.npmjs.org/"
            parsed = urlsplit(token_registry)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise PluginOperationError("无法为无效 registry 配置认证")
            auth_path = parsed.path.rstrip("/") + "/"
            npmrc.write_text(
                f"registry={token_registry}\n"
                f"//{parsed.netloc}{auth_path}:_authToken={token}\n",
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
            "--ignore-scripts",
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

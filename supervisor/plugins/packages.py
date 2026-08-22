from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from common.storage.plugin_catalog import (
    get_plugin_version,
    list_plugin_versions,
    register_plugin_version,
)

from .commands import PluginCommandRunner
from .errors import PluginOperationError
from .installer import installed_package
from .registry import (
    parse_version_metadata,
    validate_package_name,
    validate_requested_version,
)


MANAGED_SCOPE = "@dsh-managed"
MAX_REGISTRY_METADATA_OUTPUT = 1024 * 1024


def freeze_release(release: Path) -> None:
    for root, directories, files in os.walk(release):
        for name in directories:
            path = Path(root) / name
            if not path.is_symlink():
                os.chown(path, 0, 0)
                os.chmod(path, 0o555)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                executable = bool(path.stat().st_mode & 0o111)
                os.chown(path, 0, 0)
                os.chmod(path, 0o555 if executable else 0o444)
    os.chown(release, 0, 0)
    os.chmod(release, 0o555)


class SharedPackageRepository:
    def __init__(
        self,
        *,
        database_path: Path,
        state_root: Path,
        commands: PluginCommandRunner,
    ) -> None:
        self.database_path = database_path
        self.state_root = state_root
        self.commands = commands
        self.root = state_root / "plugins"
        self.artifacts = self.root / "artifacts"
        self.releases = self.root / "releases"
        self.resolver_root = (
            Path("/opt/deepseek-harness/node_modules") / MANAGED_SCOPE
        )

    def initialize(self) -> None:
        for directory in (self.root, self.artifacts, self.releases):
            directory.mkdir(parents=True, exist_ok=True)
        self.commands.initialize()
        self.resolver_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.resolver_root, 0o755)
        for version in list_plugin_versions(self.database_path):
            self.ensure_resolver(version)

    async def discover_versions(self, package_name: str) -> dict[str, Any]:
        package_name = validate_package_name(package_name)
        staging = self.commands.prepare_staging(
            f"discover-{secrets.token_hex(8)}"
        )
        npmrc = staging / ".npmrc"
        try:
            registry_arguments = self.commands.registry_arguments(staging)
            output = await self.commands.run(
                [
                    "pnpm",
                    "view",
                    package_name,
                    "versions",
                    "version",
                    "--json",
                    *registry_arguments,
                ],
                cwd=staging,
                timeout=60,
                as_installer=True,
                max_success_output=MAX_REGISTRY_METADATA_OUTPUT,
            )
            try:
                metadata = json.loads(output)
            except json.JSONDecodeError as error:
                raise PluginOperationError(
                    "registry 返回了无效的版本元数据"
                ) from error
            try:
                parsed = parse_version_metadata(metadata)
            except ValueError as error:
                raise PluginOperationError(str(error)) from error
            return {"packageName": package_name, **parsed}
        finally:
            npmrc.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)

    def publish(
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
        freeze_release(release)
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
        self.ensure_resolver(record)
        return {
            "pluginId": plugin_id,
            "versionId": version_id,
            "packageName": package_name,
            "version": version,
            "sha256": digest,
            "resolverAlias": alias,
        }

    def ensure_resolver(self, version: dict[str, Any]) -> None:
        alias = str(version["resolver_alias"])
        if not alias.startswith(f"{MANAGED_SCOPE}/"):
            raise PluginOperationError("无效的共享插件解析别名")
        release = Path(str(version["release_path"]))
        target = release / "node_modules" / str(version["package_name"])
        if not target.exists():
            raise PluginOperationError(
                f"共享插件文件不存在：{version['package_name']}"
            )
        link = self.resolver_root / alias.split("/", 1)[1]
        temporary = link.with_name(f".{link.name}.tmp-{os.getpid()}")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, link)

    @staticmethod
    def _installed_package(
        staging: Path,
        expected_name: str | None,
    ) -> tuple[str, str, Path]:
        try:
            return installed_package(staging, expected_name)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise PluginOperationError(str(error)) from error

    async def import_online(
        self,
        job_id: str,
        package_name: str,
        requested: str,
    ) -> dict[str, Any]:
        validate_package_name(package_name)
        validate_requested_version(requested)
        staging = self.commands.prepare_staging(job_id)
        try:
            await self.commands.pnpm_add(
                staging,
                f"{package_name}@{requested}",
                offline=False,
            )
            installed_name, version, _ = self._installed_package(
                staging,
                package_name,
            )
            digest = hashlib.sha256(
                (staging / "pnpm-lock.yaml").read_bytes()
            ).hexdigest()
            return self.publish(
                staging,
                installed_name,
                version,
                "registry",
                digest,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    async def import_offline(
        self,
        job_id: str,
        spool_name: str,
    ) -> dict[str, Any]:
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
        staging = self.commands.prepare_staging(job_id)
        try:
            await self.commands.pnpm_add(
                staging,
                f"file:{artifact}",
                offline=True,
            )
            package_name, version, _ = self._installed_package(staging, None)
            result = self.publish(
                staging,
                package_name,
                version,
                "offline",
                digest,
            )
            spool.unlink(missing_ok=True)
            return result
        except Exception as error:
            shutil.rmtree(staging, ignore_errors=True)
            spool.unlink(missing_ok=True)
            raise PluginOperationError(
                f"离线导入失败；请确认 tgz 已内置全部依赖：{error}"
            ) from error

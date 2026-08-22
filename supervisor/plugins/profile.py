from __future__ import annotations

import json
import os
import pwd
import stat
from pathlib import Path
from typing import Any, Callable, Protocol

from common.storage.assignments import enabled_aliases, enabled_package_links
from common.storage.plugin_catalog import list_plugin_versions

from .registry import validate_package_name


MANAGED_SCOPE = "@dsh-managed"


class ProfileRuntime(Protocol):
    database_path: Path
    users_root: Path
    releases: Path


def sync_package_links(
    runtime: ProfileRuntime,
    user: dict[str, Any],
    error_type: Callable[[str], Exception] = RuntimeError,
) -> bool:
    username = str(user["username"])
    uid = int(user["uid"])
    account = pwd.getpwnam(str(user["os_user"]))
    modules_root = runtime.users_root / username / "state" / "profiles" / "node_modules"
    release_root = runtime.releases.resolve()

    def ensure_owned_directory(path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            path.mkdir(mode=0o700)
            os.chown(path, uid, account.pw_gid)
            return
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise error_type(f"插件模块目录不是安全的普通目录：{path}")
        if metadata.st_uid != uid:
            raise error_type(f"插件模块目录所有者不正确：{path}")

    ensure_owned_directory(modules_root)
    desired: dict[str, Path] = {}
    for item in enabled_package_links(runtime.database_path, username):
        package_name = validate_package_name(item["package_name"])
        release = Path(item["release_path"]).resolve(strict=True)
        if not release.is_relative_to(release_root):
            raise error_type("共享插件 release 路径无效")
        target = release / "node_modules" / package_name
        if not target.resolve(strict=True).is_relative_to(release):
            raise error_type(f"共享插件包路径无效：{package_name}")
        desired[package_name] = target

    changed = False
    known_names = {
        str(item["package_name"])
        for item in list_plugin_versions(runtime.database_path)
    }
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
        try:
            metadata = link.lstat()
        except FileNotFoundError:
            replace = True
        else:
            if not stat.S_ISLNK(metadata.st_mode):
                raise error_type(f"插件包名与现有模块冲突：{package_name}")
            resolved = link.resolve(strict=False)
            expected = target.resolve(strict=True)
            if resolved == expected:
                continue
            if not resolved.is_relative_to(release_root):
                raise error_type(f"插件包名与现有模块链接冲突：{package_name}")
            replace = True
        if replace:
            temporary = parent / f".{parts[-1]}.dsh-managed-{os.getpid()}"
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(target)
            os.replace(temporary, link)
            changed = True
    return changed


def apply_profile(
    runtime: ProfileRuntime,
    user: dict[str, Any],
    error_type: Callable[[str], Exception] = RuntimeError,
) -> bool:
    username = str(user["username"])
    profile_dir = runtime.users_root / username / "state" / "profiles" / "web"
    manifest_path = profile_dir / "package.json"
    if not manifest_path.is_file():
        raise error_type("用户 web profile 尚未初始化")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dsh = manifest.setdefault("dsh", {})
    profile = dsh.setdefault("profile", {})
    bundles = profile.setdefault("bundles", [])
    if not isinstance(bundles, list) or not all(isinstance(value, str) for value in bundles):
        raise error_type("用户 Bundle 列表无效")
    desired = enabled_aliases(runtime.database_path, username)
    retained = [value for value in bundles if not value.startswith(f"{MANAGED_SCOPE}/")]
    updated = [*retained, *desired]
    links_changed = sync_package_links(runtime, user, error_type)
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

from __future__ import annotations

import json
from pathlib import Path

from .registry import validate_package_name, validate_requested_version


MAX_PROCESS_OUTPUT = 32 * 1024


def bounded_output(
    stdout: bytes,
    stderr: bytes,
    limit: int = MAX_PROCESS_OUTPUT,
) -> str:
    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
    if len(combined) > limit:
        combined = combined[-limit:]
        prefix = "…输出过长，仅保留末尾内容…\n"
    else:
        prefix = ""
    return prefix + combined.decode("utf-8", errors="replace").strip()


def installed_package(
    staging: Path,
    expected_name: str | None,
) -> tuple[str, str, Path]:
    project = json.loads((staging / "package.json").read_text(encoding="utf-8"))
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, dict) or len(dependencies) != 1:
        raise ValueError("插件安装结果必须只包含一个直接依赖")
    package_name = next(iter(dependencies))
    validate_package_name(package_name)
    if expected_name is not None and package_name != expected_name:
        raise ValueError("registry 返回的包名与请求不一致")
    package_link = staging / "node_modules" / package_name
    try:
        package_dir = package_link.resolve(strict=True)
    except OSError as error:
        raise ValueError("无法解析已安装插件") from error
    if not package_dir.is_relative_to(staging.resolve()):
        raise ValueError("插件解析结果逃逸共享 release 目录")
    manifest = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    if manifest.get("name") != package_name or not isinstance(manifest.get("version"), str):
        raise ValueError("插件 package.json 名称或版本无效")
    try:
        validate_requested_version(str(manifest["version"]))
    except ValueError as error:
        raise ValueError("插件 package.json 版本无效") from error
    declared = manifest.get("dsh", {}).get("bundle", {}).get("patch")
    if not isinstance(declared, str) or not declared:
        raise ValueError("该 npm 包没有声明 dsh.bundle.patch")
    patch_path = (package_dir / declared).resolve()
    if not patch_path.is_relative_to(package_dir) or not patch_path.is_file():
        raise ValueError("插件声明的 Bundle patch 无效")
    return package_name, str(manifest["version"]), package_link

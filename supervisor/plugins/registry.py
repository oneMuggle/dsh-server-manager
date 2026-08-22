from __future__ import annotations

import re


PACKAGE_PATTERN = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$"
)
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
MAX_DISCOVERED_VERSIONS = 10000


def validate_package_name(value: str) -> str:
    if len(value) > 214 or not PACKAGE_PATTERN.fullmatch(value):
        raise ValueError("无效的 npm 插件名称")
    return value


def validate_requested_version(value: str) -> str:
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("无效的插件版本或 dist-tag")
    return value


def parse_version_metadata(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, dict):
        raise ValueError("registry 未返回插件版本列表")
    raw_versions = metadata.get("versions")
    if isinstance(raw_versions, str):
        raw_versions = [raw_versions]
    if not isinstance(raw_versions, list) or not raw_versions:
        raise ValueError("registry 未返回插件版本列表")
    if len(raw_versions) > MAX_DISCOVERED_VERSIONS:
        raise ValueError("registry 返回的插件版本数量过多")
    versions: list[str] = []
    for value in raw_versions:
        if not isinstance(value, str):
            raise ValueError("registry 返回了无效的插件版本")
        try:
            version = validate_requested_version(value)
        except ValueError as error:
            raise ValueError("registry 返回了无效的插件版本") from error
        if version not in versions:
            versions.append(version)
    latest_value = metadata.get("version")
    if not isinstance(latest_value, str):
        raise ValueError("registry 未返回 latest 版本")
    try:
        latest = validate_requested_version(latest_value)
    except ValueError as error:
        raise ValueError("registry 返回了无效的 latest 版本") from error
    if latest not in versions:
        versions.append(latest)
    return {"versions": versions, "latest": latest}

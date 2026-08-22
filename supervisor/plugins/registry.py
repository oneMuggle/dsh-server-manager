from __future__ import annotations

import re


PACKAGE_PATTERN = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$"
)
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")


def validate_package_name(value: str) -> str:
    if len(value) > 214 or not PACKAGE_PATTERN.fullmatch(value):
        raise ValueError("无效的 npm 插件名称")
    return value


def validate_requested_version(value: str) -> str:
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError("无效的插件版本或 dist-tag")
    return value

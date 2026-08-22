from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import PluginOperationError


OVERLAY_NAME = "dsm-builtins.patch.yml"


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if value[:1] in {"'", '"'}:
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError("expected string")
        return parsed
    return value


def parse_builtin_catalog(output: str) -> list[dict[str, Any]]:
    source_bundle = "official"
    current: dict[str, Any] | None = None
    result: list[dict[str, Any]] = []

    def finish() -> None:
        nonlocal current
        if current is not None and current.get("moduleName"):
            result.append(current)
        current = None

    for raw in output.splitlines():
        if raw.startswith("# == "):
            source_bundle = raw[5:].split(",", 1)[0].strip()
            continue
        if raw.startswith("- id: "):
            finish()
            current = {
                "entryId": _yaml_scalar(raw[6:]),
                "moduleName": "",
                "sourceBundle": source_bundle,
                "defaultEnabled": True,
            }
            continue
        if current is None:
            continue
        if raw.startswith("  name: "):
            current["moduleName"] = _yaml_scalar(raw[8:])
        elif raw == "  disabled: true":
            current["defaultEnabled"] = False
        elif raw == "  disabled: false":
            current["defaultEnabled"] = True
    finish()
    if not result:
        raise PluginOperationError("未发现 DSH 官方内置插件")
    return result


def discover_builtin_catalog(harness_cli: str) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="dsm-builtin-discovery-") as temporary:
        completed = subprocess.run(
            ["/usr/local/bin/node", harness_cli, "web", "--dump-default-config"],
            cwd=temporary,
            env={
                "HOME": temporary,
                "DSH_HOME": temporary,
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise PluginOperationError(f"读取 DSH 官方插件目录失败：{detail[0]}")
    return parse_builtin_catalog(completed.stdout)


def builtin_overlay_path(state_root: Path) -> Path:
    return state_root / OVERLAY_NAME


def write_builtin_overlay(state_root: Path, states: list[dict[str, Any]]) -> Path:
    path = builtin_overlay_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Managed by DSM. Manual changes will be replaced.\n"]
    for state in states:
        if bool(state["enabled"]) == bool(state["defaultEnabled"]):
            continue
        lines.append(f"- id: {json.dumps(str(state['entryId']))}\n")
        lines.append(f"  disabled: {'false' if state['enabled'] else 'true'}\n")
    if len(lines) == 1:
        lines.append("[]\n")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.writelines(lines)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path

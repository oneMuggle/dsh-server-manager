from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RegistryError(RuntimeError):
    pass


def load_registry(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError("unable to read user registry") from error
    if raw.get("version") != 1 or not isinstance(raw.get("users"), dict):
        raise RegistryError("invalid user registry")
    return raw


def get_user(path: Path, username: str) -> dict[str, Any] | None:
    user = load_registry(path)["users"].get(username)
    return user if isinstance(user, dict) else None

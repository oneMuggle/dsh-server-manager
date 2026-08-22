from __future__ import annotations

import os
import pwd
import re
import secrets
import stat
from typing import Any

from common.storage.users import RegistryError, initialize_registry, load_registry

from .config import SupervisorConfig


def load_user_registry(config: SupervisorConfig) -> dict[str, Any]:
    try:
        return load_registry(config.registry_path)
    except RegistryError as error:
        raise RuntimeError("invalid user database") from error


def initialize_gateway_state(
    config: SupervisorConfig,
) -> None:
    gateway = pwd.getpwnam(config.gateway_user)
    config.state_root.mkdir(parents=True, exist_ok=True)
    state_mode = stat.S_IMODE(config.state_root.stat().st_mode)
    os.chmod(config.state_root, state_mode | stat.S_IXOTH)
    config.gateway_root.mkdir(parents=True, exist_ok=True)
    os.chown(config.gateway_root, 0, gateway.pw_gid)
    os.chmod(config.gateway_root, 0o750)
    config.users_root.mkdir(parents=True, exist_ok=True)
    os.chown(config.users_root, 0, 0)
    os.chmod(config.users_root, 0o755)

    initialize_registry(config.registry_path)
    os.chown(config.registry_path, 0, gateway.pw_gid)
    os.chmod(config.registry_path, 0o640)
    if not config.secret_path.exists():
        config.secret_path.write_text(secrets.token_urlsafe(48), encoding="ascii")
    os.chown(config.secret_path, 0, gateway.pw_gid)
    os.chmod(config.secret_path, 0o640)
    config.upload_root.mkdir(parents=True, exist_ok=True)
    os.chown(config.upload_root, gateway.pw_uid, gateway.pw_gid)
    os.chmod(config.upload_root, 0o700)
    for entry in config.upload_root.iterdir():
        try:
            if re.fullmatch(r"[0-9a-f]{32}\.plugin\.tgz", entry.name):
                continue
            if entry.is_file() or entry.is_symlink():
                entry.unlink()
        except FileNotFoundError:
            pass

from __future__ import annotations

import os
import pwd
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .firewall import TenantNetwork


FIRST_UID = 20000
FIRST_PORT = 3101
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_ENV = "DSM_ADMIN_PASSWORD"


def normalize_user_roles(registry: dict[str, Any]) -> bool:
    users = registry["users"]
    changed = False
    for username, user in users.items():
        if not isinstance(user, dict):
            raise RuntimeError("invalid user registry entry")
        expected = "admin" if username == ADMIN_USERNAME else "user"
        if user.get("role") != expected:
            user["role"] = expected
            changed = True
    return changed


def admin_password_from_environment() -> str:
    password = os.environ.get(ADMIN_PASSWORD_ENV)
    if password is None:
        raise RuntimeError(
            f"{ADMIN_PASSWORD_ENV} is required when the admin account does not exist"
        )
    if not 8 <= len(password) <= 256:
        raise RuntimeError(f"{ADMIN_PASSWORD_ENV} must contain 8 to 256 characters")
    return password


def new_user_record(
    registry: dict[str, Any],
    username: str,
    password: dict[str, Any],
) -> dict[str, Any]:
    used_uids = {entry.pw_uid for entry in pwd.getpwall()}
    used_uids.update(int(user["uid"]) for user in registry["users"].values())
    used_ports = {int(user["port"]) for user in registry["users"].values()}
    uid = next(value for value in range(FIRST_UID, 60000) if value not in used_uids)
    port = next(value for value in range(FIRST_PORT, 65536) if value not in used_ports)
    return {
        "username": username,
        "os_user": f"dsh-{username}",
        "uid": uid,
        "port": port,
        "role": "admin" if username == ADMIN_USERNAME else "user",
        "password": password,
        "password_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "disabled": False,
    }


def provision_os_user(user: dict[str, Any], users_root: Path) -> None:
    username = str(user["os_user"])
    uid = int(user["uid"])
    tenant_root = users_root / str(user["username"])
    home = tenant_root / "home"
    workspace = home / "workspace"
    state = tenant_root / "state"

    try:
        existing = pwd.getpwnam(username)
        if existing.pw_uid != uid:
            raise RuntimeError(f"OS user {username} has unexpected UID")
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--uid", str(uid),
                "--user-group",
                "--home-dir", str(home),
                "--no-create-home",
                "--shell", "/usr/sbin/nologin",
                username,
            ],
            check=True,
        )

    group_id = pwd.getpwnam(username).pw_gid
    tenant_root.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    os.chown(tenant_root, 0, 0)
    os.chmod(tenant_root, 0o711)
    for directory in (home, workspace, state):
        os.chown(directory, uid, group_id)
        os.chmod(directory, 0o700)


def network_rules(registry: dict[str, Any]) -> list[TenantNetwork]:
    return [
        TenantNetwork(uid=int(user["uid"]), port=int(user["port"]))
        for user in registry["users"].values()
        if user.get("disabled") is not True
    ]


def valid_password_record(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("algorithm") != "scrypt":
        return False
    try:
        return (
            len(str(value["salt"])) >= 16
            and len(str(value["hash"])) >= 32
            and int(value["n"]) == 1 << 14
            and int(value["r"]) == 8
            and int(value["p"]) == 1
            and int(value["dklen"]) == 64
        )
    except (KeyError, TypeError, ValueError):
        return False

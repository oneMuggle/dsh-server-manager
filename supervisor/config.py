from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _environment_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    state_root: Path
    gateway_root: Path
    users_root: Path
    registry_path: Path
    secret_path: Path
    upload_root: Path
    runtime_root: Path
    control_socket: Path
    install_root: Path
    gateway_user: str
    gateway_port: int
    cookie_secure: bool
    registration_enabled: bool
    harness_cli: str
    max_upload_bytes: int

    @classmethod
    def from_environment(cls) -> "SupervisorConfig":
        state_root = Path(os.environ.get("DSM_STATE_ROOT", "/var/lib/dsh"))
        gateway_root = state_root / "gateway"
        runtime_root = Path("/run/dsh")
        return cls(
            state_root=state_root,
            gateway_root=gateway_root,
            users_root=state_root / "users",
            registry_path=gateway_root / "users.db",
            secret_path=gateway_root / "session-secret",
            upload_root=gateway_root / "uploads",
            runtime_root=runtime_root,
            control_socket=runtime_root / "control.sock",
            install_root=Path("/opt/dsh-server-manager"),
            gateway_user="dsh-gateway",
            gateway_port=8080,
            cookie_secure=_environment_bool("DSM_COOKIE_SECURE", False),
            registration_enabled=_environment_bool(
                "DSM_REGISTRATION_ENABLED", False
            ),
            harness_cli="/opt/deepseek-harness/apps/cli/lib/bin.js",
            max_upload_bytes=100 * 1024 * 1024,
        )

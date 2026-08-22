from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    harness_cli: str
    max_upload_bytes: int

    @classmethod
    def from_environment(cls) -> "SupervisorConfig":
        state_root = Path(os.environ.get("DSH_STATE_ROOT", "/var/lib/dsh"))
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
            harness_cli="/opt/deepseek-harness/apps/cli/lib/bin.js",
            max_upload_bytes=100 * 1024 * 1024,
        )

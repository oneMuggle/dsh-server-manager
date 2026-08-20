from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    state_root: Path
    registry_path: Path
    session_secret_path: Path
    control_socket_path: Path
    cookie_name: str
    cookie_secure: bool
    session_ttl_seconds: int
    max_transform_bytes: int

    @classmethod
    def from_environment(cls) -> "Config":
        state_root = Path(os.environ.get("DSH_STATE_ROOT", "/var/lib/dsh"))
        gateway_root = state_root / "gateway"
        return cls(
            state_root=state_root,
            registry_path=gateway_root / "users.json",
            session_secret_path=gateway_root / "session-secret",
            control_socket_path=Path(
                os.environ.get("DSH_CONTROL_SOCKET", "/run/dsh/control.sock")
            ),
            cookie_name="dsh_session",
            cookie_secure=os.environ.get("DSH_COOKIE_SECURE", "0") == "1",
            session_ttl_seconds=7 * 24 * 60 * 60,
            max_transform_bytes=8 * 1024 * 1024,
        )

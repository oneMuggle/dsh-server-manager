from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def parse_environment_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def parse_environment_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True, slots=True)
class Config:
    state_root: Path
    user_database_path: Path
    session_secret_path: Path
    upload_spool_path: Path
    control_socket_path: Path
    cookie_name: str
    cookie_secure: bool
    registration_enabled: bool
    register_api_key: str
    login_api_key: str
    login_ticket_ttl_seconds: int
    session_ttl_seconds: int
    max_transform_bytes: int
    max_upload_bytes: int

    @classmethod
    def from_environment(cls) -> "Config":
        state_root = Path(os.environ.get("DSM_STATE_ROOT", "/var/lib/dsh"))
        gateway_root = state_root / "gateway"
        return cls(
            state_root=state_root,
            user_database_path=gateway_root / "users.db",
            session_secret_path=gateway_root / "session-secret",
            upload_spool_path=gateway_root / "uploads",
            control_socket_path=Path(
                os.environ.get("DSM_CONTROL_SOCKET", "/run/dsh/control.sock")
            ),
            cookie_name="dsh_session",
            cookie_secure=parse_environment_bool("DSM_COOKIE_SECURE", False),
            registration_enabled=parse_environment_bool(
                "DSM_REGISTRATION_ENABLED", False
            ),
            register_api_key=os.environ.get("DSH_REGISTER_API_KEY", ""),
            login_api_key=os.environ.get("DSH_LOGIN_API_KEY", ""),
            login_ticket_ttl_seconds=parse_environment_int(
                "LOGIN_TICKET_TTL", 60, 10, 300
            ),
            session_ttl_seconds=7 * 24 * 60 * 60,
            max_transform_bytes=8 * 1024 * 1024,
            max_upload_bytes=100 * 1024 * 1024,
        )

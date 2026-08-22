from __future__ import annotations

from pathlib import Path
from typing import Any

from common.storage.assignments import remove_user_plugin, set_user_plugin


def restore_user_assignment(
    database_path: Path,
    username: str,
    plugin_id: int,
    previous: dict[str, Any] | None,
) -> None:
    """Restore the exact persisted assignment after a runtime update fails."""

    if previous is None:
        remove_user_plugin(database_path, username, plugin_id)
        return
    set_user_plugin(
        database_path,
        username,
        plugin_id,
        int(previous["plugin_version_id"]),
        bool(previous["enabled"]),
        state=str(previous["state"]),
        error=previous.get("last_error"),
    )

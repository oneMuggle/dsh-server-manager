from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from common.errors import PluginStoreError

from .database import read_connection, utc_now, write_connection


def get_user_plugin(path: Path, username: str, plugin_id: int) -> dict[str, Any] | None:
    try:
        with read_connection(path) as connection:
            row = connection.execute(
                "SELECT * FROM user_plugins WHERE username = ? AND plugin_id = ?",
                (username, plugin_id),
            ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read user plugin") from error


def apply_plugin_defaults(path: Path, username: str) -> int:
    try:
        with write_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT plugin_id, plugin_version_id, enable_for_new_users
                FROM plugin_defaults WHERE assign_to_new_users = 1
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO user_plugins (
                        username, plugin_id, plugin_version_id, enabled,
                        state, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?)
                    ON CONFLICT(username, plugin_id) DO NOTHING
                    """,
                    (
                        username,
                        row["plugin_id"],
                        row["plugin_version_id"],
                        row["enable_for_new_users"],
                        utc_now(),
                    ),
                )
            return len(rows)
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to apply plugin defaults") from error


def set_user_plugin(
    path: Path,
    username: str,
    plugin_id: int,
    version_id: int,
    enabled: bool,
    *,
    state: str = "ready",
    error: str | None = None,
) -> None:
    try:
        with write_connection(path) as connection:
            connection.execute(
                """
                INSERT INTO user_plugins (
                    username, plugin_id, plugin_version_id, enabled,
                    state, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(username, plugin_id) DO UPDATE SET
                    plugin_version_id = excluded.plugin_version_id,
                    enabled = excluded.enabled,
                    state = excluded.state,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (username, plugin_id, version_id, int(enabled), state, error, utc_now()),
            )
    except sqlite3.DatabaseError as db_error:
        raise PluginStoreError("unable to update user plugin") from db_error


def remove_user_plugin(path: Path, username: str, plugin_id: int) -> None:
    try:
        with write_connection(path) as connection:
            connection.execute(
                "DELETE FROM user_plugins WHERE username = ? AND plugin_id = ?",
                (username, plugin_id),
            )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to remove user plugin") from error


def set_plugin_default(
    path: Path,
    plugin_id: int,
    version_id: int,
    *,
    assigned: bool,
    enabled: bool,
) -> None:
    try:
        with write_connection(path) as connection:
            if not assigned:
                connection.execute(
                    "DELETE FROM plugin_defaults WHERE plugin_id = ?", (plugin_id,)
                )
            else:
                connection.execute(
                    """
                    INSERT INTO plugin_defaults (
                        plugin_id, plugin_version_id, assign_to_new_users,
                        enable_for_new_users, updated_at
                    ) VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(plugin_id) DO UPDATE SET
                        plugin_version_id = excluded.plugin_version_id,
                        assign_to_new_users = 1,
                        enable_for_new_users = excluded.enable_for_new_users,
                        updated_at = excluded.updated_at
                    """,
                    (plugin_id, version_id, int(enabled), utc_now()),
                )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to update plugin default") from error


def enabled_aliases(path: Path, username: str) -> list[str]:
    try:
        with read_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT pv.resolver_alias
                FROM user_plugins up
                JOIN plugin_versions pv ON pv.id = up.plugin_version_id
                WHERE up.username = ? AND up.enabled = 1 AND up.state != 'failed'
                ORDER BY up.updated_at, up.plugin_id
                """,
                (username,),
            ).fetchall()
        return [str(row[0]) for row in rows]
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read enabled plugins") from error


def enabled_package_links(path: Path, username: str) -> list[dict[str, str]]:
    try:
        with read_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT p.package_name, pv.release_path
                FROM user_plugins up
                JOIN plugins p ON p.id = up.plugin_id
                JOIN plugin_versions pv ON pv.id = up.plugin_version_id
                WHERE up.username = ? AND up.enabled = 1 AND up.state != 'failed'
                ORDER BY up.updated_at, up.plugin_id
                """,
                (username,),
            ).fetchall()
        return [
            {
                "package_name": str(row["package_name"]),
                "release_path": str(row["release_path"]),
            }
            for row in rows
        ]
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read enabled plugin packages") from error


def mark_user_plugins_ready(path: Path, username: str) -> None:
    try:
        with write_connection(path) as connection:
            connection.execute(
                """
                UPDATE user_plugins
                SET state = 'ready', last_error = NULL, updated_at = ?
                WHERE username = ? AND state = 'pending'
                """,
                (utc_now(), username),
            )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to finalize user plugins") from error

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from common.errors import PluginStoreError

from .database import read_connection, utc_now, write_connection


def replace_builtin_catalog(path: Path, catalog: list[dict[str, Any]]) -> None:
    try:
        with write_connection(path) as connection:
            retained: list[str] = []
            for item in catalog:
                entry_id = str(item["entryId"])
                retained.append(entry_id)
                connection.execute(
                    """
                    INSERT INTO builtin_plugins (
                        entry_id, module_name, source_bundle, default_enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(entry_id) DO UPDATE SET
                        module_name = excluded.module_name,
                        source_bundle = excluded.source_bundle,
                        default_enabled = excluded.default_enabled,
                        updated_at = excluded.updated_at
                    """,
                    (
                        entry_id,
                        str(item["moduleName"]),
                        str(item["sourceBundle"]),
                        int(item["defaultEnabled"] is True),
                        utc_now(),
                    ),
                )
            if retained:
                placeholders = ",".join("?" for _ in retained)
                connection.execute(
                    f"DELETE FROM builtin_plugins WHERE entry_id NOT IN ({placeholders})",
                    retained,
                )
            else:
                connection.execute("DELETE FROM builtin_plugins")
    except (KeyError, sqlite3.DatabaseError) as error:
        raise PluginStoreError("unable to update builtin plugin catalog") from error


def get_builtin_plugin(path: Path, entry_id: str) -> dict[str, Any] | None:
    try:
        with read_connection(path) as connection:
            row = connection.execute(
                "SELECT * FROM builtin_plugins WHERE entry_id = ?", (entry_id,)
            ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read builtin plugin") from error


def get_user_builtin_plugin(path: Path, username: str, entry_id: str) -> bool | None:
    try:
        with read_connection(path) as connection:
            row = connection.execute(
                "SELECT enabled FROM user_builtin_plugins WHERE username = ? AND entry_id = ?",
                (username, entry_id),
            ).fetchone()
        return bool(row["enabled"]) if row is not None else None
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read builtin plugin state") from error


def set_user_builtin_plugin(path: Path, username: str, entry_id: str, enabled: bool) -> None:
    try:
        with write_connection(path) as connection:
            connection.execute(
                """
                INSERT INTO user_builtin_plugins (username, entry_id, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username, entry_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (username, entry_id, int(enabled), utc_now()),
            )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to update builtin plugin state") from error


def remove_user_builtin_plugin(path: Path, username: str, entry_id: str) -> None:
    try:
        with write_connection(path) as connection:
            connection.execute(
                "DELETE FROM user_builtin_plugins WHERE username = ? AND entry_id = ?",
                (username, entry_id),
            )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to restore builtin plugin state") from error


def set_builtin_default(path: Path, entry_id: str, enabled: bool) -> None:
    try:
        with write_connection(path) as connection:
            connection.execute(
                """
                INSERT INTO builtin_plugin_defaults (entry_id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (entry_id, int(enabled), utc_now()),
            )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to update builtin plugin default") from error


def apply_builtin_defaults(path: Path, username: str) -> int:
    try:
        with write_connection(path) as connection:
            rows = connection.execute(
                "SELECT entry_id, enabled FROM builtin_plugin_defaults"
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO user_builtin_plugins (username, entry_id, enabled, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(username, entry_id) DO NOTHING
                    """,
                    (username, row["entry_id"], row["enabled"], utc_now()),
                )
            return len(rows)
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to apply builtin plugin defaults") from error


def effective_builtin_states(path: Path, username: str) -> list[dict[str, Any]]:
    try:
        with read_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT bp.entry_id, bp.default_enabled,
                       COALESCE(ubp.enabled, bp.default_enabled) AS enabled
                FROM builtin_plugins bp
                LEFT JOIN user_builtin_plugins ubp
                  ON ubp.entry_id = bp.entry_id AND ubp.username = ?
                ORDER BY bp.entry_id
                """,
                (username,),
            ).fetchall()
        return [
            {
                "entryId": str(row["entry_id"]),
                "defaultEnabled": bool(row["default_enabled"]),
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read builtin plugin states") from error


def list_builtin_plugins(path: Path) -> list[dict[str, Any]]:
    try:
        with read_connection(path) as connection:
            plugins = connection.execute(
                "SELECT * FROM builtin_plugins ORDER BY entry_id"
            ).fetchall()
            users = connection.execute("SELECT username FROM users ORDER BY username").fetchall()
            overrides = {
                (str(row["username"]), str(row["entry_id"])): bool(row["enabled"])
                for row in connection.execute("SELECT username, entry_id, enabled FROM user_builtin_plugins")
            }
            defaults = {
                str(row["entry_id"]): bool(row["enabled"])
                for row in connection.execute("SELECT entry_id, enabled FROM builtin_plugin_defaults")
            }
        result: list[dict[str, Any]] = []
        for plugin in plugins:
            entry_id = str(plugin["entry_id"])
            default_enabled = bool(plugin["default_enabled"])
            states = [
                {
                    "username": str(user["username"]),
                    "enabled": overrides.get((str(user["username"]), entry_id), default_enabled),
                }
                for user in users
            ]
            result.append(
                {
                    "entryId": entry_id,
                    "moduleName": str(plugin["module_name"]),
                    "sourceBundle": str(plugin["source_bundle"]),
                    "defaultEnabled": default_enabled,
                    "enabledUsers": sum(item["enabled"] for item in states),
                    "users": states,
                    "futureDefault": defaults.get(entry_id, default_enabled),
                    "removable": False,
                }
            )
        return result
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to list builtin plugins") from error

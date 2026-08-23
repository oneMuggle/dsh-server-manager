from __future__ import annotations

import sqlite3
from pathlib import Path

from common.errors import PluginStoreError

from .database import read_connection, utc_now, write_connection


PLUGIN_NPM_REGISTRY_KEY = "plugins.npm_registry"


def create_settings_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def get_setting(path: Path, key: str) -> str | None:
    try:
        with read_connection(path) as connection:
            row = connection.execute(
                "SELECT value FROM system_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row is not None else None
    except (OSError, sqlite3.DatabaseError) as error:
        raise PluginStoreError("unable to read system setting") from error


def set_setting(path: Path, key: str, value: str | None) -> None:
    try:
        with write_connection(path) as connection:
            if value is None:
                connection.execute("DELETE FROM system_settings WHERE key = ?", (key,))
            else:
                connection.execute(
                    """
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, utc_now()),
                )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to update system setting") from error


def get_plugin_npm_registry(path: Path) -> str | None:
    return get_setting(path, PLUGIN_NPM_REGISTRY_KEY)


def set_plugin_npm_registry(path: Path, value: str | None) -> None:
    set_setting(path, PLUGIN_NPM_REGISTRY_KEY, value)

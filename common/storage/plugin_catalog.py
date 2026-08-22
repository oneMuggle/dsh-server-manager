from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from common.errors import PluginStoreError

from .database import read_connection, utc_now, write_connection


def create_plugin_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS plugins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_name TEXT NOT NULL UNIQUE,
            display_name TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS plugin_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plugin_id INTEGER NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK (source_type IN ('registry', 'offline')),
            artifact_sha256 TEXT NOT NULL,
            release_path TEXT NOT NULL,
            resolver_alias TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'failed')),
            created_at TEXT NOT NULL,
            UNIQUE(plugin_id, version, artifact_sha256)
        );

        CREATE TABLE IF NOT EXISTS user_plugins (
            username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
            plugin_id INTEGER NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
            plugin_version_id INTEGER NOT NULL REFERENCES plugin_versions(id) ON DELETE RESTRICT,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'pending', 'failed')),
            last_error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(username, plugin_id)
        );

        CREATE TABLE IF NOT EXISTS plugin_defaults (
            plugin_id INTEGER PRIMARY KEY REFERENCES plugins(id) ON DELETE CASCADE,
            plugin_version_id INTEGER NOT NULL REFERENCES plugin_versions(id) ON DELETE RESTRICT,
            assign_to_new_users INTEGER NOT NULL DEFAULT 0 CHECK (assign_to_new_users IN (0, 1)),
            enable_for_new_users INTEGER NOT NULL DEFAULT 0 CHECK (enable_for_new_users IN (0, 1)),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS plugin_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('online_import', 'offline_import', 'assign')),
            action TEXT,
            package_name TEXT,
            requested_version TEXT,
            plugin_id INTEGER REFERENCES plugins(id) ON DELETE SET NULL,
            plugin_version_id INTEGER REFERENCES plugin_versions(id) ON DELETE SET NULL,
            scope_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'partial_failed', 'failed')),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT,
            result_json TEXT
        );

        CREATE TABLE IF NOT EXISTS plugin_job_items (
            job_id TEXT NOT NULL REFERENCES plugin_jobs(id) ON DELETE CASCADE,
            username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'skipped')),
            error TEXT,
            started_at TEXT,
            finished_at TEXT,
            PRIMARY KEY(job_id, username)
        );

        CREATE INDEX IF NOT EXISTS plugin_versions_plugin_idx
            ON plugin_versions(plugin_id, created_at);
        CREATE INDEX IF NOT EXISTS user_plugins_state_idx
            ON user_plugins(state, username);
        CREATE INDEX IF NOT EXISTS plugin_jobs_created_idx
            ON plugin_jobs(created_at DESC);
        CREATE INDEX IF NOT EXISTS plugin_job_items_status_idx
            ON plugin_job_items(job_id, status);
        """
    )


def register_plugin_version(
    path: Path,
    *,
    package_name: str,
    version: str,
    source_type: str,
    artifact_sha256: str,
    release_path: str,
    resolver_alias: str,
) -> tuple[int, int]:
    try:
        with write_connection(path) as connection:
            connection.execute(
                "INSERT INTO plugins (package_name, created_at) VALUES (?, ?) ON CONFLICT(package_name) DO NOTHING",
                (package_name, utc_now()),
            )
            plugin_id = int(
                connection.execute(
                    "SELECT id FROM plugins WHERE package_name = ?", (package_name,)
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO plugin_versions (
                    plugin_id, version, source_type, artifact_sha256,
                    release_path, resolver_alias, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id, version, artifact_sha256) DO UPDATE SET
                    source_type = excluded.source_type,
                    release_path = excluded.release_path,
                    resolver_alias = excluded.resolver_alias,
                    status = 'ready'
                """,
                (
                    plugin_id,
                    version,
                    source_type,
                    artifact_sha256,
                    release_path,
                    resolver_alias,
                    utc_now(),
                ),
            )
            version_id = int(
                connection.execute(
                    """
                    SELECT id FROM plugin_versions
                    WHERE plugin_id = ? AND version = ? AND artifact_sha256 = ?
                    """,
                    (plugin_id, version, artifact_sha256),
                ).fetchone()[0]
            )
            return plugin_id, version_id
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to register plugin version") from error


def get_plugin_version(path: Path, version_id: int) -> dict[str, Any] | None:
    try:
        with read_connection(path) as connection:
            row = connection.execute(
                """
                SELECT pv.*, p.package_name
                FROM plugin_versions pv JOIN plugins p ON p.id = pv.plugin_id
                WHERE pv.id = ? AND pv.status = 'ready'
                """,
                (version_id,),
            ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read plugin version") from error


def list_plugin_versions(path: Path) -> list[dict[str, Any]]:
    try:
        with read_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT pv.*, p.package_name
                FROM plugin_versions pv JOIN plugins p ON p.id = pv.plugin_id
                WHERE pv.status = 'ready' ORDER BY pv.id
                """
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read plugin versions") from error


def list_plugins(path: Path) -> list[dict[str, Any]]:
    try:
        with read_connection(path) as connection:
            plugins = connection.execute(
                """
                SELECT p.id, p.package_name, p.display_name,
                       COUNT(DISTINCT up.username) AS assigned_users,
                       COUNT(DISTINCT CASE WHEN up.enabled = 1 THEN up.username END) AS enabled_users
                FROM plugins p
                LEFT JOIN user_plugins up ON up.plugin_id = p.id
                GROUP BY p.id
                ORDER BY p.package_name
                """
            ).fetchall()
            result: list[dict[str, Any]] = []
            for plugin in plugins:
                versions = connection.execute(
                    """
                    SELECT id, version, source_type, artifact_sha256, resolver_alias, created_at
                    FROM plugin_versions WHERE plugin_id = ? AND status = 'ready'
                    ORDER BY created_at DESC
                    """,
                    (plugin["id"],),
                ).fetchall()
                users = connection.execute(
                    """
                    SELECT up.username, up.plugin_version_id, up.enabled, up.state,
                           up.last_error, pv.version
                    FROM user_plugins up
                    JOIN plugin_versions pv ON pv.id = up.plugin_version_id
                    WHERE up.plugin_id = ? ORDER BY up.username
                    """,
                    (plugin["id"],),
                ).fetchall()
                default = connection.execute(
                    "SELECT * FROM plugin_defaults WHERE plugin_id = ?", (plugin["id"],)
                ).fetchone()
                result.append(
                    {
                        "id": int(plugin["id"]),
                        "packageName": str(plugin["package_name"]),
                        "displayName": plugin["display_name"],
                        "assignedUsers": int(plugin["assigned_users"]),
                        "enabledUsers": int(plugin["enabled_users"]),
                        "versions": [dict(row) for row in versions],
                        "users": [
                            {
                                "username": row["username"],
                                "versionId": row["plugin_version_id"],
                                "version": row["version"],
                                "enabled": bool(row["enabled"]),
                                "state": row["state"],
                                "error": row["last_error"],
                            }
                            for row in users
                        ],
                        "default": (
                            {
                                "versionId": int(default["plugin_version_id"]),
                                "enabled": bool(default["enable_for_new_users"]),
                            }
                            if default is not None and default["assign_to_new_users"]
                            else None
                        ),
                    }
                )
            return result
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to list plugins") from error

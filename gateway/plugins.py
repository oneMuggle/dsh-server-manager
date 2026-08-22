from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


PLUGIN_JOB_KINDS = {"online_import", "offline_import", "assign"}
PLUGIN_ACTIONS = {"install", "uninstall", "enable", "disable"}


class PluginStoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _read_connection(path: Path) -> Iterator[sqlite3.Connection]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def _write_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


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


def create_job(
    path: Path,
    *,
    job_id: str,
    kind: str,
    created_by: str,
    action: str | None = None,
    package_name: str | None = None,
    requested_version: str | None = None,
    plugin_id: int | None = None,
    plugin_version_id: int | None = None,
    scope: dict[str, Any] | None = None,
    usernames: list[str] | None = None,
) -> None:
    if kind not in PLUGIN_JOB_KINDS or (action is not None and action not in PLUGIN_ACTIONS):
        raise PluginStoreError("invalid plugin job")
    try:
        with _write_connection(path) as connection:
            connection.execute(
                """
                INSERT INTO plugin_jobs (
                    id, kind, action, package_name, requested_version,
                    plugin_id, plugin_version_id, scope_json, status,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    job_id,
                    kind,
                    action,
                    package_name,
                    requested_version,
                    plugin_id,
                    plugin_version_id,
                    json.dumps(scope or {}, separators=(",", ":"), sort_keys=True),
                    created_by,
                    _now(),
                ),
            )
            for username in usernames or []:
                connection.execute(
                    "INSERT INTO plugin_job_items (job_id, username, status) VALUES (?, ?, 'queued')",
                    (job_id, username),
                )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to create plugin job") from error


def set_job_status(
    path: Path,
    job_id: str,
    status: str,
    *,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    timestamps: dict[str, str] = {}
    if status == "running":
        timestamps["started_at"] = _now()
    if status in {"completed", "partial_failed", "failed"}:
        timestamps["finished_at"] = _now()
    assignments = ["status = ?", "error = ?", "result_json = ?"]
    values: list[Any] = [status, error, json.dumps(result) if result is not None else None]
    for column, value in timestamps.items():
        assignments.append(f"{column} = ?")
        values.append(value)
    values.append(job_id)
    try:
        with _write_connection(path) as connection:
            connection.execute(
                f"UPDATE plugin_jobs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
    except sqlite3.DatabaseError as db_error:
        raise PluginStoreError("unable to update plugin job") from db_error


def set_job_item_status(
    path: Path,
    job_id: str,
    username: str,
    status: str,
    error: str | None = None,
) -> None:
    started = _now() if status == "running" else None
    finished = _now() if status in {"completed", "failed", "skipped"} else None
    try:
        with _write_connection(path) as connection:
            connection.execute(
                """
                UPDATE plugin_job_items
                SET status = ?, error = ?,
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at)
                WHERE job_id = ? AND username = ?
                """,
                (status, error, started, finished, job_id, username),
            )
    except sqlite3.DatabaseError as db_error:
        raise PluginStoreError("unable to update plugin job item") from db_error


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
        with _write_connection(path) as connection:
            connection.execute(
                "INSERT INTO plugins (package_name, created_at) VALUES (?, ?) ON CONFLICT(package_name) DO NOTHING",
                (package_name, _now()),
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
                    _now(),
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
        with _read_connection(path) as connection:
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
        with _read_connection(path) as connection:
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


def get_user_plugin(path: Path, username: str, plugin_id: int) -> dict[str, Any] | None:
    try:
        with _read_connection(path) as connection:
            row = connection.execute(
                "SELECT * FROM user_plugins WHERE username = ? AND plugin_id = ?",
                (username, plugin_id),
            ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to read user plugin") from error


def apply_plugin_defaults(path: Path, username: str) -> int:
    try:
        with _write_connection(path) as connection:
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
                        _now(),
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
        with _write_connection(path) as connection:
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
                (username, plugin_id, version_id, int(enabled), state, error, _now()),
            )
    except sqlite3.DatabaseError as db_error:
        raise PluginStoreError("unable to update user plugin") from db_error


def remove_user_plugin(path: Path, username: str, plugin_id: int) -> None:
    try:
        with _write_connection(path) as connection:
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
        with _write_connection(path) as connection:
            if not assigned:
                connection.execute("DELETE FROM plugin_defaults WHERE plugin_id = ?", (plugin_id,))
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
                    (plugin_id, version_id, int(enabled), _now()),
                )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to update plugin default") from error


def enabled_aliases(path: Path, username: str) -> list[str]:
    try:
        with _read_connection(path) as connection:
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
        with _read_connection(path) as connection:
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
        with _write_connection(path) as connection:
            connection.execute(
                """
                UPDATE user_plugins
                SET state = 'ready', last_error = NULL, updated_at = ?
                WHERE username = ? AND state = 'pending'
                """,
                (_now(), username),
            )
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to finalize user plugins") from error


def list_plugins(path: Path) -> list[dict[str, Any]]:
    try:
        with _read_connection(path) as connection:
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


def get_job(path: Path, job_id: str) -> dict[str, Any] | None:
    try:
        with _read_connection(path) as connection:
            job = connection.execute("SELECT * FROM plugin_jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                return None
            items = connection.execute(
                "SELECT username, status, error, started_at, finished_at FROM plugin_job_items WHERE job_id = ? ORDER BY username",
                (job_id,),
            ).fetchall()
        result = dict(job)
        result["scope"] = json.loads(result.pop("scope_json"))
        result_json = result.pop("result_json")
        result["result"] = json.loads(result_json) if result_json else None
        result["items"] = [dict(row) for row in items]
        return result
    except (json.JSONDecodeError, sqlite3.DatabaseError) as error:
        raise PluginStoreError("unable to read plugin job") from error


def list_jobs(path: Path, limit: int = 30) -> list[dict[str, Any]]:
    try:
        with _read_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT id, kind, action, package_name, requested_version,
                       plugin_id, plugin_version_id, status, created_by,
                       created_at, started_at, finished_at, error
                FROM plugin_jobs ORDER BY created_at DESC LIMIT ?
                """,
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to list plugin jobs") from error


def unfinished_job_ids(path: Path) -> list[str]:
    try:
        with _write_connection(path) as connection:
            connection.execute(
                "UPDATE plugin_jobs SET status = 'queued', started_at = NULL WHERE status = 'running'"
            )
            connection.execute(
                "UPDATE plugin_job_items SET status = 'queued', started_at = NULL WHERE status = 'running'"
            )
            rows = connection.execute(
                "SELECT id FROM plugin_jobs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [str(row[0]) for row in rows]
    except sqlite3.DatabaseError as error:
        raise PluginStoreError("unable to recover plugin jobs") from error

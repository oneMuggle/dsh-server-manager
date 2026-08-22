from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from common.errors import PluginStoreError

from .database import read_connection, utc_now, write_connection


PLUGIN_JOB_KINDS = {"online_import", "offline_import", "assign"}
PLUGIN_ACTIONS = {"install", "uninstall", "enable", "disable"}


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
        with write_connection(path) as connection:
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
                    utc_now(),
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
        timestamps["started_at"] = utc_now()
    if status in {"completed", "partial_failed", "failed"}:
        timestamps["finished_at"] = utc_now()
    assignments = ["status = ?", "error = ?", "result_json = ?"]
    values: list[Any] = [status, error, json.dumps(result) if result is not None else None]
    for column, value in timestamps.items():
        assignments.append(f"{column} = ?")
        values.append(value)
    values.append(job_id)
    try:
        with write_connection(path) as connection:
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
    started = utc_now() if status == "running" else None
    finished = utc_now() if status in {"completed", "failed", "skipped"} else None
    try:
        with write_connection(path) as connection:
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


def get_job(path: Path, job_id: str) -> dict[str, Any] | None:
    try:
        with read_connection(path) as connection:
            job = connection.execute(
                "SELECT * FROM plugin_jobs WHERE id = ?", (job_id,)
            ).fetchone()
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
        with read_connection(path) as connection:
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
        with write_connection(path) as connection:
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

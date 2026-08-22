from __future__ import annotations

import sqlite3


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

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .plugins import create_plugin_schema

SCHEMA_VERSION = 2


class RegistryError(RuntimeError):
    pass


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


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            os_user TEXT NOT NULL UNIQUE,
            uid INTEGER NOT NULL UNIQUE,
            port INTEGER NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
            password TEXT NOT NULL,
            password_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1))
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS users_created_at_idx ON users(created_at, username)"
    )
    create_plugin_schema(connection)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _validate_registry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("invalid user registry")
    if value.get("version") != 1 or not isinstance(value.get("users"), dict):
        raise RegistryError("invalid user registry")
    return value


def _user_values(user: dict[str, Any]) -> tuple[Any, ...]:
    try:
        role = str(user["role"])
        if role not in {"admin", "user"}:
            raise ValueError("invalid role")
        return (
            str(user["username"]),
            str(user["os_user"]),
            int(user["uid"]),
            int(user["port"]),
            role,
            json.dumps(user["password"], separators=(",", ":"), sort_keys=True),
            int(user["password_version"]),
            str(user["created_at"]),
            int(user.get("disabled") is True),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RegistryError("invalid user registry entry") from error


def _insert_user(connection: sqlite3.Connection, user: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO users (
            username, os_user, uid, port, role, password,
            password_version, created_at, disabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _user_values(user),
    )


def initialize_registry(path: Path, legacy_json_path: Path | None = None) -> bool:
    """Create the SQLite registry and import a legacy JSON registry once.

    Returns True when at least one legacy user was imported. The JSON file is
    intentionally retained as a migration backup.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _write_connection(path) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1, SCHEMA_VERSION}:
                raise RegistryError(f"unsupported user database version: {version}")
            _create_schema(connection)
            user_count = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            if user_count or legacy_json_path is None or not legacy_json_path.exists():
                return False
            try:
                legacy_registry = _validate_registry(
                    json.loads(legacy_json_path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError) as error:
                raise RegistryError("unable to read legacy user registry") from error
            for user in legacy_registry["users"].values():
                if not isinstance(user, dict):
                    raise RegistryError("invalid user registry entry")
                _insert_user(connection, user)
            return bool(legacy_registry["users"])
    except sqlite3.DatabaseError as error:
        raise RegistryError("unable to initialize user database") from error


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    try:
        password = json.loads(str(row["password"]))
    except json.JSONDecodeError as error:
        raise RegistryError("invalid password record in user database") from error
    return {
        "username": str(row["username"]),
        "os_user": str(row["os_user"]),
        "uid": int(row["uid"]),
        "port": int(row["port"]),
        "role": str(row["role"]),
        "password": password,
        "password_version": int(row["password_version"]),
        "created_at": str(row["created_at"]),
        "disabled": bool(row["disabled"]),
    }


def load_registry(path: Path) -> dict[str, Any]:
    try:
        with _read_connection(path) as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY created_at, username"
            ).fetchall()
        return {
            "version": 1,
            "users": {str(row["username"]): _row_to_user(row) for row in rows},
        }
    except (OSError, sqlite3.DatabaseError) as error:
        raise RegistryError("unable to read user database") from error


def get_user(path: Path, username: str) -> dict[str, Any] | None:
    try:
        with _read_connection(path) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return _row_to_user(row) if row is not None else None
    except (OSError, sqlite3.DatabaseError) as error:
        raise RegistryError("unable to read user database") from error


def list_users(path: Path) -> list[dict[str, Any]]:
    try:
        with _read_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT username, role, disabled, created_at
                FROM users
                ORDER BY created_at, username
                """
            ).fetchall()
        return [
            {
                "username": str(row["username"]),
                "role": str(row["role"]),
                "disabled": bool(row["disabled"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
    except (OSError, sqlite3.DatabaseError) as error:
        raise RegistryError("unable to read user database") from error


def add_user(path: Path, user: dict[str, Any]) -> None:
    try:
        with _write_connection(path) as connection:
            _insert_user(connection, user)
    except sqlite3.IntegrityError as error:
        raise RegistryError("user database constraint violation") from error
    except sqlite3.DatabaseError as error:
        raise RegistryError("unable to update user database") from error


def replace_registry(path: Path, registry: dict[str, Any]) -> None:
    registry = _validate_registry(registry)
    try:
        with _write_connection(path) as connection:
            retained: list[str] = []
            for user in registry["users"].values():
                if not isinstance(user, dict):
                    raise RegistryError("invalid user registry entry")
                values = _user_values(user)
                retained.append(str(user["username"]))
                connection.execute(
                    """
                    INSERT INTO users (
                        username, os_user, uid, port, role, password,
                        password_version, created_at, disabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET
                        os_user = excluded.os_user,
                        uid = excluded.uid,
                        port = excluded.port,
                        role = excluded.role,
                        password = excluded.password,
                        password_version = excluded.password_version,
                        created_at = excluded.created_at,
                        disabled = excluded.disabled
                    """,
                    values,
                )
            if retained:
                placeholders = ",".join("?" for _ in retained)
                connection.execute(
                    f"DELETE FROM users WHERE username NOT IN ({placeholders})",
                    retained,
                )
            else:
                connection.execute("DELETE FROM users")
    except sqlite3.DatabaseError as error:
        raise RegistryError("unable to update user database") from error

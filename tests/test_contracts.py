from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway.main import app
from gateway.registry import initialize_registry
from gateway.supervisor_client import SupervisorError, _request


PUBLIC_RESPONSES = {
    ("get", "/api/gateway/v1/admin/plugin-jobs"): ["200", "422"],
    ("post", "/api/gateway/v1/admin/plugin-jobs"): ["202", "422"],
    ("get", "/api/gateway/v1/admin/plugin-jobs/{job_id}"): ["200", "422"],
    ("get", "/api/gateway/v1/admin/plugins"): ["200"],
    ("post", "/api/gateway/v1/admin/plugins/offline"): ["202"],
    ("post", "/api/gateway/v1/admin/plugins/online"): ["202", "422"],
    ("get", "/api/gateway/v1/admin/plugins/registry-versions"): ["200", "422"],
    ("get", "/api/gateway/v1/admin/users"): ["200"],
    ("post", "/api/gateway/v1/admin/users/{username}/providers"): ["201", "422"],
    ("post", "/api/gateway/v1/auth/login"): ["200", "422"],
    ("post", "/api/gateway/v1/auth/logout"): ["204"],
    ("get", "/api/gateway/v1/auth/me"): ["200"],
    ("post", "/api/gateway/v1/auth/register"): ["201", "422"],
    ("get", "/api/gateway/v1/files/directories"): ["200", "422"],
    ("post", "/api/gateway/v1/files/upload"): ["200", "422"],
    ("get", "/api/gateway/v1/health"): ["200"],
}

SCHEMA_PROPERTIES = {
    "AuthenticatedUser": ["accessToken", "expiresIn", "role", "tokenType", "username"],
    "CreateCustomProvider": ["api", "apiKey", "baseURL", "displayName", "models", "providerId"],
    "Credentials": ["password", "username"],
    "CurrentUser": ["role", "username"],
    "DirectoryListing": ["directories", "directory", "truncated"],
    "OnlinePluginImport": ["packageName", "version"],
    "PluginAssignment": [
        "action",
        "applyToFutureUsers",
        "enabledAfterInstall",
        "pluginId",
        "scope",
        "versionId",
    ],
    "PluginScope": ["includeDisabledUsers", "type", "usernames"],
    "RegisteredUser": ["created_at", "disabled", "role", "username"],
    "RegisteredUsers": ["users"],
}

TABLE_COLUMNS = {
    "users": [
        "username",
        "os_user",
        "uid",
        "port",
        "role",
        "password",
        "password_version",
        "created_at",
        "disabled",
    ],
    "plugin_versions": [
        "id",
        "plugin_id",
        "version",
        "source_type",
        "artifact_sha256",
        "release_path",
        "resolver_alias",
        "status",
        "created_at",
    ],
    "user_plugins": [
        "username",
        "plugin_id",
        "plugin_version_id",
        "enabled",
        "state",
        "last_error",
        "updated_at",
    ],
    "plugin_jobs": [
        "id",
        "kind",
        "action",
        "package_name",
        "requested_version",
        "plugin_id",
        "plugin_version_id",
        "scope_json",
        "status",
        "created_by",
        "created_at",
        "started_at",
        "finished_at",
        "error",
        "result_json",
    ],
    "plugin_job_items": [
        "job_id",
        "username",
        "status",
        "error",
        "started_at",
        "finished_at",
    ],
}


class OpenApiContractTests(unittest.TestCase):
    def test_public_paths_methods_and_response_statuses_are_stable(self) -> None:
        schema = app.openapi()
        actual = {
            (method, path): sorted(operation["responses"])
            for path, path_item in schema["paths"].items()
            for method, operation in path_item.items()
        }
        self.assertEqual(actual, PUBLIC_RESPONSES)

    def test_security_schemes_and_public_model_fields_are_stable(self) -> None:
        schema = app.openapi()
        self.assertEqual(
            schema["components"]["securitySchemes"],
            {
                "APIKeyCookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "dsh_session",
                },
                "HTTPBearer": {"type": "http", "scheme": "bearer"},
            },
        )
        schemas = schema["components"]["schemas"]
        for name, expected in SCHEMA_PROPERTIES.items():
            with self.subTest(schema=name):
                self.assertEqual(sorted(schemas[name]["properties"]), expected)


class SqliteContractTests(unittest.TestCase):
    def test_persisted_table_columns_and_schema_version_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "users.db"
            initialize_registry(database)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                for table, expected in TABLE_COLUMNS.items():
                    with self.subTest(table=table):
                        columns = [
                            row[1]
                            for row in connection.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        ]
                        self.assertEqual(columns, expected)


class _MemoryWriter:
    def __init__(self) -> None:
        self.body = bytearray()
        self.closed = False

    def write(self, value: bytes) -> None:
        self.body.extend(value)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class SupervisorWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_uses_compact_newline_delimited_json(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"ok":true,"username":"alice"}\n')
        reader.feed_eof()
        writer = _MemoryWriter()

        with patch(
            "gateway.supervisor_client.asyncio.open_unix_connection",
            return_value=(reader, writer),
        ):
            response = await _request(
                Path("/run/dsh/control.sock"),
                {"action": "create_user", "username": "alice"},
                timeout=1,
            )

        self.assertEqual(
            bytes(writer.body),
            b'{"action":"create_user","username":"alice"}\n',
        )
        self.assertEqual(response, {"ok": True, "username": "alice"})
        self.assertTrue(writer.closed)

    async def test_error_code_and_message_survive_the_wire_boundary(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(
            json.dumps(
                {"ok": False, "code": "conflict", "message": "exists"},
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        reader.feed_eof()
        writer = _MemoryWriter()

        with patch(
            "gateway.supervisor_client.asyncio.open_unix_connection",
            return_value=(reader, writer),
        ):
            with self.assertRaises(SupervisorError) as caught:
                await _request(
                    Path("/run/dsh/control.sock"),
                    {"action": "create_user", "username": "alice"},
                    timeout=1,
                )

        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(str(caught.exception), "exists")
        self.assertTrue(writer.closed)


if __name__ == "__main__":
    unittest.main()

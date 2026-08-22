from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.contracts.control import (
    AssignPluginRequest,
    ControlRequestError,
    CreateUserRequest,
    parse_control_request,
)
from gateway.main import app, create_app
from common.storage.users import initialize_registry
from gateway.supervisor_client import SupervisorError, _request
from supervisor.ipc_server import ControlServer


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
    def test_application_factory_preserves_the_public_route_contract(self) -> None:
        self.assertIs(create_app(), app)
        self.assertEqual(
            sorted(create_app().openapi()["paths"]),
            sorted({path for _, path in PUBLIC_RESPONSES}),
        )

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

    def get_extra_info(self, name: str) -> None:
        return None


class SupervisorWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_server_rejects_a_peer_without_credentials(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"action":"create_user"}\n')
        reader.feed_eof()
        writer = _MemoryWriter()
        server = ControlServer(
            owner=object(),
            gateway_user="dsh-gateway",
            fatal=asyncio.Event(),
        )

        await server.handle(reader, writer)

        self.assertEqual(
            json.loads(bytes(writer.body)),
            {"ok": False, "code": "forbidden", "message": "forbidden"},
        )
        self.assertTrue(writer.closed)

    async def test_request_uses_compact_newline_delimited_json(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"ok":true,"username":"alice"}\n')
        reader.feed_eof()
        writer = _MemoryWriter()

        with patch(
            "gateway.services.supervisor.asyncio.open_unix_connection",
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


class TypedControlContractTests(unittest.IsolatedAsyncioTestCase):
    def test_each_wire_action_parses_to_a_typed_request(self) -> None:
        fixtures = [
            ({"action": "create_user", "username": "alice", "password": {}}, CreateUserRequest),
            ({"action": "install_upload", "username": "alice", "spool_name": "a", "directory": ".", "filename": "a.txt", "size": 1}, "InstallUploadRequest"),
            ({"action": "list_upload_directories", "username": "alice", "directory": "."}, "ListUploadDirectoriesRequest"),
            ({"action": "plugin_online_import", "package_name": "pkg", "requested_version": "latest", "created_by": "admin"}, "ImportOnlinePluginRequest"),
            ({"action": "plugin_discover_versions", "package_name": "pkg"}, "DiscoverPluginVersionsRequest"),
            ({"action": "plugin_offline_import", "spool_name": "a.plugin.tgz", "created_by": "admin"}, "ImportOfflinePluginRequest"),
            ({"action": "plugin_assignment", "plugin_action": "install", "plugin_id": 1, "plugin_version_id": 2, "scope": {"type": "all"}, "enabled_after_install": True, "apply_to_future_users": False, "created_by": "admin"}, AssignPluginRequest),
        ]
        for payload, expected in fixtures:
            with self.subTest(action=payload["action"]):
                request = parse_control_request(payload)
                expected_name = expected if isinstance(expected, str) else expected.__name__
                self.assertEqual(type(request).__name__, expected_name)

    def test_assignment_round_trips_without_wire_field_changes(self) -> None:
        payload = {
            "action": "plugin_assignment",
            "plugin_action": "enable",
            "plugin_id": 7,
            "plugin_version_id": 11,
            "scope": {"type": "users", "usernames": ["alice"]},
            "enabled_after_install": True,
            "apply_to_future_users": False,
            "created_by": "admin",
        }
        request = parse_control_request(payload)
        self.assertEqual(request.model_dump(exclude_none=True), payload)

    def test_unknown_or_incomplete_actions_are_bad_requests(self) -> None:
        for payload in ({"action": "missing"}, {"action": "create_user"}):
            with self.subTest(payload=payload), self.assertRaises(ControlRequestError) as caught:
                parse_control_request(payload)
            self.assertEqual(caught.exception.code, "bad_request")

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
            "gateway.services.supervisor.asyncio.open_unix_connection",
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

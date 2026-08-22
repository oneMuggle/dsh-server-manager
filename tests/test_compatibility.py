from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from common.security import hash_password
from common.storage.users import add_user, initialize_registry
from gateway.services.compatibility import (
    LoginTicketStore,
    fetch_provider_models,
    safe_return_to,
)


compatibility = importlib.import_module("gateway.routers.compatibility.router")


def _user(username: str = "alice") -> dict:
    return {
        "username": username,
        "os_user": f"dsh-{username}",
        "uid": 20001,
        "port": 3102,
        "role": "user",
        "password": hash_password("existing-password"),
        "password_version": 1,
        "created_at": "2026-08-22T00:00:00+00:00",
        "disabled": False,
    }


class LoginTicketStoreTests(unittest.TestCase):
    def test_ticket_is_one_time_and_only_its_digest_is_stored(self) -> None:
        store = LoginTicketStore(60)
        ticket = store.issue("alice", "/admin/plugins")

        self.assertIsNotNone(ticket)
        self.assertNotIn(ticket, store._tickets)
        self.assertEqual(store.consume(ticket).username, "alice")
        self.assertIsNone(store.consume(ticket))

    def test_expired_ticket_is_rejected(self) -> None:
        store = LoginTicketStore(10)
        with patch("gateway.services.compatibility.time.monotonic", return_value=100):
            ticket = store.issue("alice", "/")
        with patch("gateway.services.compatibility.time.monotonic", return_value=110):
            self.assertIsNone(store.consume(ticket))

    def test_return_path_must_stay_on_the_same_origin(self) -> None:
        self.assertEqual(safe_return_to(None), "/")
        self.assertEqual(
            safe_return_to("/admin/../settings?tab=models#custom"),
            "/settings?tab=models#custom",
        )
        for value in ("https://example.com", "//example.com", "/\\example.com"):
            with self.subTest(value=value):
                self.assertIsNone(safe_return_to(value))


class _ProviderResponse:
    status = 200
    reason = "OK"

    async def __aenter__(self) -> "_ProviderResponse":
        return self

    async def __aexit__(self, *arguments: object) -> None:
        return None

    async def json(self) -> dict:
        return {"data": [{"id": "model-b"}, {"id": " model-a "}, {}]}


class _ProviderSession:
    def __init__(self) -> None:
        self.call: tuple | None = None

    def get(self, *arguments: object, **options: object) -> _ProviderResponse:
        self.call = (arguments, options)
        return _ProviderResponse()


class ProviderDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_models_using_the_old_openai_compatible_request(self) -> None:
        session = _ProviderSession()

        models = await fetch_provider_models(
            session,  # type: ignore[arg-type]
            "https://gateway.example/v1/",
            "sk-test",
        )

        self.assertEqual(models, ["model-b", "model-a"])
        arguments, options = session.call
        self.assertEqual(arguments[0], "https://gateway.example/v1/models")
        self.assertEqual(options["headers"]["Authorization"], "Bearer sk-test")


class CompatibilityApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "users.db"
        initialize_registry(self.database)
        add_user(self.database, _user())
        self.config = SimpleNamespace(
            user_database_path=self.database,
            control_socket_path=Path("/run/dsh/control.sock"),
            register_api_key="register-secret",
            login_api_key="login-secret",
            login_ticket_ttl_seconds=60,
        )
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.app.state.session_secret = b"compatibility-test-session-secret"
        self.app.state.http = object()
        self.app.state.login_tickets = LoginTicketStore(60)
        self.config_patch = patch.object(compatibility, "config", self.config)
        self.config_patch.start()

    async def asyncTearDown(self) -> None:
        self.config_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def credentials(token: str) -> HTTPAuthorizationCredentials:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def request(
        self,
        path: str,
        body: dict | None = None,
        method: str = "POST",
    ) -> Request:
        payload = json.dumps(body).encode() if body is not None else b""
        sent = False

        async def receive() -> dict:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}

        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "app": self.app,
            },
            receive,
        )

    @staticmethod
    def payload(value: object) -> dict:
        if isinstance(value, Response):
            return json.loads(value.body)
        return value.model_dump(by_alias=True)

    async def test_all_machine_apis_reject_a_missing_or_wrong_key(self) -> None:
        responses = (
            await compatibility.compatible_register(
                self.request("/api/register", {"username": "bob"}), None
            ),
            await compatibility.compatible_provider(
                "alice",
                self.request("/api/users/alice/provider", {"provider": {}}),
                None,
            ),
            await compatibility.compatible_login_ticket(
                self.request("/api/login-ticket", {"username": "alice"}),
                Response(),
                None,
            ),
        )
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(
                self.payload(response),
                {"ok": False, "error": "unauthorized"},
            )

    async def test_register_matches_the_old_response_contract(self) -> None:
        created = {
            "username": "bob",
            "port": 3103,
            "role": "user",
            "password_version": 1,
            "created_at": "2026-08-22T00:00:00+00:00",
            "disabled": False,
            "ready": True,
        }
        with patch.object(
            compatibility,
            "create_user",
            new=AsyncMock(return_value=created),
        ) as create:
            response = await compatibility.compatible_register(
                self.request(
                    "/api/register",
                    {"username": "bob", "password": "password-123"},
                ),
                self.credentials("register-secret"),
            )

        self.assertEqual(
            self.payload(response),
            {"ok": True, "user": "bob", "port": 3103},
        )
        self.assertEqual(create.await_args.args[1], "bob")
        self.assertNotIn("password-123", json.dumps(create.await_args.args[2]))

    async def test_provider_matches_payload_and_replace_semantics(self) -> None:
        with patch.object(
            compatibility,
            "create_provider",
            new=AsyncMock(return_value=object()),
        ) as create:
            response = await compatibility.compatible_provider(
                "alice",
                self.request(
                    "/api/users/alice/provider",
                    {
                        "provider": {
                            "name": "my_gateway",
                            "baseURL": "https://gateway.example/v1",
                            "model": "chat-model",
                        },
                        "apiKey": "sk-test",
                    },
                ),
                self.credentials("register-secret"),
            )

        self.assertEqual(
            self.payload(response),
            {
                "ok": True,
                "user": "alice",
                "provider": {
                    "name": "my_gateway",
                    "baseURL": "https://gateway.example/v1",
                    "model": "chat-model",
                    "models": ["chat-model"],
                    "api": "openai-completions",
                    "apiKeyEnv": "MY_GATEWAY_API_KEY",
                },
                "ref": "MY_GATEWAY_API_KEY",
                "models": ["chat-model"],
            },
        )
        self.assertTrue(create.await_args.kwargs["replace"])

    async def test_login_link_sets_a_session_and_cannot_be_replayed(self) -> None:
        raw_response = Response()
        response = await compatibility.compatible_login_ticket(
            self.request(
                "/api/login-ticket",
                {"username": "alice", "returnTo": "/admin/plugins"},
            ),
            raw_response,
            self.credentials("login-secret"),
        )
        payload = self.payload(response)
        self.assertTrue(payload["loginUrl"].startswith("/auth/external?ticket="))
        self.assertEqual(payload["expiresIn"], 60)
        self.assertEqual(raw_response.headers["cache-control"], "no-store")

        ticket = payload["loginUrl"].split("ticket=", 1)[1]
        login = await compatibility.consume_external_login(
            self.request("/auth/external", method="GET"), ticket
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.headers["location"], "/admin/plugins")
        self.assertIn("dsh_session=", login.headers["set-cookie"])
        self.assertEqual(login.headers["referrer-policy"], "no-referrer")

        replay = await compatibility.consume_external_login(
            self.request("/auth/external", method="GET"), ticket
        )
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(replay.headers["location"], "/login")


class CompatibilityAuthenticationTests(unittest.TestCase):
    def test_bearer_comparison_requires_the_configured_secret(self) -> None:
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials="secret",
        )
        self.assertTrue(compatibility._authorized(credentials, "secret"))
        self.assertFalse(compatibility._authorized(credentials, ""))
        self.assertFalse(compatibility._authorized(credentials, "wrong"))


if __name__ == "__main__":
    unittest.main()

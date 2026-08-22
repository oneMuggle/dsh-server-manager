from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from common.storage.users import add_user, initialize_registry
from gateway.schemas.providers import CreateCustomProvider
from gateway.services.harness import (
    HarnessRpcBusinessError,
    HarnessRpcTransportError,
    decode_rpc_response,
)
from gateway.services.providers import ProviderOperationError, create_provider


class ProviderRequestTests(unittest.TestCase):
    def request(self, **overrides):
        payload = {
            "providerId": "my-gateway",
            "displayName": "My Gateway",
            "baseURL": "https://gateway.example/v1",
            "api": "openai-completions",
            "apiKey": "sk-example",
            "models": [
                {
                    "id": "chat-model",
                    "name": "Chat Model",
                    "contextWindow": 128000,
                    "maxTokens": 8192,
                }
            ],
        }
        payload.update(overrides)
        return CreateCustomProvider.model_validate(payload)

    def test_builds_same_profile_as_custom_provider_ui(self) -> None:
        request = self.request()
        self.assertEqual(request.credential_ref(), "MY_GATEWAY_API_KEY")
        self.assertEqual(
            request.provider_profile(),
            {
                "displayName": "My Gateway",
                "apiKeyEnv": "MY_GATEWAY_API_KEY",
                "api": "openai-completions",
                "baseURL": "https://gateway.example/v1",
                "models": [
                    {
                        "id": "chat-model",
                        "name": "Chat Model",
                        "contextWindow": 128000,
                        "maxTokens": 8192,
                    }
                ],
            },
        )

    def test_omits_optional_display_name_and_credential(self) -> None:
        request = self.request(displayName="", apiKey="")
        self.assertNotIn("displayName", request.provider_profile())
        self.assertNotIn("apiKeyEnv", request.provider_profile())

    def test_rejects_invalid_provider_protocol_and_duplicate_models(self) -> None:
        for overrides in (
            {"providerId": "Bad_ID"},
            {"api": "azure-openai"},
            {"models": [{"id": "same"}, {"id": " same "}]},
            {"models": [{"id": "model", "contextWindow": 0}]},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.request(**overrides)

    def test_rejects_api_key_paste_mistakes(self) -> None:
        for api_key in ("   ", "OPENAI_API_KEY=sk-test", "'sk-test'", "sk test"):
            with self.subTest(api_key=api_key), self.assertRaises(ValidationError):
                self.request(apiKey=api_key)


class RpcResponseTests(unittest.TestCase):
    def test_returns_success_value(self) -> None:
        self.assertEqual(
            decode_rpc_response(
                {
                    "type": "server-response",
                    "rpcId": "rpc-1",
                    "result": {"ok": True, "value": {"saved": True}},
                },
                "rpc-1",
            ),
            {"saved": True},
        )

    def test_preserves_business_error_without_secret_payload(self) -> None:
        with self.assertRaises(HarnessRpcBusinessError) as caught:
            decode_rpc_response(
                {
                    "type": "server-response",
                    "rpcId": "rpc-1",
                    "result": {
                        "ok": False,
                        "error": {
                            "code": "settings-conflict",
                            "message": "stale revision",
                            "details": {"actual": 2},
                        },
                    },
                },
                "rpc-1",
            )
        self.assertEqual(caught.exception.code, "settings-conflict")
        self.assertEqual(caught.exception.details, {"actual": 2})

    def test_rejects_mismatched_rpc_id(self) -> None:
        with self.assertRaises(HarnessRpcTransportError):
            decode_rpc_response(
                {
                    "type": "server-response",
                    "rpcId": "other",
                    "result": {"ok": True, "value": {}},
                },
                "rpc-1",
            )


class ProviderServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "users.db"
        initialize_registry(self.database)
        add_user(
            self.database,
            {
                "username": "alice",
                "os_user": "dsh-alice",
                "uid": 20001,
                "port": 3102,
                "role": "user",
                "password": {
                    "algorithm": "scrypt",
                    "salt": "test-salt",
                    "hash": "test-hash",
                    "n": 1 << 14,
                    "r": 8,
                    "p": 1,
                    "dklen": 64,
                },
                "password_version": 1,
                "created_at": "2026-08-22T00:00:00+00:00",
                "disabled": False,
            },
        )
        self.session = object()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def provider() -> CreateCustomProvider:
        return CreateCustomProvider.model_validate(
            {
                "providerId": "my-gateway",
                "displayName": "My Gateway",
                "baseURL": "https://gateway.example/v1",
                "api": "openai-completions",
                "apiKey": "sk-example",
                "models": [{"id": "chat-model"}],
            }
        )

    @staticmethod
    def catalog() -> dict:
        return {"providers": []}

    @staticmethod
    def settings(*, writable: bool = True) -> dict:
        return {
            "writable": writable,
            "namespaces": [{"ns": "llm-pi-ai", "revision": 4}],
        }

    async def test_writes_settings_then_credential(self) -> None:
        responses = [
            self.catalog(),
            self.settings(),
            {"credentials": {"MY_GATEWAY_API_KEY": {"writable": True}}},
            {"saved": True},
            {"saved": True},
        ]
        with patch(
            "gateway.services.providers.call_harness_rpc",
            new=AsyncMock(side_effect=responses),
        ) as rpc:
            result = await create_provider(
                self.database,
                self.session,
                "alice",
                self.provider(),
            )

        self.assertEqual(result.username, "alice")
        self.assertEqual(result.provider_id, "my-gateway")
        self.assertTrue(result.credential_stored)
        self.assertEqual(rpc.await_args_list[3].args[2], "settings.mutate")
        self.assertEqual(rpc.await_args_list[4].args[2], "credentials.set")

    async def test_rejects_duplicate_provider(self) -> None:
        with patch(
            "gateway.services.providers.call_harness_rpc",
            new=AsyncMock(
                side_effect=[
                    {"providers": [{"provider": "my-gateway"}]},
                    self.settings(),
                ]
            ),
        ):
            with self.assertRaises(ProviderOperationError) as caught:
                await create_provider(
                    self.database, self.session, "alice", self.provider()
                )
        self.assertEqual(caught.exception.code, "conflict")

    async def test_rejects_unwritable_settings(self) -> None:
        with patch(
            "gateway.services.providers.call_harness_rpc",
            new=AsyncMock(side_effect=[self.catalog(), self.settings(writable=False)]),
        ):
            with self.assertRaises(ProviderOperationError) as caught:
                await create_provider(
                    self.database, self.session, "alice", self.provider()
                )
        self.assertEqual(caught.exception.code, "conflict")

    async def test_rejects_malformed_provider_catalog(self) -> None:
        with patch(
            "gateway.services.providers.call_harness_rpc",
            new=AsyncMock(side_effect=[{"providers": None}, self.settings()]),
        ):
            with self.assertRaises(ProviderOperationError) as caught:
                await create_provider(
                    self.database, self.session, "alice", self.provider()
                )
        self.assertEqual(caught.exception.code, "invalid_upstream")

    async def test_preserves_harness_business_error_code(self) -> None:
        error = HarnessRpcBusinessError(
            message="stale revision",
            code="settings-conflict",
            details={"actual": 5},
        )
        with patch(
            "gateway.services.providers.call_harness_rpc",
            new=AsyncMock(side_effect=error),
        ):
            with self.assertRaises(ProviderOperationError) as caught:
                await create_provider(
                    self.database, self.session, "alice", self.provider()
                )
        self.assertEqual(caught.exception.code, "business")
        self.assertEqual(caught.exception.upstream_code, "settings-conflict")

    async def test_reports_partial_credential_failure_without_secret(self) -> None:
        error = HarnessRpcBusinessError(
            message="credential rejected",
            code="credential-invalid",
            details={},
        )
        with patch(
            "gateway.services.providers.call_harness_rpc",
            new=AsyncMock(
                side_effect=[
                    self.catalog(),
                    self.settings(),
                    {"credentials": {"MY_GATEWAY_API_KEY": {"writable": True}}},
                    {"saved": True},
                    error,
                ]
            ),
        ):
            with self.assertRaises(ProviderOperationError) as caught:
                await create_provider(
                    self.database, self.session, "alice", self.provider()
                )
        self.assertEqual(caught.exception.code, "credential_partial")
        self.assertTrue(caught.exception.provider_created)
        self.assertFalse(caught.exception.credential_stored)
        self.assertNotIn("sk-example", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

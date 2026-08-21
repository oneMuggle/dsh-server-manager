from __future__ import annotations

import unittest

from pydantic import ValidationError

from gateway.harness_rpc import (
    HarnessRpcBusinessError,
    HarnessRpcTransportError,
    decode_rpc_response,
)
from gateway.providers import CreateCustomProvider


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


if __name__ == "__main__":
    unittest.main()

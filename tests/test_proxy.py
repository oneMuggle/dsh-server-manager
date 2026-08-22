from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from gateway.auth import hash_password, parse_session
from gateway.dependencies import api_token as _api_token
from gateway.proxy import LOOPBACK_CHECK, UUID_SHIM, _patch_connection_module, _patch_html
from gateway.main import (
    SWAGGER_UI_STATIC_DIR,
    app,
    config,
    login,
    swagger_ui,
)
from gateway.schemas.auth import Credentials
from gateway.pages import admin_page, auth_page
from gateway.web import STATIC_ROOT


class ProxyTransformTests(unittest.TestCase):
    def test_html_injects_compatibility_script_and_cache_buster(self) -> None:
        original = (
            b'<html><head></head><body><script src="/plugins/@deepseek-ai/'
            b'dsh-client-connection/client.js?rev=abc"></script></body></html>'
        )
        transformed = _patch_html(original)
        self.assertIn(UUID_SHIM, transformed)
        self.assertIn(b"client.js?lan=2&rev=abc", transformed)
        self.assertIn("上传文件".encode(), transformed)
        self.assertIn(b'/assets/upload-widget.css', transformed)
        self.assertIn(b'/assets/upload-widget.js', transformed)
        self.assertIn("上传到当前目录".encode(), transformed)
        self.assertIn("退出登录".encode(), transformed)
        self.assertIn(b"/api/gateway/v1/auth/logout", transformed)
        self.assertNotIn("管理后台".encode(), transformed)

    def test_admin_html_includes_management_entry(self) -> None:
        transformed = _patch_html(b"<html><head></head><body></body></html>", is_admin=True)
        self.assertIn(b'href="/admin"', transformed)
        self.assertIn("管理后台".encode(), transformed)

    def test_connection_module_forces_loopback_capabilities(self) -> None:
        original = b"const connection = {" + LOOPBACK_CHECK + b"};"
        transformed = _patch_connection_module(original)
        self.assertIn(b"isLoopback: true", transformed)
        self.assertNotIn(LOOPBACK_CHECK, transformed)


class AuthenticationPageTests(unittest.TestCase):
    def test_login_page_has_registration_button(self) -> None:
        page = auth_page("login")
        self.assertIn('class="secondary-button"', page)
        self.assertIn('href="/register"', page)
        self.assertIn("注册新账号", page)

    def test_registration_page_links_back_to_login(self) -> None:
        page = auth_page("register")
        self.assertIn('href="/login"', page)
        self.assertIn("返回登录", page)


class ManagementPageTests(unittest.TestCase):
    def test_management_page_loads_users_and_links_swagger(self) -> None:
        page = admin_page()
        script = (STATIC_ROOT / "admin.js").read_text()
        self.assertIn("/api/gateway/v1/admin/users", script)
        self.assertIn('href="/docs"', page)
        self.assertIn("用户管理", page)
        self.assertIn("退出登录", page)
        self.assertIn("/api/gateway/v1/auth/logout", script)

    def test_swagger_and_openapi_are_enabled(self) -> None:
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/docs", paths)
        self.assertIn("/openapi.json", paths)

    def test_swagger_uses_local_static_assets(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/docs",
                "headers": [],
                "app": app,
            }
        )
        response = asyncio.run(swagger_ui(request))
        html = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'src="/static/swagger-ui/swagger-ui-bundle.js"', html
        )
        self.assertIn('href="/static/swagger-ui/swagger-ui.css"', html)
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertNotIn("fastapi.tiangolo.com/img/favicon.png", html)

        for asset in ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png"):
            asset_path = SWAGGER_UI_STATIC_DIR / asset
            self.assertTrue(asset_path.is_file(), asset)
            self.assertGreater(asset_path.stat().st_size, 0, asset)

    def test_openapi_describes_cookie_and_bearer_authentication(self) -> None:
        schema = app.openapi()
        security_schemes = schema["components"]["securitySchemes"]
        self.assertEqual(security_schemes["HTTPBearer"]["scheme"], "bearer")
        self.assertEqual(security_schemes["APIKeyCookie"]["in"], "cookie")

    def test_custom_provider_api_is_described_with_ui_field_names(self) -> None:
        schema = app.openapi()
        path = "/api/gateway/v1/admin/users/{username}/providers"
        operation = schema["paths"][path]["post"]
        request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        component_name = request_schema["$ref"].rsplit("/", 1)[-1]
        properties = schema["components"]["schemas"][component_name]["properties"]
        self.assertEqual(
            set(properties),
            {"providerId", "displayName", "baseURL", "api", "apiKey", "models"},
        )
        self.assertEqual(
            properties["api"]["enum"],
            ["openai-completions", "openai-responses", "anthropic-messages"],
        )

    def test_plugin_management_api_uses_public_field_names(self) -> None:
        schema = app.openapi()
        operation = schema["paths"]["/api/gateway/v1/admin/plugin-jobs"]["post"]
        request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        component_name = request_schema["$ref"].rsplit("/", 1)[-1]
        properties = schema["components"]["schemas"][component_name]["properties"]
        self.assertEqual(
            set(properties),
            {
                "action",
                "pluginId",
                "versionId",
                "scope",
                "enabledAfterInstall",
                "applyToFutureUsers",
            },
        )
        self.assertIn(
            "/api/gateway/v1/admin/plugins/registry-versions", schema["paths"]
        )
        self.assertIn("/api/gateway/v1/admin/plugins/online", schema["paths"])
        self.assertIn("/api/gateway/v1/admin/plugins/offline", schema["paths"])


class TokenAuthenticationTests(unittest.TestCase):
    def test_login_returns_same_token_in_body_and_http_only_cookie(self) -> None:
        user = {
            "username": "alice",
            "role": "user",
            "disabled": False,
            "password": hash_password("change-me-123"),
            "password_version": 1,
        }
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/gateway/v1/auth/login",
                "headers": [],
                "client": ("192.0.2.10", 12345),
                "app": app,
            }
        )
        app.state.session_secret = b"test-session-secret"

        with patch("gateway.routers.auth.get_user", return_value=user):
            response = asyncio.run(
                login(request, Credentials(username="alice", password="change-me-123"))
            )

        body = json.loads(response.body)
        self.assertEqual(body["tokenType"], "Bearer")
        self.assertEqual(body["expiresIn"], config.session_ttl_seconds)
        self.assertIn(
            f"{config.cookie_name}={body['accessToken']}",
            response.headers["set-cookie"],
        )
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        payload = parse_session(body["accessToken"], app.state.session_secret)
        self.assertEqual(payload["sub"], "alice")
        self.assertEqual(payload["pv"], 1)

    def test_bearer_token_takes_precedence_over_cookie(self) -> None:
        bearer = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials="bearer-token",
        )
        bearer_request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", b"Bearer bearer-token")],
            }
        )
        self.assertEqual(
            _api_token(bearer_request, "cookie-token", bearer),
            "bearer-token",
        )

        cookie_request = Request({"type": "http", "headers": []})
        self.assertEqual(_api_token(cookie_request, "cookie-token", None), "cookie-token")

    def test_malformed_authorization_header_is_rejected(self) -> None:
        request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", b"Basic credentials")],
            }
        )
        with self.assertRaises(HTTPException) as caught:
            _api_token(request, "valid-cookie", None)
        self.assertEqual(caught.exception.status_code, 401)

if __name__ == "__main__":
    unittest.main()

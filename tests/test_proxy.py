from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm

from common.security import hash_password

from gateway.application import app, swagger_ui
from gateway.dependencies import api_token as _api_token
from gateway.proxy.headers import request_headers, response_headers, upstream_url
from gateway.proxy.transforms import (
    LOOPBACK_CHECK,
    UUID_SHIM,
    patch_connection_module,
    patch_html,
)
from gateway.routers.auth.registrations import register
from gateway.routers.auth.sessions import login, oauth_token
from gateway.routers.management import register_page
from gateway.runtime import SWAGGER_UI_STATIC_DIR, config
from gateway.schemas.auth import Credentials
from gateway.services.auth import parse_session, verify_credentials
from gateway.web import MANAGEMENT_STATIC_ROOT
from gateway.web.fragments import ADMIN_WIDGET, LOGOUT_WIDGET
from gateway.web.pages import admin_page, auth_page


class ProxyTransformTests(unittest.TestCase):
    def test_injected_controls_use_local_assets_without_inline_code(self) -> None:
        controls = ADMIN_WIDGET + LOGOUT_WIDGET
        self.assertIn(b"/gateway-assets/proxy-widgets.css?v=1", controls)
        self.assertIn(b"/gateway-assets/logout-widget.js?v=1", controls)
        self.assertNotIn(b"<style>", controls)
        self.assertNotIn(b"fetch(", controls)

    def test_html_injects_compatibility_script_and_cache_buster(self) -> None:
        original = (
            b'<html><head></head><body><script src="/plugins/@deepseek-ai/'
            b'dsh-client-connection/client.js?rev=abc"></script></body></html>'
        )
        transformed = patch_html(original)
        self.assertIn(UUID_SHIM, transformed)
        self.assertIn(b"client.js?lan=2&rev=abc", transformed)
        self.assertIn("上传文件".encode(), transformed)
        self.assertIn(b'/gateway-assets/upload-widget.css', transformed)
        self.assertIn(b'/gateway-assets/upload-widget.js', transformed)
        self.assertIn("上传到当前目录".encode(), transformed)
        self.assertIn("退出登录".encode(), transformed)
        self.assertIn(b"/gateway-assets/logout-widget.js", transformed)
        self.assertNotIn("管理后台".encode(), transformed)

    def test_admin_html_includes_management_entry(self) -> None:
        transformed = patch_html(b"<html><head></head><body></body></html>", is_admin=True)
        self.assertIn(b'href="/admin"', transformed)
        self.assertIn("管理后台".encode(), transformed)

    def test_connection_module_forces_loopback_capabilities(self) -> None:
        original = b"const connection = {" + LOOPBACK_CHECK + b"};"
        transformed = patch_connection_module(original)
        self.assertIn(b"isLoopback: true", transformed)
        self.assertNotIn(LOOPBACK_CHECK, transformed)


class ProxyHeaderPolicyTests(unittest.TestCase):
    def test_upstream_url_preserves_path_and_query(self) -> None:
        self.assertEqual(
            upstream_url(3101, "/api/items", "limit=2"),
            "http://127.0.0.1:3101/api/items?limit=2",
        )

    def test_http_request_headers_hide_gateway_identity(self) -> None:
        headers = request_headers(
            {
                "Host": "gateway.example",
                "Cookie": "session=secret",
                "X-Forwarded-For": "192.0.2.10",
                "Connection": "keep-alive",
                "X-Request-ID": "request-1",
            },
            3101,
        )
        self.assertEqual(headers["Host"], "127.0.0.1:3101")
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertEqual(headers["X-Request-ID"], "request-1")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("X-Forwarded-For", headers)
        self.assertNotIn("Connection", headers)

    def test_transformed_response_drops_stale_entity_headers(self) -> None:
        upstream = type(
            "Upstream",
            (),
            {
                "headers": {
                    "Content-Length": "100",
                    "Content-Encoding": "gzip",
                    "ETag": "old",
                    "Set-Cookie": "upstream=secret",
                    "Content-Type": "text/html",
                }
            },
        )()
        headers = response_headers(upstream, transformed=True)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Content-Type"], "text/html")
        self.assertNotIn("Content-Length", headers)
        self.assertNotIn("Content-Encoding", headers)
        self.assertNotIn("ETag", headers)
        self.assertNotIn("Set-Cookie", headers)


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

    def test_login_page_hides_registration_when_disabled(self) -> None:
        page = auth_page("login", registration_enabled=False)
        self.assertNotIn('href="/register"', page)
        self.assertNotIn("注册新账号", page)

    def test_disabled_registration_page_is_not_found(self) -> None:
        request = Request({"type": "http", "headers": [], "app": app})
        with (
            patch(
                "gateway.routers.management.config",
                replace(config, registration_enabled=False),
            ),
            self.assertRaises(HTTPException) as caught,
        ):
            asyncio.run(register_page(request))
        self.assertEqual(caught.exception.status_code, 404)

    def test_disabled_registration_api_rejects_before_creating_user(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/registrations",
                "headers": [],
                "client": ("192.0.2.10", 12345),
                "app": app,
            }
        )
        credentials = Credentials(username="alice", password="change-me-123")
        with (
            patch(
                "gateway.routers.auth.registrations.config",
                replace(config, registration_enabled=False),
            ),
            patch("gateway.routers.auth.registrations.create_user") as create,
            self.assertRaises(HTTPException) as caught,
        ):
            asyncio.run(register(request, credentials))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "注册功能已关闭")
        create.assert_not_called()


class ManagementPageTests(unittest.TestCase):
    def test_management_page_loads_users_and_links_swagger(self) -> None:
        page = admin_page()
        script = (MANAGEMENT_STATIC_ROOT / "admin.js").read_text()
        self.assertIn("/api/v1/users", script)
        self.assertIn('href="/docs"', page)
        self.assertIn("用户管理", page)
        self.assertIn("退出登录", page)
        self.assertIn("/api/v1/auth/sessions/current", script)

    def test_management_page_exposes_create_reset_and_status_controls(self) -> None:
        page = admin_page()
        script = (MANAGEMENT_STATIC_ROOT / "admin.js").read_text()
        self.assertIn('id="create-user-form"', page)
        self.assertIn('id="reset-dialog"', page)
        self.assertIn("创建用户", page)
        self.assertIn("重置密码", page)
        self.assertIn("method: 'PATCH'", script)
        self.assertIn("disabled: nextDisabled", script)
        self.assertNotIn("删除用户", page)
        self.assertNotIn("innerHTML", script)

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
        password_flow = security_schemes["OAuth2PasswordBearer"]["flows"]["password"]
        self.assertEqual(
            password_flow["tokenUrl"],
            "/api/v1/auth/token",
        )
        self.assertEqual(security_schemes["APIKeyCookie"]["in"], "cookie")

    def test_custom_provider_api_is_described_with_ui_field_names(self) -> None:
        schema = app.openapi()
        path = "/api/v1/users/{username}/providers"
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
        operation = schema["paths"]["/api/v1/plugins/jobs"]["post"]
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
            "/api/v1/plugins/registry/versions", schema["paths"]
        )
        self.assertIn("/api/v1/plugins/imports/registry", schema["paths"])
        self.assertIn("/api/v1/plugins/imports/archive", schema["paths"])


class TokenAuthenticationTests(unittest.TestCase):
    def test_oauth_password_exchange_returns_bearer_without_cookie(self) -> None:
        user = {
            "username": "alice",
            "role": "user",
            "disabled": False,
            "password_version": 3,
        }
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/token",
                "headers": [],
                "client": ("192.0.2.11", 12345),
                "app": app,
            }
        )
        app.state.session_secret = b"test-session-secret"
        form = OAuth2PasswordRequestForm(
            username="alice",
            password="change-me-123",
            scope="",
            grant_type="password",
            client_id=None,
            client_secret=None,
        )

        with patch(
            "gateway.routers.auth.sessions.verify_credentials",
            return_value=user,
        ):
            response = asyncio.run(oauth_token(request, form))

        self.assertEqual(response.token_type, "bearer")
        self.assertEqual(response.expires_in, config.session_ttl_seconds)
        self.assertEqual(
            parse_session(response.access_token, app.state.session_secret)["pv"],
            3,
        )
    def test_shared_credential_verification_accepts_an_enabled_user(self) -> None:
        user = {
            "username": "alice",
            "role": "user",
            "disabled": False,
            "password": hash_password("change-me-123"),
            "password_version": 1,
        }
        with patch("gateway.services.auth.get_user", return_value=user):
            authenticated = asyncio.run(
                verify_credentials("alice", "change-me-123")
            )
        self.assertIs(authenticated, user)

    def test_shared_credential_verification_rejects_disabled_user(self) -> None:
        user = {
            "username": "alice",
            "role": "user",
            "disabled": True,
            "password": hash_password("change-me-123"),
            "password_version": 1,
        }
        with patch("gateway.services.auth.get_user", return_value=user):
            authenticated = asyncio.run(
                verify_credentials("alice", "change-me-123")
            )
        self.assertIsNone(authenticated)

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
                "path": "/api/v1/auth/sessions",
                "headers": [],
                "client": ("192.0.2.10", 12345),
                "app": app,
            }
        )
        app.state.session_secret = b"test-session-secret"

        with patch("gateway.services.auth.get_user", return_value=user):
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
        bearer_request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", b"Bearer bearer-token")],
            }
        )
        self.assertEqual(
            _api_token(bearer_request, "cookie-token", "bearer-token"),
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

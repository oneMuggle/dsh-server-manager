from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from gateway.application import create_app
from gateway.runtime import SWAGGER_UI_STATIC_DIR
from gateway.web import MANAGEMENT_STATIC_ROOT, STATIC_ROOT, TEMPLATE_ROOT
from gateway.web.fragments import ADMIN_WIDGET, LOGOUT_WIDGET, UPLOAD_WIDGET
from gateway.web.pages import admin_page, auth_page, plugin_admin_page


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def asgi_get(path: str) -> tuple[int, dict[str, str], bytes]:
    application = create_app()
    request_sent = False
    messages: list[dict] = []

    async def receive() -> dict:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("192.0.2.10", 12345),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    headers = {
        name.decode().lower(): value.decode()
        for name, value in start.get("headers", [])
    }
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], headers, body


class ManagementWebAssetTests(unittest.TestCase):
    def test_gateway_assets_do_not_intercept_tenant_asset_paths(self) -> None:
        tenant_status, tenant_headers, _ = asyncio.run(
            asgi_get("/assets/dsh-main-bundle.js")
        )
        self.assertEqual(tenant_status, 303)
        self.assertEqual(tenant_headers["location"], "/login")

        gateway_status, _, gateway_body = asyncio.run(
            asgi_get("/gateway-assets/admin.js")
        )
        self.assertEqual(gateway_status, 200)
        self.assertIn(
            b"/api/v1/users",
            gateway_body,
        )

    def test_all_browser_assets_live_under_the_web_package(self) -> None:
        self.assertEqual(MANAGEMENT_STATIC_ROOT, STATIC_ROOT / "management")
        self.assertEqual(SWAGGER_UI_STATIC_DIR, STATIC_ROOT / "swagger-ui")
        self.assertFalse((PROJECT_ROOT / "gateway" / "static").exists())

    def test_management_pages_are_loaded_from_templates(self) -> None:
        login_template = (TEMPLATE_ROOT / "login.html").read_text()
        pages = {
            "login.html": auth_page("login", registration_enabled=False),
            "register.html": auth_page("register"),
            "admin.html": admin_page(),
            "plugins.html": plugin_admin_page(),
        }
        for name, rendered in pages.items():
            with self.subTest(name=name):
                expected = (TEMPLATE_ROOT / name).read_text()
                if name == "login.html":
                    expected = login_template.replace("<!-- REGISTRATION_LINK -->", "")
                self.assertEqual(rendered, expected)

    def test_templates_reference_local_styles_and_scripts(self) -> None:
        expected = {
            "login.html": ("auth.css", "auth-login.js"),
            "register.html": ("auth.css", "auth-register.js"),
            "admin.html": ("admin.css", "admin.js"),
            "plugins.html": ("plugins.css", "plugins.js"),
        }
        for template, assets in expected.items():
            html = (TEMPLATE_ROOT / template).read_text()
            for asset in assets:
                with self.subTest(template=template, asset=asset):
                    self.assertIn(f'/gateway-assets/{asset}', html)
                    self.assertTrue((MANAGEMENT_STATIC_ROOT / asset).is_file())

    def test_official_plugins_are_collapsible_and_remember_their_state(self) -> None:
        html = (TEMPLATE_ROOT / "plugins.html").read_text()
        script = (MANAGEMENT_STATIC_ROOT / "plugins.js").read_text()
        stylesheet = (MANAGEMENT_STATIC_ROOT / "plugins.css").read_text()

        self.assertIn('<details id="builtin-details">', html)
        self.assertIn('id="builtin-count"', html)
        self.assertIn("dsm.plugins.builtins.expanded", script)
        self.assertIn("localStorage.setItem", script)
        self.assertIn("details[open] .collapse-label", stylesheet)

    def test_injected_upload_widget_is_also_packaged_as_web_assets(self) -> None:
        widget = UPLOAD_WIDGET.decode()
        self.assertEqual(widget, (TEMPLATE_ROOT / "upload-widget.html").read_text())
        for asset in ("upload-widget.css", "upload-widget.js"):
            self.assertIn(f"/gateway-assets/{asset}", widget)
            self.assertTrue((MANAGEMENT_STATIC_ROOT / asset).is_file())

    def test_injected_navigation_widgets_use_local_assets(self) -> None:
        widgets = {
            "admin-widget.html": ADMIN_WIDGET.decode(),
            "logout-widget.html": LOGOUT_WIDGET.decode(),
        }
        for template, rendered in widgets.items():
            with self.subTest(template=template):
                self.assertEqual(rendered, (TEMPLATE_ROOT / template).read_text())

        combined = (ADMIN_WIDGET + LOGOUT_WIDGET).decode()
        self.assertIn('href="/admin"', combined)
        for asset in ("proxy-widgets.css", "logout-widget.js"):
            self.assertIn(f"/gateway-assets/{asset}", combined)
            self.assertTrue((MANAGEMENT_STATIC_ROOT / asset).is_file())
        self.assertNotIn("<style>", combined)
        self.assertNotIn("fetch(", combined)


if __name__ == "__main__":
    unittest.main()

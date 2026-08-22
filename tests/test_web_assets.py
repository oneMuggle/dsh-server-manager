from __future__ import annotations

import unittest
from pathlib import Path

from gateway.pages import admin_page, auth_page
from gateway.plugin_ui import plugin_admin_page
from gateway.runtime import SWAGGER_UI_STATIC_DIR
from gateway.upload_ui import UPLOAD_WIDGET
from gateway.web import MANAGEMENT_STATIC_ROOT, STATIC_ROOT, TEMPLATE_ROOT


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ManagementWebAssetTests(unittest.TestCase):
    def test_all_browser_assets_live_under_the_web_package(self) -> None:
        self.assertEqual(MANAGEMENT_STATIC_ROOT, STATIC_ROOT / "management")
        self.assertEqual(SWAGGER_UI_STATIC_DIR, STATIC_ROOT / "swagger-ui")
        self.assertFalse((PROJECT_ROOT / "gateway" / "static").exists())

    def test_management_pages_are_loaded_from_templates(self) -> None:
        pages = {
            "login.html": auth_page("login"),
            "register.html": auth_page("register"),
            "admin.html": admin_page(),
            "plugins.html": plugin_admin_page(),
        }
        for name, rendered in pages.items():
            with self.subTest(name=name):
                self.assertEqual(rendered, (TEMPLATE_ROOT / name).read_text())

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
                    self.assertIn(f'/assets/{asset}', html)
                    self.assertTrue((MANAGEMENT_STATIC_ROOT / asset).is_file())

    def test_injected_upload_widget_is_also_packaged_as_web_assets(self) -> None:
        widget = UPLOAD_WIDGET.decode()
        self.assertEqual(widget, (TEMPLATE_ROOT / "upload-widget.html").read_text())
        for asset in ("upload-widget.css", "upload-widget.js"):
            self.assertIn(f"/assets/{asset}", widget)
            self.assertTrue((MANAGEMENT_STATIC_ROOT / asset).is_file())


if __name__ == "__main__":
    unittest.main()

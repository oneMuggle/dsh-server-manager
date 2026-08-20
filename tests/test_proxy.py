from __future__ import annotations

import unittest

from gateway.proxy import LOOPBACK_CHECK, UUID_SHIM, _patch_connection_module, _patch_html
from gateway.pages import auth_page


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


if __name__ == "__main__":
    unittest.main()

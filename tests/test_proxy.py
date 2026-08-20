from __future__ import annotations

import unittest

from gateway.proxy import LOOPBACK_CHECK, UUID_SHIM, _patch_connection_module, _patch_html


class ProxyTransformTests(unittest.TestCase):
    def test_html_injects_compatibility_script_and_cache_buster(self) -> None:
        original = (
            b'<html><head></head><script src="/plugins/@deepseek-ai/'
            b'dsh-client-connection/client.js?rev=abc"></script></html>'
        )
        transformed = _patch_html(original)
        self.assertIn(UUID_SHIM, transformed)
        self.assertIn(b"client.js?lan=2&rev=abc", transformed)

    def test_connection_module_forces_loopback_capabilities(self) -> None:
        original = b"const connection = {" + LOOPBACK_CHECK + b"};"
        transformed = _patch_connection_module(original)
        self.assertIn(b"isLoopback: true", transformed)
        self.assertNotIn(LOOPBACK_CHECK, transformed)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from gateway.web import STATIC_ROOT


class UploadWidgetThemeTests(unittest.TestCase):
    def test_uses_dsh_theme_tokens_for_dialog_controls_and_text(self) -> None:
        widget = (STATIC_ROOT / "upload-widget.css").read_text()
        for token in (
            "--dsw-alias-bg-mask-1",
            "--dsw-alias-bg-layer-2",
            "--dsw-alias-border-l2",
            "--dsw-alias-label-primary",
            "--dsw-alias-label-secondary",
            "--dsw-alias-button-primary-fill",
            "--dsw-alias-interactive-bg-hover",
        ):
            with self.subTest(token=token):
                self.assertIn(f"var({token}", widget)

    def test_old_fixed_dark_palette_is_removed(self) -> None:
        widget = (STATIC_ROOT / "upload-widget.css").read_text()
        for color in ("#181d27", "#10131a", "#315efb", "#aeb9d2"):
            with self.subTest(color=color):
                self.assertNotIn(color, widget)


if __name__ == "__main__":
    unittest.main()

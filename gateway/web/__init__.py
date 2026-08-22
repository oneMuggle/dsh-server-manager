from __future__ import annotations

from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parent
TEMPLATE_ROOT = WEB_ROOT / "templates"
STATIC_ROOT = WEB_ROOT / "static"
MANAGEMENT_STATIC_ROOT = STATIC_ROOT / "management"


def render_template(name: str) -> str:
    """Read a packaged management page without requiring a template engine."""

    return (TEMPLATE_ROOT / name).read_text(encoding="utf-8")

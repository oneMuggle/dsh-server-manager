from __future__ import annotations

from .web import render_template


def plugin_admin_page() -> str:
    return render_template("plugins.html")

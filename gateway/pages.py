from __future__ import annotations

from .web import render_template


def auth_page(mode: str) -> str:
    if mode not in {"login", "register"}:
        raise ValueError(f"unsupported authentication page: {mode}")
    return render_template(f"{mode}.html")


def admin_page() -> str:
    return render_template("admin.html")

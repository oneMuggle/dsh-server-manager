from __future__ import annotations

from . import render_template


def auth_page(mode: str, *, registration_enabled: bool = True) -> str:
    if mode not in {"login", "register"}:
        raise ValueError(f"unsupported authentication page: {mode}")
    page = render_template(f"{mode}.html")
    registration_link = (
        '<p><a class="secondary-button" href="/register">注册新账号</a></p>'
        if mode == "login" and registration_enabled
        else ""
    )
    return page.replace("<!-- REGISTRATION_LINK -->", registration_link)


def admin_page() -> str:
    return render_template("admin.html")


def plugin_admin_page() -> str:
    return render_template("plugins.html")

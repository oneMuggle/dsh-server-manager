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


def _management_page(name: str, title: str, message: str) -> str:
    header = (
        render_template("management-header.html")
        .replace("<!-- PAGE_TITLE -->", title)
        .replace("<!-- PAGE_MESSAGE -->", message)
    )
    return render_template(name).replace("<!-- MANAGEMENT_HEADER -->", header)


def admin_page() -> str:
    return _management_page("admin.html", "用户管理", "正在加载用户列表…")


def plugin_admin_page() -> str:
    return _management_page("plugins.html", "插件管理", "正在加载…")

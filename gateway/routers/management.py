from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..runtime import config
from ..services.auth import request_user, user_role
from ..web.pages import admin_page, auth_page, plugin_admin_page


router = APIRouter()


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> Response:
    if request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        auth_page("login", registration_enabled=config.registration_enabled),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request) -> Response:
    if not config.registration_enabled:
        raise HTTPException(status_code=404, detail="页面不存在")
    if request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("register"), headers={"Cache-Control": "no-store"})


def _require_management_page(request: Request) -> RedirectResponse | None:
    user = request_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return None


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def management_page(request: Request) -> Response:
    redirect = _require_management_page(request)
    if redirect:
        return redirect
    return HTMLResponse(admin_page(), headers={"Cache-Control": "no-store"})


@router.get("/admin/plugins", response_class=HTMLResponse, include_in_schema=False)
async def plugin_management_page(request: Request) -> Response:
    redirect = _require_management_page(request)
    if redirect:
        return redirect
    return HTMLResponse(plugin_admin_page(), headers={"Cache-Control": "no-store"})

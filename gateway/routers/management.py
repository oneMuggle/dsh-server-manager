from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..services.auth import request_user, user_role
from ..web.pages import admin_page, plugin_admin_page


router = APIRouter()


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

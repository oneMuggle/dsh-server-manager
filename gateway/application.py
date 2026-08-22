from __future__ import annotations

from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .routers import auth, compatibility, file, management, plugin, proxy, system, user
from .runtime import SWAGGER_UI_STATIC_DIR, config
from .services.compatibility import LoginTicketStore
from .web import MANAGEMENT_STATIC_ROOT as MANAGEMENT_STATIC_DIR


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.session_secret = config.session_secret_path.read_bytes()
    application.state.http = aiohttp.ClientSession(auto_decompress=False)
    application.state.login_tickets = LoginTicketStore(
        config.login_ticket_ttl_seconds
    )
    try:
        yield
    finally:
        await application.state.http.close()


async def swagger_ui(request: Request) -> HTMLResponse:
    application = request.app
    root_path = request.scope.get("root_path", "").rstrip("/")
    static_url = f"{root_path}/static/swagger-ui"
    return get_swagger_ui_html(
        openapi_url=f"{root_path}{application.openapi_url}",
        title=f"{application.title} - Swagger UI",
        swagger_js_url=f"{static_url}/swagger-ui-bundle.js",
        swagger_css_url=f"{static_url}/swagger-ui.css",
        swagger_favicon_url=f"{static_url}/favicon-32x32.png",
    )


def create_app() -> FastAPI:
    application = FastAPI(
        title="DSM · DSH Server Manager",
        description=(
            "DeepSeek Harness 多用户服务器管理网关。受保护接口支持登录后生成的 "
            "HttpOnly Cookie 或 Bearer Token。"
        ),
        version="0.4.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    application.mount(
        "/static/swagger-ui",
        StaticFiles(directory=SWAGGER_UI_STATIC_DIR),
        name="swagger-ui-static",
    )
    application.mount(
        "/gateway-assets",
        StaticFiles(directory=MANAGEMENT_STATIC_DIR),
        name="management-static",
    )
    application.add_api_route("/docs", swagger_ui, include_in_schema=False)
    application.include_router(auth.router)
    application.include_router(system.router)
    application.include_router(user.router)
    application.include_router(file.router)
    application.include_router(plugin.router)
    application.include_router(management.router)
    application.include_router(compatibility.router)
    application.include_router(proxy.router)
    return application


app = create_app()

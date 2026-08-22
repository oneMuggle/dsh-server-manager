from __future__ import annotations

from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .routers import auth, management, plugins, providers, proxy, uploads, users
from .runtime import SWAGGER_UI_STATIC_DIR, config
from .web import MANAGEMENT_STATIC_ROOT as MANAGEMENT_STATIC_DIR


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.session_secret = config.session_secret_path.read_bytes()
    application.state.http = aiohttp.ClientSession(auto_decompress=False)
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


async def health() -> dict[str, bool]:
    return {"ok": True}


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
    application.add_api_route(
        "/api/gateway/v1/health",
        health,
        methods=["GET"],
        tags=["系统"],
        summary="健康检查",
    )
    application.include_router(auth.router)
    application.include_router(management.router)
    application.include_router(users.router)
    application.include_router(plugins.router)
    application.include_router(providers.router)
    application.include_router(uploads.router)
    application.include_router(proxy.router)
    return application


app = create_app()

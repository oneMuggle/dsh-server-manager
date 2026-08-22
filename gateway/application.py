from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import aiohttp
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import (
    FixedWindowLimiter,
    hash_password,
    make_session,
    parse_session,
    verify_password,
)
from .config import Config
from .dependencies import ApiToken, api_token as _api_token
from .harness_rpc import (
    HarnessRpcBusinessError,
    HarnessRpcError,
    HarnessRpcTransportError,
    call_harness_rpc,
)
from .pages import admin_page, auth_page
from .plugin_ui import plugin_admin_page
from .web import STATIC_ROOT as MANAGEMENT_STATIC_DIR
from .plugins import PluginStoreError, get_job, list_jobs, list_plugins
from .providers import (
    PROVIDER_NAMESPACE,
    CreateCustomProvider,
    CustomProviderCreated,
)
from .schemas.auth import AuthenticatedUser, Credentials, CurrentUser
from .schemas.plugins import OnlinePluginImport, PluginAssignment
from .schemas.uploads import DirectoryListing
from .schemas.users import RegisteredUser, RegisteredUsers
from .proxy import cookie_from_websocket, proxy_http, proxy_websocket
from .registry import RegistryError, get_user, list_users
from .supervisor_client import (
    SupervisorError,
    assign_plugin,
    create_user,
    discover_plugin_versions,
    import_offline_plugin,
    import_online_plugin,
    install_upload,
    list_upload_directories,
)

LOGGER = logging.getLogger("dsh.gateway")
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
SWAGGER_UI_STATIC_DIR = Path(__file__).parent / "static" / "swagger-ui"
config = Config.from_environment()
login_ip_limiter = FixedWindowLimiter(attempts=30, seconds=60)
login_account_limiter = FixedWindowLimiter(attempts=10, seconds=60)
register_limiter = FixedWindowLimiter(attempts=5, seconds=300)


def _rpc_failure(error: HarnessRpcError, operation: str) -> HTTPException:
    if isinstance(error, HarnessRpcTransportError):
        return HTTPException(status_code=503, detail=f"{operation}：{error}")
    if isinstance(error, HarnessRpcBusinessError):
        status = 409 if error.code == "settings-conflict" else 422
        return HTTPException(
            status_code=status,
            detail={
                "message": f"{operation}：{error.message}",
                "upstreamCode": error.code,
            },
        )
    return HTTPException(status_code=502, detail=f"{operation}失败")


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _resolve_user(token: str | None) -> dict | None:
    if not token:
        return None
    payload = parse_session(token, app.state.session_secret)
    if payload is None:
        return None
    try:
        user = get_user(config.user_database_path, payload["sub"])
    except RegistryError:
        LOGGER.exception("failed to load registry")
        return None
    if not user or user.get("disabled") is True:
        return None
    if int(user.get("password_version", 0)) != payload["pv"]:
        return None
    return user


def _request_user(request: Request) -> dict | None:
    return _resolve_user(request.cookies.get(config.cookie_name))



def _user_role(user: dict) -> Literal["admin", "user"]:
    return "admin" if user.get("role") == "admin" else "user"


def _require_session(token: str | None) -> dict:
    user = _resolve_user(token)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _require_admin(token: str | None) -> dict:
    user = _require_session(token)
    if _user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _make_session_token(user: dict) -> str:
    return make_session(
        str(user["username"]),
        int(user["password_version"]),
        app.state.session_secret,
        config.session_ttl_seconds,
    )


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.cookie_name,
        value=token,
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def _authenticated_response(user: dict, status_code: int = 200) -> JSONResponse:
    token = _make_session_token(user)
    response = JSONResponse(
        {
            "username": user["username"],
            "role": _user_role(user),
            "accessToken": token,
            "tokenType": "Bearer",
            "expiresIn": config.session_ttl_seconds,
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
    _set_session(response, token)
    return response


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.session_secret = config.session_secret_path.read_bytes()
    application.state.http = aiohttp.ClientSession(auto_decompress=False)
    try:
        yield
    finally:
        await application.state.http.close()


app = FastAPI(
    title="DSH LAN Gateway",
    description=(
        "DeepSeek Harness 多用户局域网网关。受保护接口支持登录后生成的 "
        "HttpOnly Cookie 或 Bearer Token。"
    ),
    version="0.4.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)
app.mount(
    "/static/swagger-ui",
    StaticFiles(directory=SWAGGER_UI_STATIC_DIR),
    name="swagger-ui-static",
)
app.mount(
    "/assets",
    StaticFiles(directory=MANAGEMENT_STATIC_DIR),
    name="management-static",
)


@app.get("/docs", include_in_schema=False)
async def swagger_ui(request: Request) -> HTMLResponse:
    root_path = request.scope.get("root_path", "").rstrip("/")
    static_url = f"{root_path}/static/swagger-ui"
    return get_swagger_ui_html(
        openapi_url=f"{root_path}{app.openapi_url}",
        title=f"{app.title} - Swagger UI",
        swagger_js_url=f"{static_url}/swagger-ui-bundle.js",
        swagger_css_url=f"{static_url}/swagger-ui.css",
        swagger_favicon_url=f"{static_url}/favicon-32x32.png",
    )


@app.get("/api/gateway/v1/health", tags=["系统"], summary="健康检查")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> Response:
    if _request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("login"), headers={"Cache-Control": "no-store"})


@app.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request) -> Response:
    if _request_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth_page("register"), headers={"Cache-Control": "no-store"})


@app.post(
    "/api/gateway/v1/auth/register",
    status_code=201,
    response_model=AuthenticatedUser,
    tags=["认证"],
    summary="注册账号",
)
async def register(request: Request, credentials: Credentials) -> Response:
    if not register_limiter.allow(_client_key(request)):
        raise HTTPException(status_code=429, detail="注册请求过于频繁")
    if not USERNAME_PATTERN.fullmatch(credentials.username):
        raise HTTPException(
            status_code=422,
            detail="用户名须以小写字母开头，只能包含小写字母、数字、_ 和 -",
        )

    password_record = await asyncio.to_thread(hash_password, credentials.password)
    try:
        result = await create_user(
            config.control_socket_path,
            credentials.username,
            password_record,
        )
    except SupervisorError as error:
        status = 409 if error.code == "conflict" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

    user = {
        "username": result["username"],
        "password_version": result["password_version"],
        "role": result["role"],
    }
    return _authenticated_response(user, status_code=201)


@app.post(
    "/api/gateway/v1/auth/login",
    response_model=AuthenticatedUser,
    tags=["认证"],
    summary="登录",
)
async def login(request: Request, credentials: Credentials) -> Response:
    client = _client_key(request)
    account_key = f"{client}:{credentials.username}"
    if not login_ip_limiter.allow(client) or not login_account_limiter.allow(account_key):
        raise HTTPException(status_code=429, detail="登录请求过于频繁")
    try:
        user = get_user(config.user_database_path, credentials.username)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error

    valid = bool(user and user.get("disabled") is not True)
    if valid:
        valid = await asyncio.to_thread(
            verify_password,
            credentials.password,
            user.get("password", {}),
        )
    else:
        await asyncio.to_thread(hash_password, credentials.password)
    if not valid or user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    return _authenticated_response(user)


@app.post(
    "/api/gateway/v1/auth/logout",
    status_code=204,
    tags=["认证"],
    summary="退出登录",
)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(config.cookie_name, path="/")
    return response


@app.get(
    "/api/gateway/v1/auth/me",
    response_model=CurrentUser,
    tags=["认证"],
    summary="查询当前账号",
)
async def me(token: ApiToken) -> CurrentUser:
    user = _require_session(token)
    return CurrentUser(username=str(user["username"]), role=_user_role(user))


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def management_page(request: Request) -> Response:
    user = _request_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if _user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return HTMLResponse(admin_page(), headers={"Cache-Control": "no-store"})


@app.get("/admin/plugins", response_class=HTMLResponse, include_in_schema=False)
async def plugin_management_page(request: Request) -> Response:
    user = _request_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if _user_role(user) != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return HTMLResponse(plugin_admin_page(), headers={"Cache-Control": "no-store"})


@app.get(
    "/api/gateway/v1/admin/users",
    response_model=RegisteredUsers,
    tags=["管理"],
    summary="列出注册用户",
)
async def registered_users(token: ApiToken) -> RegisteredUsers:
    _require_admin(token)
    try:
        stored_users = list_users(config.user_database_path)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error
    users = [
        RegisteredUser(
            username=str(user["username"]),
            role=_user_role(user),
            disabled=user.get("disabled") is True,
            created_at=str(user.get("created_at", "")),
        )
        for user in stored_users
    ]
    users.sort(key=lambda user: (user.created_at, user.username))
    return RegisteredUsers(users=users)


@app.get(
    "/api/gateway/v1/admin/plugins",
    tags=["插件管理"],
    summary="列出共享插件及用户状态",
)
async def registered_plugins(token: ApiToken) -> dict:
    _require_admin(token)
    try:
        return {"plugins": list_plugins(config.user_database_path)}
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error


@app.get(
    "/api/gateway/v1/admin/plugin-jobs",
    tags=["插件管理"],
    summary="列出插件任务",
)
async def plugin_jobs(token: ApiToken, limit: int = 30) -> dict:
    _require_admin(token)
    try:
        return {"jobs": list_jobs(config.user_database_path, limit)}
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error


@app.get(
    "/api/gateway/v1/admin/plugin-jobs/{job_id}",
    tags=["插件管理"],
    summary="查询插件任务详情",
)
async def plugin_job(job_id: str, token: ApiToken) -> dict:
    _require_admin(token)
    try:
        job = get_job(config.user_database_path, job_id)
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error
    if job is None:
        raise HTTPException(status_code=404, detail="插件任务不存在")
    return job


@app.post(
    "/api/gateway/v1/admin/plugins/online",
    status_code=202,
    tags=["插件管理"],
    summary="按 npm 包名导入共享插件",
)
async def online_plugin_import(payload: OnlinePluginImport, token: ApiToken) -> dict:
    user = _require_admin(token)
    try:
        return await import_online_plugin(
            config.control_socket_path,
            payload.package_name,
            payload.version,
            str(user["username"]),
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.get(
    "/api/gateway/v1/admin/plugins/registry-versions",
    tags=["插件管理"],
    summary="发现 npm 插件的所有可用版本",
)
async def registry_plugin_versions(
    token: ApiToken,
    package_name: Annotated[
        str,
        Query(alias="packageName", min_length=1, max_length=214),
    ],
) -> dict:
    _require_admin(token)
    try:
        return await discover_plugin_versions(
            config.control_socket_path,
            package_name,
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.post(
    "/api/gateway/v1/admin/plugins/offline",
    status_code=202,
    tags=["插件管理"],
    summary="导入离线共享插件包",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def offline_plugin_import(request: Request, token: ApiToken) -> dict:
    user = _require_admin(token)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from error
        if declared_size <= 0 or declared_size > config.max_upload_bytes:
            raise HTTPException(status_code=413, detail="离线插件包必须小于 100 MB")

    spool_name = f"{secrets.token_hex(16)}.plugin.tgz"
    spool_path = config.upload_spool_path / spool_name
    descriptor = os.open(
        spool_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > config.max_upload_bytes:
                raise HTTPException(status_code=413, detail="离线插件包不能超过 100 MB")
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(descriptor, remaining)
                if not written:
                    raise OSError("failed to write plugin spool")
                remaining = remaining[written:]
        if size == 0:
            raise HTTPException(status_code=400, detail="离线插件包不能为空")
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        descriptor = -1
        spool_path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        return await import_offline_plugin(
            config.control_socket_path,
            spool_name,
            str(user["username"]),
        )
    except SupervisorError as error:
        spool_path.unlink(missing_ok=True)
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.post(
    "/api/gateway/v1/admin/plugin-jobs",
    status_code=202,
    tags=["插件管理"],
    summary="批量安装、卸载、启用或停用插件",
)
async def create_plugin_assignment(payload: PluginAssignment, token: ApiToken) -> dict:
    user = _require_admin(token)
    if payload.scope.type == "users" and not payload.scope.usernames:
        raise HTTPException(status_code=400, detail="至少选择一个用户")
    try:
        return await assign_plugin(
            config.control_socket_path,
            plugin_action=payload.action,
            plugin_id=payload.plugin_id,
            plugin_version_id=payload.version_id,
            scope=payload.scope.model_dump(),
            enabled_after_install=payload.enabled_after_install,
            apply_to_future_users=payload.apply_to_future_users,
            created_by=str(user["username"]),
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.post(
    "/api/gateway/v1/admin/users/{username}/providers",
    status_code=201,
    response_model=CustomProviderCreated,
    response_model_by_alias=True,
    tags=["管理"],
    summary="为指定用户添加自定义模型提供方",
    description=(
        "字段与 DSH 的“设置 → 模型 → 自定义提供方”一致。提供方配置通过目标用户 "
        "Harness 的 settings.mutate 写入；可选 API Key 通过 credentials.set 单独保存。"
    ),
)
async def create_custom_provider(
    username: str,
    provider: CreateCustomProvider,
    token: ApiToken,
) -> CustomProviderCreated:
    _require_admin(token)
    try:
        target = get_user(config.user_database_path, username)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error
    if target is None:
        raise HTTPException(status_code=404, detail="目标用户不存在")
    if target.get("disabled") is True:
        raise HTTPException(status_code=409, detail="目标用户已禁用")
    try:
        port = int(target["port"])
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=503, detail="目标用户服务配置无效") from error

    try:
        providers_value = await call_harness_rpc(
            app.state.http,
            port,
            "llm.providers",
            {},
        )
        settings_value = await call_harness_rpc(
            app.state.http,
            port,
            "settings.describe",
            {},
        )
    except HarnessRpcError as error:
        raise _rpc_failure(error, "读取目标用户模型设置") from error

    if not isinstance(providers_value, dict) or not isinstance(
        providers_value.get("providers"), list
    ):
        raise HTTPException(status_code=502, detail="目标 Harness 提供方目录格式无效")
    taken = {
        row.get("provider")
        for row in providers_value["providers"]
        if isinstance(row, dict) and isinstance(row.get("provider"), str)
    }
    if provider.provider_id in taken:
        raise HTTPException(status_code=409, detail="该提供方 ID 已存在")

    if not isinstance(settings_value, dict) or not isinstance(
        settings_value.get("namespaces"), list
    ):
        raise HTTPException(status_code=502, detail="目标 Harness 设置目录格式无效")
    if settings_value.get("writable") is not True:
        raise HTTPException(status_code=409, detail="目标用户设置当前不可写")
    namespace = next(
        (
            row
            for row in settings_value["namespaces"]
            if isinstance(row, dict) and row.get("ns") == PROVIDER_NAMESPACE
        ),
        None,
    )
    if namespace is None or not isinstance(namespace.get("revision"), int):
        raise HTTPException(status_code=409, detail="目标用户未加载自定义提供方设置模块")

    credential_ref = provider.credential_ref() if provider.api_key is not None else None
    if credential_ref is not None:
        try:
            credential_value = await call_harness_rpc(
                app.state.http,
                port,
                "credentials.describe",
                {"refs": [credential_ref]},
            )
        except HarnessRpcError as error:
            raise _rpc_failure(error, "检查目标用户凭据存储") from error
        credentials = (
            credential_value.get("credentials")
            if isinstance(credential_value, dict)
            else None
        )
        credential = credentials.get(credential_ref) if isinstance(credentials, dict) else None
        if not isinstance(credential, dict):
            raise HTTPException(status_code=502, detail="目标 Harness 凭据状态格式无效")
        if credential.get("writable") is not True:
            raise HTTPException(status_code=409, detail="目标用户的该凭据当前不可写")

    try:
        await call_harness_rpc(
            app.state.http,
            port,
            "settings.mutate",
            {
                "ns": PROVIDER_NAMESPACE,
                "ops": [
                    {
                        "op": "set",
                        "path": ["providers", provider.provider_id],
                        "value": provider.provider_profile(),
                    }
                ],
                "expectedRevision": namespace["revision"],
            },
        )
    except HarnessRpcError as error:
        raise _rpc_failure(error, "创建目标用户提供方") from error

    if credential_ref is not None and provider.api_key is not None:
        try:
            await call_harness_rpc(
                app.state.http,
                port,
                "credentials.set",
                {"ref": credential_ref, "value": provider.api_key},
            )
        except HarnessRpcError as error:
            detail = {
                "message": f"提供方已创建，但 API Key 保存失败：{error}",
                "providerCreated": True,
                "credentialStored": False,
            }
            if isinstance(error, HarnessRpcBusinessError):
                detail["upstreamCode"] = error.code
            raise HTTPException(status_code=502, detail=detail) from error

    return CustomProviderCreated(
        username=username,
        providerId=provider.provider_id,
        credentialRef=credential_ref,
        credentialStored=credential_ref is not None,
    )


@app.get(
    "/api/gateway/v1/files/directories",
    response_model=DirectoryListing,
    tags=["文件"],
    summary="列出当前用户工作区目录",
)
async def upload_directories(
    token: ApiToken,
    directory: str = ".",
) -> DirectoryListing:
    user = _require_session(token)
    if len(directory) > 512:
        raise HTTPException(status_code=400, detail="目标目录过长")
    try:
        result = await list_upload_directories(
            config.control_socket_path,
            str(user["username"]),
            directory,
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error
    return DirectoryListing.model_validate(result)


@app.post(
    "/api/gateway/v1/files/upload",
    tags=["文件"],
    summary="上传文件到当前用户工作区",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_file(
    request: Request,
    token: ApiToken,
    directory: str = ".",
    name: str = "",
) -> Response:
    user = _require_session(token)
    if not name:
        raise HTTPException(status_code=400, detail="缺少文件名")
    if len(directory) > 512 or len(name) > 255:
        raise HTTPException(status_code=400, detail="目标目录或文件名过长")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from error
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="无效的 Content-Length")
        if declared_size > config.max_upload_bytes:
            raise HTTPException(status_code=413, detail="单个文件不能超过 100 MB")

    spool_name = f"{secrets.token_hex(16)}.upload"
    spool_path = config.upload_spool_path / spool_name
    descriptor = os.open(
        spool_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > config.max_upload_bytes:
                raise HTTPException(status_code=413, detail="单个文件不能超过 100 MB")
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise OSError("failed to write upload spool")
                remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        descriptor = -1
        spool_path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        result = await install_upload(
            config.control_socket_path,
            str(user["username"]),
            spool_name,
            directory,
            name,
            size,
        )
    except SupervisorError as error:
        spool_path.unlink(missing_ok=True)
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

    return JSONResponse(
        {
            "directory": result["directory"],
            "name": result["filename"],
            "size": result["size"],
        }
    )


async def _proxy_authenticated_request(request: Request) -> Response:
    user = _request_user(request)
    if not user:
        if request.method in {"GET", "HEAD"}:
            return RedirectResponse("/login", status_code=303)
        raise HTTPException(status_code=401, detail="未登录")
    return await proxy_http(
        request,
        app.state.http,
        int(user["port"]),
        config.max_transform_bytes,
        is_admin=_user_role(user) == "admin",
    )


@app.api_route(
    "/",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def main_page(request: Request) -> Response:
    """Authenticated main entry; the DSH Web application is the main interface."""
    return await _proxy_authenticated_request(request)


@app.api_route(
    "/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def harness_proxy(request: Request, path: str) -> Response:
    return await _proxy_authenticated_request(request)


@app.websocket("/{path:path}")
async def harness_websocket(websocket: WebSocket, path: str) -> None:
    token = cookie_from_websocket(websocket, config.cookie_name)
    user = _resolve_user(token)
    if not user:
        await websocket.close(code=4401, reason="authentication required")
        return
    await proxy_websocket(websocket, app.state.http, int(user["port"]))

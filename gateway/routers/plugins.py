from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from common.errors import PluginStoreError
from common.storage.plugin_catalog import list_plugins
from common.storage.plugin_jobs import get_job, list_jobs

from ..dependencies import ApiToken
from ..runtime import config
from ..schemas.plugins import OnlinePluginImport, PluginAssignment
from ..services.auth import require_admin
from ..services.spool import SpoolError, receive_spool
from ..services.supervisor import (
    SupervisorError,
    assign_plugin,
    discover_plugin_versions,
    import_offline_plugin,
    import_online_plugin,
)


router = APIRouter()


@router.get(
    "/api/gateway/v1/admin/plugins",
    tags=["插件管理"],
    summary="列出共享插件及用户状态",
)
async def registered_plugins(request: Request, token: ApiToken) -> dict:
    require_admin(request, token)
    try:
        return {"plugins": list_plugins(config.user_database_path)}
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error


@router.get(
    "/api/gateway/v1/admin/plugin-jobs",
    tags=["插件管理"],
    summary="列出插件任务",
)
async def plugin_jobs(request: Request, token: ApiToken, limit: int = 30) -> dict:
    require_admin(request, token)
    try:
        return {"jobs": list_jobs(config.user_database_path, limit)}
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error


@router.get(
    "/api/gateway/v1/admin/plugin-jobs/{job_id}",
    tags=["插件管理"],
    summary="查询插件任务详情",
)
async def plugin_job(job_id: str, request: Request, token: ApiToken) -> dict:
    require_admin(request, token)
    try:
        job = get_job(config.user_database_path, job_id)
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error
    if job is None:
        raise HTTPException(status_code=404, detail="插件任务不存在")
    return job


@router.post(
    "/api/gateway/v1/admin/plugins/online",
    status_code=202,
    tags=["插件管理"],
    summary="按 npm 包名导入共享插件",
)
async def online_plugin_import(payload: OnlinePluginImport, request: Request, token: ApiToken) -> dict:
    user = require_admin(request, token)
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


@router.get(
    "/api/gateway/v1/admin/plugins/registry-versions",
    tags=["插件管理"],
    summary="发现 npm 插件的所有可用版本",
)
async def registry_plugin_versions(
    request: Request,
    token: ApiToken,
    package_name: Annotated[
        str,
        Query(alias="packageName", min_length=1, max_length=214),
    ],
) -> dict:
    require_admin(request, token)
    try:
        return await discover_plugin_versions(
            config.control_socket_path,
            package_name,
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post(
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
    user = require_admin(request, token)
    try:
        spool = await receive_spool(
            request.stream(),
            config.upload_spool_path,
            suffix=".plugin.tgz",
            max_bytes=config.max_upload_bytes,
            content_length=request.headers.get("content-length"),
            allow_empty=False,
        )
    except SpoolError as error:
        if error.code == "invalid_length":
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from error
        if error.code in {"declared_empty", "declared_too_large"}:
            raise HTTPException(status_code=413, detail="离线插件包必须小于 100 MB") from error
        if error.code == "too_large":
            raise HTTPException(status_code=413, detail="离线插件包不能超过 100 MB") from error
        if error.code == "empty":
            raise HTTPException(status_code=400, detail="离线插件包不能为空") from error
        raise

    try:
        return await import_offline_plugin(
            config.control_socket_path,
            spool.name,
            str(user["username"]),
        )
    except SupervisorError as error:
        spool.path.unlink(missing_ok=True)
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post(
    "/api/gateway/v1/admin/plugin-jobs",
    status_code=202,
    tags=["插件管理"],
    summary="批量安装、卸载、启用或停用插件",
)
async def create_plugin_assignment(payload: PluginAssignment, request: Request, token: ApiToken) -> dict:
    user = require_admin(request, token)
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

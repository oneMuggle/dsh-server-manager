from fastapi import APIRouter, HTTPException, Request

from common.errors import PluginStoreError
from common.storage.plugin_jobs import get_job, list_jobs

from ...dependencies import ApiToken
from ...runtime import config
from ...schemas.plugins import PluginAssignment
from ...services.auth import require_admin
from ...services.supervisor import SupervisorError, assign_plugin


router = APIRouter(prefix="/plugins/jobs")


@router.get("", summary="列出插件任务")
async def plugin_jobs(request: Request, token: ApiToken, limit: int = 30) -> dict:
    require_admin(request, token)
    try:
        return {"jobs": list_jobs(config.user_database_path, limit)}
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error


@router.get("/{job_id}", summary="查询插件任务详情")
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
    "",
    status_code=202,
    summary="批量安装、卸载、启用或停用插件",
)
async def create_plugin_assignment(
    payload: PluginAssignment,
    request: Request,
    token: ApiToken,
) -> dict:
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

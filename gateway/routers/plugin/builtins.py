from fastapi import APIRouter, HTTPException, Request

from ...dependencies import ApiToken
from ...runtime import config
from ...schemas.plugins import BuiltinPluginAssignment
from ...services.auth import require_admin
from ...services.supervisor import SupervisorError, assign_builtin_plugin


router = APIRouter(prefix="/plugins/builtins")


@router.post("/jobs", status_code=202, summary="批量启用或停用官方内置插件")
async def create_builtin_plugin_assignment(
    payload: BuiltinPluginAssignment,
    request: Request,
    token: ApiToken,
) -> dict:
    user = require_admin(request, token)
    if payload.scope.type == "users" and not payload.scope.usernames:
        raise HTTPException(status_code=400, detail="至少选择一个用户")
    try:
        return await assign_builtin_plugin(
            config.control_socket_path,
            plugin_action=payload.action,
            entry_id=payload.entry_id,
            scope=payload.scope.model_dump(),
            apply_to_future_users=payload.apply_to_future_users,
            created_by=str(user["username"]),
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

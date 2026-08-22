from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ...dependencies import ApiToken
from ...runtime import config
from ...services.auth import require_admin
from ...services.supervisor import SupervisorError, discover_plugin_versions


router = APIRouter(prefix="/plugins/registry")


@router.get("/versions", summary="发现 npm 插件的所有可用版本")
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

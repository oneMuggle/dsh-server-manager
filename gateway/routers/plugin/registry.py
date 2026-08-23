from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ...dependencies import ApiToken
from ...runtime import config
from ...schemas.plugins import PluginRegistryConfiguration, UpdatePluginRegistry
from ...services.auth import require_admin
from ...services.supervisor import (
    SupervisorError,
    discover_plugin_versions,
    plugin_registry_configuration,
    update_plugin_registry,
)


router = APIRouter(prefix="/plugins/registry")


def _registry_error(error: SupervisorError) -> HTTPException:
    status = 400 if error.code == "bad_request" else 503
    return HTTPException(status_code=status, detail=str(error))


@router.get(
    "",
    response_model=PluginRegistryConfiguration,
    response_model_by_alias=True,
    summary="查询当前 npm Registry 配置",
)
async def get_registry_configuration(
    request: Request,
    token: ApiToken,
) -> dict:
    require_admin(request, token)
    try:
        return await plugin_registry_configuration(config.control_socket_path)
    except SupervisorError as error:
        raise _registry_error(error) from error


@router.put(
    "",
    response_model=PluginRegistryConfiguration,
    response_model_by_alias=True,
    summary="保存 npm Registry 配置",
)
async def put_registry_configuration(
    request: Request,
    update: UpdatePluginRegistry,
    token: ApiToken,
) -> dict:
    require_admin(request, token)
    try:
        return await update_plugin_registry(
            config.control_socket_path,
            update.registry_url,
        )
    except SupervisorError as error:
        raise _registry_error(error) from error


@router.delete(
    "",
    status_code=204,
    summary="恢复启动时的默认 npm Registry",
)
async def delete_registry_configuration(
    request: Request,
    token: ApiToken,
) -> Response:
    require_admin(request, token)
    try:
        await update_plugin_registry(config.control_socket_path, None)
    except SupervisorError as error:
        raise _registry_error(error) from error
    return Response(status_code=204)


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
        raise _registry_error(error) from error

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ...dependencies import ApiToken
from ...runtime import config
from ...services.auth import require_admin
from ...services.supervisor import (
    SupervisorError,
    python_pip_index_configuration,
    update_python_pip_index,
)


router = APIRouter(prefix="/system/settings")


def _settings_error(error: SupervisorError) -> HTTPException:
    status = 400 if error.code == "bad_request" else 503
    return HTTPException(status_code=status, detail=str(error))


class PipIndexConfiguration(BaseModel):
    pip_index_url: str | None = Field(None, alias="pipIndexUrl")
    source: str = Field(..., alias="source")
    default_pip_index_url: str = Field(..., alias="defaultPipIndexUrl")

    model_config = {"populate_by_name": True}


class UpdatePipIndexRequest(BaseModel):
    pip_index_url: str | None = Field(None, alias="pipIndexUrl")


@router.get(
    "/pip-index",
    response_model=PipIndexConfiguration,
    response_model_by_alias=True,
    summary="查询当前 Python pip 镜像源配置",
)
async def get_pip_index_configuration(
    request: Request,
    token: ApiToken,
) -> dict:
    require_admin(request, token)
    try:
        return await python_pip_index_configuration(config.control_socket_path)
    except SupervisorError as error:
        raise _settings_error(error) from error


@router.put(
    "/pip-index",
    response_model=PipIndexConfiguration,
    response_model_by_alias=True,
    summary="保存 Python pip 镜像源配置",
)
async def put_pip_index_configuration(
    request: Request,
    update: UpdatePipIndexRequest,
    token: ApiToken,
) -> dict:
    require_admin(request, token)
    try:
        return await update_python_pip_index(
            config.control_socket_path,
            update.pip_index_url,
        )
    except SupervisorError as error:
        raise _settings_error(error) from error


@router.delete(
    "/pip-index",
    status_code=204,
    summary="恢复默认的 Python pip 镜像源",
)
async def delete_pip_index_configuration(
    request: Request,
    token: ApiToken,
) -> Response:
    require_admin(request, token)
    try:
        await update_python_pip_index(config.control_socket_path, None)
    except SupervisorError as error:
        raise _settings_error(error) from error
    return Response(status_code=204)

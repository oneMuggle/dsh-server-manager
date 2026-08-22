from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...dependencies import ApiToken
from ...runtime import config
from ...schemas.providers import CreateCustomProvider, CustomProviderCreated
from ...services.auth import require_admin
from ...services.providers import ProviderOperationError, create_provider


router = APIRouter(prefix="/users")

STATUS_BY_CODE = {
    "unavailable": 503,
    "not_found": 404,
    "disabled": 409,
    "invalid_target": 503,
    "invalid_upstream": 502,
    "conflict": 409,
    "business": 422,
    "transport": 503,
    "credential_partial": 502,
}


def _http_error(error: ProviderOperationError) -> HTTPException:
    status = STATUS_BY_CODE.get(error.code, 502)
    if error.code == "business":
        if error.upstream_code == "settings-conflict":
            status = 409
        detail: str | dict = {"message": error.message}
        if error.upstream_code is not None:
            detail["upstreamCode"] = error.upstream_code
        return HTTPException(status_code=status, detail=detail)
    if error.code == "credential_partial":
        detail = {
            "message": error.message,
            "providerCreated": error.provider_created,
            "credentialStored": error.credential_stored,
        }
        if error.upstream_code is not None:
            detail["upstreamCode"] = error.upstream_code
        return HTTPException(status_code=status, detail=detail)
    return HTTPException(status_code=status, detail=error.message)


@router.post(
    "/{username}/providers",
    status_code=201,
    response_model=CustomProviderCreated,
    response_model_by_alias=True,
    summary="为指定用户添加自定义模型提供方",
    description=(
        "字段与 DSH 的“设置 → 模型 → 自定义提供方”一致。提供方配置通过目标用户 "
        "Harness 的 settings.mutate 写入；可选 API Key 通过 credentials.set 单独保存。"
    ),
)
async def create_custom_provider(
    username: str,
    provider: CreateCustomProvider,
    request: Request,
    token: ApiToken,
) -> CustomProviderCreated:
    require_admin(request, token)
    try:
        return await create_provider(
            config.user_database_path,
            request.app.state.http,
            username,
            provider,
        )
    except ProviderOperationError as error:
        raise _http_error(error) from error

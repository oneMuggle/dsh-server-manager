from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from common.storage.users import RegistryError, get_user

from ..dependencies import ApiToken
from ..harness_rpc import HarnessRpcBusinessError, HarnessRpcError
from ..providers import PROVIDER_NAMESPACE, CreateCustomProvider, CustomProviderCreated
from ..runtime import config
from ..services.auth import require_admin
from ..services.harness import call_harness_rpc, rpc_failure


router = APIRouter()


@router.post(
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
    request: Request,
    token: ApiToken,
) -> CustomProviderCreated:
    require_admin(request, token)
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
            request.app.state.http,
            port,
            "llm.providers",
            {},
        )
        settings_value = await call_harness_rpc(
            request.app.state.http,
            port,
            "settings.describe",
            {},
        )
    except HarnessRpcError as error:
        raise rpc_failure(error, "读取目标用户模型设置") from error

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
                request.app.state.http,
                port,
                "credentials.describe",
                {"refs": [credential_ref]},
            )
        except HarnessRpcError as error:
            raise rpc_failure(error, "检查目标用户凭据存储") from error
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
            request.app.state.http,
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
        raise rpc_failure(error, "创建目标用户提供方") from error

    if credential_ref is not None and provider.api_key is not None:
        try:
            await call_harness_rpc(
                request.app.state.http,
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

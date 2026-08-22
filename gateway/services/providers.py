from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import aiohttp

from common.storage.users import RegistryError, get_user

from ..schemas.providers import (
    PROVIDER_NAMESPACE,
    CustomProviderCreated,
)
from .harness import (
    HarnessRpcBusinessError,
    HarnessRpcError,
    HarnessRpcTransportError,
    call_harness_rpc,
)


@dataclass(slots=True)
class ProviderOperationError(Exception):
    code: str
    message: str
    upstream_code: str | None = None
    provider_created: bool = False
    credential_stored: bool | None = None

    def __str__(self) -> str:
        return self.message


class ProviderDefinition(Protocol):
    provider_id: str
    api_key: str | None

    def credential_ref(self) -> str: ...

    def provider_profile(self) -> dict: ...


def _rpc_error(error: HarnessRpcError, operation: str) -> ProviderOperationError:
    if isinstance(error, HarnessRpcTransportError):
        return ProviderOperationError("transport", f"{operation}：{error}")
    if isinstance(error, HarnessRpcBusinessError):
        return ProviderOperationError(
            "business",
            f"{operation}：{error.message}",
            upstream_code=error.code,
        )
    return ProviderOperationError("invalid_upstream", f"{operation}失败")


async def create_provider(
    database_path: Path,
    session: aiohttp.ClientSession,
    username: str,
    provider: ProviderDefinition,
    *,
    replace: bool = False,
) -> CustomProviderCreated:
    try:
        target = get_user(database_path, username)
    except RegistryError as error:
        raise ProviderOperationError("unavailable", "用户服务不可用") from error
    if target is None:
        raise ProviderOperationError("not_found", "目标用户不存在")
    if target.get("disabled") is True:
        raise ProviderOperationError("disabled", "目标用户已禁用")
    try:
        port = int(target["port"])
    except (KeyError, TypeError, ValueError) as error:
        raise ProviderOperationError("invalid_target", "目标用户服务配置无效") from error

    try:
        providers_value = await call_harness_rpc(session, port, "llm.providers", {})
        settings_value = await call_harness_rpc(session, port, "settings.describe", {})
    except HarnessRpcError as error:
        raise _rpc_error(error, "读取目标用户模型设置") from error

    if not isinstance(providers_value, dict) or not isinstance(
        providers_value.get("providers"), list
    ):
        raise ProviderOperationError("invalid_upstream", "目标 Harness 提供方目录格式无效")
    taken = {
        row.get("provider")
        for row in providers_value["providers"]
        if isinstance(row, dict) and isinstance(row.get("provider"), str)
    }
    if provider.provider_id in taken and not replace:
        raise ProviderOperationError("conflict", "该提供方 ID 已存在")

    if not isinstance(settings_value, dict) or not isinstance(
        settings_value.get("namespaces"), list
    ):
        raise ProviderOperationError("invalid_upstream", "目标 Harness 设置目录格式无效")
    if settings_value.get("writable") is not True:
        raise ProviderOperationError("conflict", "目标用户设置当前不可写")
    namespace = next(
        (
            row
            for row in settings_value["namespaces"]
            if isinstance(row, dict) and row.get("ns") == PROVIDER_NAMESPACE
        ),
        None,
    )
    if namespace is None or not isinstance(namespace.get("revision"), int):
        raise ProviderOperationError(
            "conflict", "目标用户未加载自定义提供方设置模块"
        )

    credential_ref = provider.credential_ref() if provider.api_key is not None else None
    if credential_ref is not None:
        try:
            credential_value = await call_harness_rpc(
                session,
                port,
                "credentials.describe",
                {"refs": [credential_ref]},
            )
        except HarnessRpcError as error:
            raise _rpc_error(error, "检查目标用户凭据存储") from error
        credentials = (
            credential_value.get("credentials")
            if isinstance(credential_value, dict)
            else None
        )
        credential = credentials.get(credential_ref) if isinstance(credentials, dict) else None
        if not isinstance(credential, dict):
            raise ProviderOperationError(
                "invalid_upstream", "目标 Harness 凭据状态格式无效"
            )
        if credential.get("writable") is not True:
            raise ProviderOperationError("conflict", "目标用户的该凭据当前不可写")

    try:
        await call_harness_rpc(
            session,
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
        raise _rpc_error(error, "创建目标用户提供方") from error

    if credential_ref is not None and provider.api_key is not None:
        try:
            await call_harness_rpc(
                session,
                port,
                "credentials.set",
                {"ref": credential_ref, "value": provider.api_key},
            )
        except HarnessRpcError as error:
            upstream_code = error.code if isinstance(error, HarnessRpcBusinessError) else None
            raise ProviderOperationError(
                "credential_partial",
                f"提供方已创建，但 API Key 保存失败：{error}",
                upstream_code=upstream_code,
                provider_created=True,
                credential_stored=False,
            ) from error

    return CustomProviderCreated(
        username=username,
        providerId=provider.provider_id,
        credentialRef=credential_ref,
        credentialStored=credential_ref is not None,
    )

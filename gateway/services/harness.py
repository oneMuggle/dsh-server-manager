from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import aiohttp


@dataclass(slots=True)
class HarnessRpcError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class HarnessRpcTransportError(HarnessRpcError):
    pass


@dataclass(slots=True)
class HarnessRpcBusinessError(HarnessRpcError):
    code: str
    details: dict[str, Any]


def decode_rpc_response(body: Any, rpc_id: str) -> Any:
    if not isinstance(body, dict):
        raise HarnessRpcTransportError("目标 Harness 返回了无效响应")
    if body.get("type") != "server-response" or body.get("rpcId") != rpc_id:
        raise HarnessRpcTransportError("目标 Harness 返回了不匹配的 RPC 响应")
    result = body.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise HarnessRpcTransportError("目标 Harness 返回了无效 RPC 结果")
    if result["ok"]:
        if "value" not in result:
            raise HarnessRpcTransportError("目标 Harness RPC 结果缺少 value")
        return result["value"]
    error = result.get("error")
    if not isinstance(error, dict):
        raise HarnessRpcTransportError("目标 Harness RPC 错误格式无效")
    code = error.get("code")
    message = error.get("message")
    details = error.get("details", {})
    if not isinstance(code, str) or not isinstance(message, str) or not isinstance(details, dict):
        raise HarnessRpcTransportError("目标 Harness RPC 错误格式无效")
    raise HarnessRpcBusinessError(message=message, code=code, details=details)


async def call_harness_rpc(
    session: aiohttp.ClientSession,
    port: int,
    method: str,
    payload: dict[str, Any],
) -> Any:
    rpc_id = str(uuid4())
    url = f"http://127.0.0.1:{port}/api/{method}"
    envelope = {
        "type": "client-request",
        "rpcId": rpc_id,
        "method": method,
        "payload": payload,
    }
    try:
        async with session.post(
            url,
            json=envelope,
            headers={"Host": f"127.0.0.1:{port}"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status != 200:
                raise HarnessRpcTransportError(
                    f"目标 Harness RPC 返回 HTTP {response.status}"
                )
            try:
                body = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as error:
                raise HarnessRpcTransportError("目标 Harness 返回了非 JSON 响应") from error
    except HarnessRpcError:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        raise HarnessRpcTransportError("无法连接目标用户的 Harness 服务") from error
    return decode_rpc_response(body, rpc_id)

from fastapi import HTTPException

from gateway.harness_rpc import (
    HarnessRpcBusinessError,
    HarnessRpcError,
    HarnessRpcTransportError,
    call_harness_rpc,
    decode_rpc_response,
)


def rpc_failure(error: HarnessRpcError, operation: str) -> HTTPException:
    if isinstance(error, HarnessRpcTransportError):
        return HTTPException(status_code=503, detail=f"{operation}：{error}")
    if isinstance(error, HarnessRpcBusinessError):
        status = 409 if error.code == "settings-conflict" else 422
        return HTTPException(
            status_code=status,
            detail={
                "message": f"{operation}：{error.message}",
                "upstreamCode": error.code,
            },
        )
    return HTTPException(status_code=502, detail=f"{operation}失败")

__all__ = [
    "HarnessRpcBusinessError",
    "HarnessRpcError",
    "HarnessRpcTransportError",
    "call_harness_rpc",
    "decode_rpc_response",
    "rpc_failure",
]

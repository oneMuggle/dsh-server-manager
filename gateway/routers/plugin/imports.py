from fastapi import APIRouter, HTTPException, Request

from ...dependencies import ApiToken
from ...runtime import config
from ...schemas.plugins import OnlinePluginImport
from ...services.auth import require_admin
from ...services.spool import SpoolError, receive_spool
from ...services.supervisor import (
    SupervisorError,
    import_offline_plugin,
    import_online_plugin,
)


router = APIRouter(prefix="/plugins/imports")


@router.post(
    "/registry",
    status_code=202,
    summary="从 npm Registry 导入共享插件",
)
async def online_plugin_import(
    payload: OnlinePluginImport,
    request: Request,
    token: ApiToken,
) -> dict:
    user = require_admin(request, token)
    try:
        return await import_online_plugin(
            config.control_socket_path,
            payload.package_name,
            payload.version,
            str(user["username"]),
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post(
    "/archive",
    status_code=202,
    summary="从离线归档导入共享插件",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def offline_plugin_import(request: Request, token: ApiToken) -> dict:
    user = require_admin(request, token)
    try:
        spool = await receive_spool(
            request.stream(),
            config.upload_spool_path,
            suffix=".plugin.tgz",
            max_bytes=config.max_upload_bytes,
            content_length=request.headers.get("content-length"),
            allow_empty=False,
        )
    except SpoolError as error:
        if error.code == "invalid_length":
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from error
        if error.code in {"declared_empty", "declared_too_large"}:
            raise HTTPException(status_code=413, detail="离线插件包必须小于 100 MB") from error
        if error.code == "too_large":
            raise HTTPException(status_code=413, detail="离线插件包不能超过 100 MB") from error
        if error.code == "empty":
            raise HTTPException(status_code=400, detail="离线插件包不能为空") from error
        raise

    try:
        return await import_offline_plugin(
            config.control_socket_path,
            spool.name,
            str(user["username"]),
        )
    except SupervisorError as error:
        spool.path.unlink(missing_ok=True)
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

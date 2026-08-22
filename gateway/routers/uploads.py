from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..dependencies import ApiToken
from ..runtime import config
from ..schemas.uploads import DirectoryListing
from ..services.auth import require_session
from ..services.spool import SpoolError, receive_spool
from ..services.supervisor import (
    SupervisorError,
    install_upload,
    list_upload_directories,
)


router = APIRouter()


@router.get(
    "/api/gateway/v1/files/directories",
    response_model=DirectoryListing,
    tags=["文件"],
    summary="列出当前用户工作区目录",
)
async def upload_directories(
    request: Request,
    token: ApiToken,
    directory: str = ".",
) -> DirectoryListing:
    user = require_session(request, token)
    if len(directory) > 512:
        raise HTTPException(status_code=400, detail="目标目录过长")
    try:
        result = await list_upload_directories(
            config.control_socket_path,
            str(user["username"]),
            directory,
        )
    except SupervisorError as error:
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error
    return DirectoryListing.model_validate(result)


@router.post(
    "/api/gateway/v1/files/upload",
    tags=["文件"],
    summary="上传文件到当前用户工作区",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_file(
    request: Request,
    token: ApiToken,
    directory: str = ".",
    name: str = "",
) -> Response:
    user = require_session(request, token)
    if not name:
        raise HTTPException(status_code=400, detail="缺少文件名")
    if len(directory) > 512 or len(name) > 255:
        raise HTTPException(status_code=400, detail="目标目录或文件名过长")

    try:
        spool = await receive_spool(
            request.stream(),
            config.upload_spool_path,
            suffix=".upload",
            max_bytes=config.max_upload_bytes,
            content_length=request.headers.get("content-length"),
            allow_empty=True,
        )
    except SpoolError as error:
        if error.code == "invalid_length":
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from error
        if error.code in {"declared_too_large", "too_large"}:
            raise HTTPException(status_code=413, detail="单个文件不能超过 100 MB") from error
        raise

    try:
        result = await install_upload(
            config.control_socket_path,
            str(user["username"]),
            spool.name,
            directory,
            name,
            spool.size,
        )
    except SupervisorError as error:
        spool.path.unlink(missing_ok=True)
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

    return JSONResponse(
        {
            "directory": result["directory"],
            "name": result["filename"],
            "size": result["size"],
        }
    )

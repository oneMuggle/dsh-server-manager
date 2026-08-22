from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..dependencies import ApiToken
from ..runtime import config
from ..schemas.uploads import DirectoryListing
from ..services.auth import require_session
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

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="无效的 Content-Length") from error
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="无效的 Content-Length")
        if declared_size > config.max_upload_bytes:
            raise HTTPException(status_code=413, detail="单个文件不能超过 100 MB")

    spool_name = f"{secrets.token_hex(16)}.upload"
    spool_path = config.upload_spool_path / spool_name
    descriptor = os.open(
        spool_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > config.max_upload_bytes:
                raise HTTPException(status_code=413, detail="单个文件不能超过 100 MB")
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise OSError("failed to write upload spool")
                remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        descriptor = -1
        spool_path.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        result = await install_upload(
            config.control_socket_path,
            str(user["username"]),
            spool_name,
            directory,
            name,
            size,
        )
    except SupervisorError as error:
        spool_path.unlink(missing_ok=True)
        status = 400 if error.code == "bad_request" else 503
        raise HTTPException(status_code=status, detail=str(error)) from error

    return JSONResponse(
        {
            "directory": result["directory"],
            "name": result["filename"],
            "size": result["size"],
        }
    )

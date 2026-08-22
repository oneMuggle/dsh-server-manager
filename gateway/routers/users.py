from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from common.security import hash_password
from common.storage.users import RegistryError, get_user, list_users

from ..dependencies import ApiToken
from ..runtime import config
from ..schemas.users import (
    CreateManagedUser,
    RegisteredUser,
    RegisteredUsers,
    UpdateManagedUser,
)
from ..services.auth import require_admin, user_role
from ..services.supervisor import (
    SupervisorError,
    create_user,
    update_user,
)


router = APIRouter()


def _public_user(user: dict) -> RegisteredUser:
    return RegisteredUser(
        username=str(user["username"]),
        role=user_role(user),
        disabled=user.get("disabled") is True,
        created_at=str(user.get("created_at", "")),
    )


def _raise_supervisor_error(error: SupervisorError) -> None:
    status = {
        "not_found": 404,
        "conflict": 409,
        "bad_request": 422,
    }.get(error.code, 503)
    raise HTTPException(status_code=status, detail=str(error)) from error


@router.get(
    "/api/gateway/v1/admin/users",
    response_model=RegisteredUsers,
    tags=["管理"],
    summary="列出注册用户",
)
async def registered_users(request: Request, token: ApiToken) -> RegisteredUsers:
    require_admin(request, token)
    try:
        stored_users = list_users(config.user_database_path)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error
    users = [
        _public_user(user)
        for user in stored_users
    ]
    users.sort(key=lambda user: (user.created_at, user.username))
    return RegisteredUsers(users=users)


@router.get(
    "/api/gateway/v1/admin/users/{username}",
    response_model=RegisteredUser,
    tags=["管理"],
    summary="查询用户",
)
async def managed_user(
    username: str,
    request: Request,
    token: ApiToken,
) -> RegisteredUser:
    require_admin(request, token)
    try:
        user = get_user(config.user_database_path, username)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _public_user(user)


@router.post(
    "/api/gateway/v1/admin/users",
    response_model=RegisteredUser,
    status_code=201,
    tags=["管理"],
    summary="创建用户",
)
async def create_managed_user(
    request: Request,
    user: CreateManagedUser,
    token: ApiToken,
) -> RegisteredUser:
    require_admin(request, token)
    password_record = await asyncio.to_thread(hash_password, user.password)
    try:
        created = await create_user(
            config.control_socket_path,
            user.username,
            password_record,
        )
    except SupervisorError as error:
        _raise_supervisor_error(error)
    return _public_user(created)


@router.patch(
    "/api/gateway/v1/admin/users/{username}",
    response_model=RegisteredUser,
    tags=["管理"],
    summary="重置密码或切换用户状态",
)
async def update_managed_user(
    username: str,
    request: Request,
    change: UpdateManagedUser,
    token: ApiToken,
) -> RegisteredUser:
    require_admin(request, token)
    password_record = (
        await asyncio.to_thread(hash_password, change.password)
        if change.password is not None
        else None
    )
    try:
        updated = await update_user(
            config.control_socket_path,
            username,
            password_record=password_record,
            disabled=change.disabled,
        )
    except SupervisorError as error:
        _raise_supervisor_error(error)
    return _public_user(updated)


@router.delete(
    "/api/gateway/v1/admin/users/{username}",
    status_code=501,
    tags=["管理"],
    summary="删除用户（暂未实现）",
)
async def delete_managed_user(
    username: str,
    request: Request,
    token: ApiToken,
) -> None:
    require_admin(request, token)
    raise HTTPException(status_code=501, detail="用户删除暂未实现")

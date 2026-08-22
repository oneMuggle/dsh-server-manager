from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from common.storage.users import RegistryError, list_users

from ..dependencies import ApiToken
from ..runtime import config
from ..schemas.users import RegisteredUser, RegisteredUsers
from ..services.auth import require_admin, user_role


router = APIRouter()


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
        RegisteredUser(
            username=str(user["username"]),
            role=user_role(user),
            disabled=user.get("disabled") is True,
            created_at=str(user.get("created_at", "")),
        )
        for user in stored_users
    ]
    users.sort(key=lambda user: (user.created_at, user.username))
    return RegisteredUsers(users=users)

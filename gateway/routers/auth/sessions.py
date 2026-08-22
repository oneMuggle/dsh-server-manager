from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordRequestForm

from common.storage.users import RegistryError

from ...dependencies import ApiToken
from ...runtime import config
from ...schemas.auth import AuthenticatedUser, Credentials, CurrentUser, OAuthToken
from ...services.auth import (
    FixedWindowLimiter,
    authenticated_response,
    make_session_token,
    require_session,
    user_role,
    verify_credentials,
)


router = APIRouter()
login_ip_limiter = FixedWindowLimiter(attempts=30, seconds=60)
login_account_limiter = FixedWindowLimiter(attempts=10, seconds=60)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def authenticated_credentials(
    request: Request,
    username: str,
    password: str,
) -> dict:
    client = client_key(request)
    account_key = f"{client}:{username}"
    if not login_ip_limiter.allow(client) or not login_account_limiter.allow(account_key):
        raise HTTPException(status_code=429, detail="登录请求过于频繁")
    try:
        user = await verify_credentials(username, password)
    except RegistryError as error:
        raise HTTPException(status_code=503, detail="用户服务不可用") from error
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post(
    "/sessions",
    response_model=AuthenticatedUser,
    summary="登录",
)
async def login(request: Request, credentials: Credentials) -> Response:
    user = await authenticated_credentials(
        request, credentials.username, credentials.password
    )
    return authenticated_response(request, user)


@router.post(
    "/token",
    response_model=OAuthToken,
    summary="使用账号密码获取 Bearer Token",
)
async def oauth_token(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
) -> OAuthToken:
    user = await authenticated_credentials(request, form.username, form.password)
    return OAuthToken(
        access_token=make_session_token(request, user),
        expires_in=config.session_ttl_seconds,
    )


@router.delete(
    "/sessions/current",
    status_code=204,
    summary="退出登录",
)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(config.cookie_name, path="/")
    return response


@router.get(
    "/sessions/current",
    response_model=CurrentUser,
    summary="查询当前账号",
)
async def me(request: Request, token: ApiToken) -> CurrentUser:
    user = require_session(request, token)
    return CurrentUser(username=str(user["username"]), role=user_role(user))

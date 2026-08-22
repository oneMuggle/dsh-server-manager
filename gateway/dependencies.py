from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyCookie, OAuth2PasswordBearer

from .runtime import config


session_cookie = APIKeyCookie(name=config.cookie_name, auto_error=False)
bearer_token = OAuth2PasswordBearer(
    tokenUrl="/api/gateway/v1/auth/token",
    auto_error=False,
)
SessionCookie = Annotated[str | None, Security(session_cookie)]
BearerToken = Annotated[str | None, Security(bearer_token)]


def api_token(
    request: Request,
    cookie: SessionCookie,
    bearer: BearerToken,
) -> str | None:
    if "authorization" in request.headers:
        if bearer is None:
            raise HTTPException(
                status_code=401,
                detail="无效的 Authorization 请求头",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return bearer
    return cookie


ApiToken = Annotated[str | None, Depends(api_token)]

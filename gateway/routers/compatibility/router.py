from __future__ import annotations

import asyncio
import hmac
import json
import re
from typing import Annotated, Any

import aiohttp
from fastapi import APIRouter, Request, Response, Security
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common.security import hash_password
from common.storage.users import RegistryError, get_user

from ...runtime import config
from ...schemas.compatibility import (
    CompatibleLoginTicket,
    CompatibleProviderRegistration,
    CompatibleRegistration,
)
from ...services.auth import make_session_token, set_session
from ...services.compatibility import (
    CompatibleProvider,
    LoginTicketStore,
    safe_return_to,
)
from ...services.providers import (
    ProviderOperationError,
    create_provider,
    fetch_provider_models,
)
from ...services.supervisor import SupervisorError, create_user


router = APIRouter(tags=["旧服务兼容接口"])

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PROVIDER_NAME_PATTERN = USERNAME_PATTERN
BASE_URL_PATTERN = re.compile(r"^https?://[^\s]+$")
PROVIDER_APIS = {
    "openai-completions",
    "openai-responses",
    "anthropic-messages",
}
MAX_BODY_BYTES = 10_000

REGISTER_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string", "example": "alice"},
                        "password": {
                            "type": "string",
                            "format": "password",
                            "minLength": 8,
                        },
                    },
                }
            }
        },
    }
}
PROVIDER_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["provider", "apiKey"],
                    "properties": {
                        "provider": {
                            "type": "object",
                            "required": ["name", "baseURL"],
                            "properties": {
                                "name": {"type": "string"},
                                "baseURL": {"type": "string", "format": "uri"},
                                "api": {"type": "string"},
                                "model": {"type": "string"},
                                "models": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                        "apiKey": {"type": "string", "format": "password"},
                    },
                }
            }
        },
    }
}
LOGIN_TICKET_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["username"],
                    "properties": {
                        "username": {"type": "string"},
                        "returnTo": {"type": "string", "default": "/"},
                    },
                }
            }
        },
    }
}

register_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="DSHRegistrationKey",
    description="DSH_REGISTER_API_KEY",
)
login_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="DSHLoginKey",
    description="DSH_LOGIN_API_KEY",
)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(register_bearer),
]
LoginBearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(login_bearer),
]


def _error(status_code: int, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": message, **extra},
        status_code=status_code,
    )


def _authorized(
    credentials: HTTPAuthorizationCredentials | None,
    expected: str,
) -> bool:
    return bool(
        expected
        and credentials is not None
        and credentials.scheme.lower() == "bearer"
        and hmac.compare_digest(credentials.credentials, expected)
    )


async def _json_body(request: Request) -> dict[str, Any] | None:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _stored_user(username: str) -> dict | None:
    try:
        return get_user(config.user_database_path, username)
    except RegistryError:
        return None


@router.post(
    "/api/register",
    response_model=CompatibleRegistration,
    response_model_by_alias=True,
    summary="兼容接口：添加用户",
    responses={400: {}, 401: {}, 409: {}},
    openapi_extra=REGISTER_REQUEST_BODY,
)
async def compatible_register(
    request: Request,
    credentials: BearerCredentials,
) -> CompatibleRegistration | JSONResponse:
    if not _authorized(credentials, config.register_api_key):
        return _error(401, "unauthorized")
    body = await _json_body(request)
    if body is None:
        return _error(400, "invalid JSON body")
    if body.get("provider") is not None:
        return _error(
            400,
            "provider is not accepted here; use POST /api/users/<username>/provider",
        )
    username_value = body.get("username")
    username = username_value.strip() if isinstance(username_value, str) else ""
    password_value = body.get("password")
    password = password_value if isinstance(password_value, str) else ""
    if not USERNAME_PATTERN.fullmatch(username):
        return _error(400, "invalid username")
    if len(password) < 8:
        return _error(400, "password must be at least 8 characters")
    password_record = await asyncio.to_thread(hash_password, password)
    try:
        created = await create_user(
            config.control_socket_path,
            username,
            password_record,
        )
    except SupervisorError as error:
        return _error(409, str(error))
    return CompatibleRegistration(user=username, port=int(created["port"]))


def _explicit_models(provider: dict[str, Any]) -> list[str] | None:
    values = provider.get("models")
    if isinstance(values, list) and values:
        models = [
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip() and len(value.strip()) <= 128
        ]
        return models
    value = provider.get("model")
    if isinstance(value, str) and value.strip():
        model = value.strip()
        return [model] if len(model) <= 128 else []
    return None


@router.post(
    "/api/users/{username}/provider",
    response_model=CompatibleProviderRegistration,
    response_model_by_alias=True,
    summary="兼容接口：添加或替换用户的自定义提供商",
    responses={400: {}, 401: {}, 404: {}, 409: {}, 502: {}},
    openapi_extra=PROVIDER_REQUEST_BODY,
)
async def compatible_provider(
    username: str,
    request: Request,
    credentials: BearerCredentials,
) -> CompatibleProviderRegistration | JSONResponse:
    if not _authorized(credentials, config.register_api_key):
        return _error(401, "unauthorized")
    if not USERNAME_PATTERN.fullmatch(username):
        return _error(404, "user not found")
    body = await _json_body(request)
    if body is None:
        return _error(400, "invalid JSON body")
    provider_value = body.get("provider")
    if not isinstance(provider_value, dict):
        return _error(400, "provider is required")
    name_value = provider_value.get("name")
    name = name_value.strip() if isinstance(name_value, str) else ""
    if not PROVIDER_NAME_PATTERN.fullmatch(name):
        return _error(
            400,
            "invalid provider name (letters, digits, underscore, hyphen)",
        )
    base_url_value = provider_value.get("baseURL")
    base_url = base_url_value.strip() if isinstance(base_url_value, str) else ""
    if not BASE_URL_PATTERN.fullmatch(base_url):
        return _error(400, "invalid provider baseURL (must be an http(s) URL)")
    api_value = provider_value.get("api")
    api = "openai-completions" if api_value in (None, "") else str(api_value)
    if api not in PROVIDER_APIS:
        return _error(
            400,
            "invalid provider api (openai-completions | openai-responses | anthropic-messages)",
        )
    key_value = body.get("apiKey", body.get("key", ""))
    api_key = key_value.strip() if isinstance(key_value, str) else ""
    if not api_key:
        return _error(400, "apiKey is required")
    models = _explicit_models(provider_value)
    if models == []:
        error = (
            "invalid provider models"
            if isinstance(provider_value.get("models"), list)
            else "invalid provider model"
        )
        return _error(400, error)
    if models is None:
        try:
            models = await fetch_provider_models(
                request.app.state.http,
                base_url,
                api_key,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as error:
            return _error(
                502,
                f"failed to fetch models from provider: {error}; "
                "pass provider.model to set it explicitly",
            )
    target = _stored_user(username)
    if target is None or target.get("disabled") is True:
        return _error(404, "user not found")
    if target.get("role") == "admin":
        return _error(400, "admin has no instance")
    provider = CompatibleProvider(
        provider_id=name,
        base_url=base_url,
        api=api,
        api_key=api_key,
        models=tuple(models),
    )
    try:
        await create_provider(
            config.user_database_path,
            request.app.state.http,
            username,
            provider,
            replace=True,
        )
    except ProviderOperationError as error:
        if error.code == "not_found":
            return _error(404, "user not found")
        if error.code == "credential_partial":
            profile = provider.compatibility_profile()
            return _error(
                502,
                f"provider configured but API key write failed: {error}",
                provider=profile,
                keyWrite={"ok": False, "detail": str(error)},
            )
        return _error(409, str(error))
    profile = provider.compatibility_profile()
    return CompatibleProviderRegistration(
        user=username,
        provider=profile,
        ref=provider.credential_ref(),
        models=models,
    )


@router.post(
    "/api/login-ticket",
    response_model=CompatibleLoginTicket,
    response_model_by_alias=True,
    summary="兼容接口：返回一次性登录链接",
    responses={400: {}, 401: {}, 404: {}, 503: {}},
    openapi_extra=LOGIN_TICKET_REQUEST_BODY,
)
async def compatible_login_ticket(
    request: Request,
    response: Response,
    credentials: LoginBearerCredentials,
) -> CompatibleLoginTicket | JSONResponse:
    if not _authorized(credentials, config.login_api_key):
        return _error(401, "unauthorized")
    body = await _json_body(request)
    if body is None:
        return _error(400, "invalid JSON body")
    username_value = body.get("username")
    username = username_value.strip() if isinstance(username_value, str) else ""
    if not USERNAME_PATTERN.fullmatch(username):
        return _error(400, "invalid username")
    target = _stored_user(username)
    if target is None or target.get("disabled") is True:
        return _error(404, "user not found")
    return_to = safe_return_to(body.get("returnTo"))
    if return_to is None:
        return _error(400, "returnTo must be a same-origin absolute path")
    store: LoginTicketStore = request.app.state.login_tickets
    ticket = store.issue(username, return_to)
    if ticket is None:
        return _error(503, "too many outstanding login tickets")
    response.headers["Cache-Control"] = "no-store"
    return CompatibleLoginTicket(
        loginUrl=f"/auth/external?ticket={ticket}",
        expiresIn=config.login_ticket_ttl_seconds,
    )


@router.get("/auth/external", include_in_schema=False)
async def consume_external_login(request: Request, ticket: str = "") -> RedirectResponse:
    store: LoginTicketStore = request.app.state.login_tickets
    login = store.consume(ticket)
    target = _stored_user(login.username) if login is not None else None
    if login is None or target is None or target.get("disabled") is True:
        return RedirectResponse("/login", status_code=302)
    response = RedirectResponse(login.return_to, status_code=302)
    response.headers["Referrer-Policy"] = "no-referrer"
    set_session(response, make_session_token(request, target))
    return response

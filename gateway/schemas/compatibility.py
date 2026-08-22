from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CompatibleRegistration(BaseModel):
    ok: Literal[True] = True
    user: str
    port: int


class CompatibleProviderProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    base_url: str = Field(alias="baseURL")
    model: str
    models: list[str]
    api: str
    api_key_env: str = Field(alias="apiKeyEnv")


class CompatibleProviderRegistration(BaseModel):
    ok: Literal[True] = True
    user: str
    provider: CompatibleProviderProfile
    ref: str
    models: list[str]


class CompatibleLoginTicket(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: Literal[True] = True
    login_url: str = Field(alias="loginUrl")
    expires_in: int = Field(alias="expiresIn")

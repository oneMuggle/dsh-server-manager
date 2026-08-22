from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=3, max_length=24)]
    password: Annotated[str, Field(min_length=8, max_length=256)]


class CurrentUser(BaseModel):
    username: str
    role: Literal["admin", "user"]


class AuthenticatedUser(CurrentUser):
    access_token: str = Field(alias="accessToken")
    token_type: Literal["Bearer"] = Field(alias="tokenType")
    expires_in: int = Field(alias="expiresIn")


class OAuthToken(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int

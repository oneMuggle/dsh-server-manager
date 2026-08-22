from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


USERNAME_PATTERN = r"^[a-z][a-z0-9_-]{2,23}$"


class CreateManagedUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=24, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=256)


class UpdateManagedUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, min_length=8, max_length=256)
    disabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UpdateManagedUser":
        if self.password is None and self.disabled is None:
            raise ValueError("至少需要提供密码或停用状态")
        return self


class RegisteredUser(BaseModel):
    username: str
    role: Literal["admin", "user"]
    disabled: bool
    created_at: str


class RegisteredUsers(BaseModel):
    users: list[RegisteredUser]

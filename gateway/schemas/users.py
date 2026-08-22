from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RegisteredUser(BaseModel):
    username: str
    role: Literal["admin", "user"]
    disabled: bool
    created_at: str


class RegisteredUsers(BaseModel):
    users: list[RegisteredUser]

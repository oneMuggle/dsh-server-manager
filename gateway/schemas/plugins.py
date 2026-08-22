from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OnlinePluginImport(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    package_name: str = Field(alias="packageName", min_length=1, max_length=214)
    version: str = Field(default="latest", min_length=1, max_length=64)


class PluginScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["all", "users"]
    usernames: list[str] = Field(default_factory=list, max_length=10000)
    includeDisabledUsers: bool = False


class PluginAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: Literal["install", "uninstall", "enable", "disable"]
    plugin_id: int = Field(alias="pluginId", gt=0)
    version_id: int | None = Field(default=None, alias="versionId", gt=0)
    scope: PluginScope
    enabled_after_install: bool = Field(default=True, alias="enabledAfterInstall")
    apply_to_future_users: bool = Field(default=False, alias="applyToFutureUsers")


class BuiltinPluginAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action: Literal["enable", "disable"]
    entry_id: str = Field(alias="entryId", min_length=1, max_length=128)
    scope: PluginScope
    apply_to_future_users: bool = Field(default=False, alias="applyToFutureUsers")

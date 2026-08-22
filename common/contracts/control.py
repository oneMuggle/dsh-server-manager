from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from common.errors import ControlRequestError


class _ControlRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CreateUserRequest(_ControlRequest):
    action: Literal["create_user"]
    username: str
    password: dict[str, Any]


class UpdateUserRequest(_ControlRequest):
    action: Literal["update_user"]
    username: str
    password: dict[str, Any] | None = None
    disabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UpdateUserRequest":
        if self.password is None and self.disabled is None:
            raise ValueError("update requires password or disabled")
        return self


class UserMetricsRequest(_ControlRequest):
    action: Literal["user_metrics"]


class InstallUploadRequest(_ControlRequest):
    action: Literal["install_upload"]
    username: str
    spool_name: str
    directory: str
    filename: str
    size: int


class ListUploadDirectoriesRequest(_ControlRequest):
    action: Literal["list_upload_directories"]
    username: str
    directory: str = "."


class ImportOnlinePluginRequest(_ControlRequest):
    action: Literal["plugin_online_import"]
    package_name: str
    requested_version: str = "latest"
    created_by: str = "admin"


class DiscoverPluginVersionsRequest(_ControlRequest):
    action: Literal["plugin_discover_versions"]
    package_name: str


class ImportOfflinePluginRequest(_ControlRequest):
    action: Literal["plugin_offline_import"]
    spool_name: str
    created_by: str = "admin"


class AssignPluginRequest(_ControlRequest):
    action: Literal["plugin_assignment"]
    plugin_action: str
    plugin_id: int
    plugin_version_id: int | None = None
    scope: dict[str, Any]
    enabled_after_install: bool = True
    apply_to_future_users: bool = False
    created_by: str = "admin"


class AssignBuiltinPluginRequest(_ControlRequest):
    action: Literal["builtin_plugin_assignment"]
    plugin_action: Literal["enable", "disable"]
    entry_id: str
    scope: dict[str, Any]
    apply_to_future_users: bool = False
    created_by: str = "admin"


ControlRequest = Annotated[
    Union[
        CreateUserRequest,
        UpdateUserRequest,
        UserMetricsRequest,
        InstallUploadRequest,
        ListUploadDirectoriesRequest,
        ImportOnlinePluginRequest,
        DiscoverPluginVersionsRequest,
        ImportOfflinePluginRequest,
        AssignPluginRequest,
        AssignBuiltinPluginRequest,
    ],
    Field(discriminator="action"),
]

_CONTROL_REQUEST_ADAPTER = TypeAdapter(ControlRequest)


def parse_control_request(value: object) -> ControlRequest:
    try:
        return _CONTROL_REQUEST_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ControlRequestError("invalid request") from error


def success_response(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def error_response(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message}


__all__ = [
    "AssignPluginRequest",
    "AssignBuiltinPluginRequest",
    "ControlRequest",
    "ControlRequestError",
    "CreateUserRequest",
    "DiscoverPluginVersionsRequest",
    "ImportOfflinePluginRequest",
    "ImportOnlinePluginRequest",
    "InstallUploadRequest",
    "ListUploadDirectoriesRequest",
    "UpdateUserRequest",
    "UserMetricsRequest",
    "error_response",
    "parse_control_request",
    "success_response",
]

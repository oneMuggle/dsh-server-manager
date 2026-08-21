from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROVIDER_NAMESPACE = "llm-pi-ai"
PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
PRINTABLE_API_KEY_PATTERN = re.compile(r"^[\x21-\x7e]+$")
ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*=")

ProviderProtocol = Literal[
    "openai-completions",
    "openai-responses",
    "anthropic-messages",
]


class CustomProviderModel(BaseModel):
    """One model row exposed by Settings → Models → Custom provider."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, min_length=1, max_length=512)
    context_window: int | None = Field(default=None, alias="contextWindow", gt=0)
    max_tokens: int | None = Field(default=None, alias="maxTokens", gt=0)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("模型 ID 不能为空")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("模型名称不能为空")
        return normalized


class CreateCustomProvider(BaseModel):
    """Fields mirrored from Settings → Models → Custom provider."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    provider_id: str = Field(
        alias="providerId",
        min_length=1,
        max_length=128,
        pattern=PROVIDER_ID_PATTERN.pattern,
        description="提供方 ID：小写字母开头，可包含小写字母、数字和连字符",
    )
    display_name: str | None = Field(
        default=None,
        alias="displayName",
        max_length=256,
    )
    base_url: str = Field(alias="baseURL", min_length=1, max_length=4096)
    api: ProviderProtocol
    api_key: str | None = Field(
        default=None,
        alias="apiKey",
        max_length=16384,
        description="可选；只写入目标用户的 Harness 凭据存储，不进入设置文件",
    )
    models: list[CustomProviderModel] = Field(min_length=1, max_length=256)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("API 地址不能为空")
        return normalized

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("API Key 不能只包含空白字符")
        if ENV_ASSIGNMENT_PATTERN.match(normalized):
            raise ValueError("API Key 只填写值，不要填写 NAME=value")
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in "\"'`":
            raise ValueError("API Key 不要包含首尾引号")
        if not PRINTABLE_API_KEY_PATTERN.fullmatch(normalized):
            raise ValueError("API Key 只能包含不带空格的可打印 ASCII 字符")
        return normalized

    @model_validator(mode="after")
    def reject_duplicate_models(self) -> "CreateCustomProvider":
        model_ids = [model.id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("模型 ID 不能重复")
        return self

    def credential_ref(self) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", self.provider_id.upper()) + "_API_KEY"

    def provider_profile(self) -> dict:
        profile: dict = {
            "api": self.api,
            "baseURL": self.base_url,
            "models": [
                model.model_dump(by_alias=True, exclude_none=True)
                for model in self.models
            ],
        }
        if self.display_name is not None:
            profile["displayName"] = self.display_name
        if self.api_key is not None:
            profile["apiKeyEnv"] = self.credential_ref()
        return profile


class CustomProviderCreated(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str
    provider_id: str = Field(alias="providerId")
    credential_ref: str | None = Field(alias="credentialRef")
    credential_stored: bool = Field(alias="credentialStored")

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp


LOGIN_TICKET_MAX = 10_000


@dataclass(frozen=True, slots=True)
class CompatibleProvider:
    provider_id: str
    base_url: str
    api: str
    api_key: str
    models: tuple[str, ...]

    def credential_ref(self) -> str:
        normalized = "".join(
            character if character.isascii() and character.isalnum() else "_"
            for character in self.provider_id.upper()
        )
        return f"{normalized}_API_KEY"

    def provider_profile(self) -> dict:
        return {
            "apiKeyEnv": self.credential_ref(),
            "displayName": self.provider_id,
            "api": self.api,
            "baseURL": self.base_url,
            "models": [
                {
                    "id": model,
                    "name": model,
                    "contextWindow": 32768,
                    "maxTokens": 8192,
                }
                for model in self.models
            ],
        }

    def compatibility_profile(self) -> dict:
        return {
            "name": self.provider_id,
            "baseURL": self.base_url,
            "model": self.models[0],
            "models": list(self.models),
            "api": self.api,
            "apiKeyEnv": self.credential_ref(),
        }


async def fetch_provider_models(
    session: aiohttp.ClientSession,
    base_url: str,
    api_key: str,
) -> list[str]:
    url = f"{base_url.rstrip('/')}/models"
    timeout = aiohttp.ClientTimeout(total=15)
    async with session.get(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=timeout,
    ) as response:
        if response.status < 200 or response.status >= 300:
            reason = f" {response.reason}" if response.reason else ""
            raise RuntimeError(f"HTTP {response.status}{reason}")
        try:
            payload = await response.json()
        except (aiohttp.ContentTypeError, ValueError) as error:
            raise RuntimeError("provider returned invalid JSON") from error
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("provider response has no data array")
    models = [
        str(row["id"]).strip()
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and str(row["id"]).strip()
    ]
    if not models:
        raise RuntimeError("provider returned no models")
    return models[:100]


@dataclass(frozen=True, slots=True)
class ExternalLogin:
    username: str
    return_to: str


@dataclass(frozen=True, slots=True)
class _StoredLogin:
    username: str
    return_to: str
    expires_at: float


class LoginTicketStore:
    def __init__(self, ttl_seconds: int, maximum: int = LOGIN_TICKET_MAX) -> None:
        self.ttl_seconds = ttl_seconds
        self.maximum = maximum
        self._tickets: dict[str, _StoredLogin] = {}

    @staticmethod
    def _key(ticket: str) -> str:
        digest = hashlib.sha256(ticket.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def issue(self, username: str, return_to: str) -> str | None:
        now = time.monotonic()
        self._tickets = {
            key: login
            for key, login in self._tickets.items()
            if login.expires_at > now
        }
        if len(self._tickets) >= self.maximum:
            return None
        ticket = secrets.token_urlsafe(32)
        self._tickets[self._key(ticket)] = _StoredLogin(
            username=username,
            return_to=return_to,
            expires_at=now + self.ttl_seconds,
        )
        return ticket

    def consume(self, ticket: str) -> ExternalLogin | None:
        if not ticket or len(ticket) > 256:
            return None
        login = self._tickets.pop(self._key(ticket), None)
        if login is None or login.expires_at <= time.monotonic():
            return None
        return ExternalLogin(login.username, login.return_to)


def safe_return_to(value: object) -> str | None:
    if value is None or value == "":
        return "/"
    if not isinstance(value, str) or len(value) > 2048:
        return None
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    try:
        resolved = urlsplit(urljoin("http://dsh.invalid/", value))
    except ValueError:
        return None
    if resolved.scheme != "http" or resolved.netloc != "dsh.invalid":
        return None
    result = resolved.path or "/"
    if resolved.query:
        result += f"?{resolved.query}"
    if resolved.fragment:
        result += f"#{resolved.fragment}"
    return result

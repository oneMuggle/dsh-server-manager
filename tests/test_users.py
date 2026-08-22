from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Request
from pydantic import ValidationError

from gateway.application import app
from gateway.routers.user.accounts import (
    create_managed_user,
    delete_managed_user,
    managed_user,
    update_managed_user,
)
from gateway.schemas.users import CreateManagedUser, UpdateManagedUser


def _request(method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/v1/users/alice",
            "headers": [],
            "app": app,
        }
    )


class ManagedUserSchemaTests(unittest.TestCase):
    def test_create_enforces_the_public_registration_constraints(self) -> None:
        with self.assertRaises(ValidationError):
            CreateManagedUser(username="Alice", password="change-me-123")
        with self.assertRaises(ValidationError):
            CreateManagedUser(username="alice", password="short")

    def test_update_requires_at_least_one_supported_change(self) -> None:
        with self.assertRaises(ValidationError):
            UpdateManagedUser()
        with self.assertRaises(ValidationError):
            UpdateManagedUser(username="renamed")


class ManagedUserApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_returns_only_public_fields(self) -> None:
        stored = {
            "username": "alice",
            "role": "user",
            "disabled": False,
            "created_at": "2026-08-22T00:00:00+00:00",
            "password": {"hash": "secret"},
            "uid": 20001,
        }
        with (
            patch("gateway.routers.user.accounts.require_admin"),
            patch("gateway.routers.user.accounts.get_user", return_value=stored),
        ):
            result = await managed_user("alice", _request(), None)
        self.assertEqual(
            result.model_dump(),
            {
                "username": "alice",
                "role": "user",
                "disabled": False,
                "created_at": "2026-08-22T00:00:00+00:00",
            },
        )

    async def test_create_hashes_password_and_uses_supervisor(self) -> None:
        created = {
            "username": "alice",
            "role": "user",
            "disabled": False,
            "created_at": "2026-08-22T00:00:00+00:00",
        }
        supervisor = AsyncMock(return_value=created)
        with (
            patch("gateway.routers.user.accounts.require_admin"),
            patch("gateway.routers.user.accounts.create_user", supervisor),
        ):
            result = await create_managed_user(
                _request("POST"),
                CreateManagedUser(username="alice", password="change-me-123"),
                None,
            )
        self.assertEqual(result.username, "alice")
        password_record = supervisor.await_args.args[2]
        self.assertEqual(password_record["algorithm"], "scrypt")
        self.assertNotIn("change-me-123", str(password_record))

    async def test_update_sends_only_supported_changes(self) -> None:
        updated = {
            "username": "alice",
            "role": "user",
            "disabled": True,
            "created_at": "2026-08-22T00:00:00+00:00",
        }
        supervisor = AsyncMock(return_value=updated)
        with (
            patch("gateway.routers.user.accounts.require_admin"),
            patch("gateway.routers.user.accounts.update_user", supervisor),
        ):
            result = await update_managed_user(
                "alice",
                _request("PATCH"),
                UpdateManagedUser(disabled=True),
                None,
            )
        self.assertTrue(result.disabled)
        self.assertEqual(supervisor.await_args.kwargs["disabled"], True)
        self.assertIsNone(supervisor.await_args.kwargs["password_record"])

    async def test_supervisor_errors_map_to_public_statuses(self) -> None:
        from gateway.services.supervisor import SupervisorError

        for code, status in (
            ("not_found", 404),
            ("conflict", 409),
            ("bad_request", 422),
            ("unavailable", 503),
        ):
            with (
                self.subTest(code=code),
                patch("gateway.routers.user.accounts.require_admin"),
                patch(
                    "gateway.routers.user.accounts.update_user",
                    AsyncMock(side_effect=SupervisorError("failed", code)),
                ),
                self.assertRaises(HTTPException) as caught,
            ):
                await update_managed_user(
                    "alice",
                    _request("PATCH"),
                    UpdateManagedUser(disabled=True),
                    None,
                )
            self.assertEqual(caught.exception.status_code, status)

    async def test_delete_is_an_authenticated_no_op(self) -> None:
        with (
            patch("gateway.routers.user.accounts.require_admin") as authorize,
            patch("gateway.routers.user.accounts.get_user") as lookup,
            self.assertRaises(HTTPException) as caught,
        ):
            await delete_managed_user("alice", _request("DELETE"), None)
        authorize.assert_called_once()
        lookup.assert_not_called()
        self.assertEqual(caught.exception.status_code, 501)
        self.assertEqual(caught.exception.detail, "用户删除暂未实现")

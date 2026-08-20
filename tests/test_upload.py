from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from supervisor.main import (
    _admin_password_from_environment,
    _normalize_user_roles,
    _upload_directory_parts,
    _valid_password_record,
    _validate_upload_filename,
)


class UploadPathTests(unittest.TestCase):
    def test_relative_directory_is_split(self) -> None:
        self.assertEqual(_upload_directory_parts("reports/2026"), ("reports", "2026"))
        self.assertEqual(_upload_directory_parts("."), ())

    def test_directory_escape_and_hidden_paths_are_rejected(self) -> None:
        for value in ("/tmp", "../state", "reports/../../state", ".hidden", "a/.hidden"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _upload_directory_parts(value)

    def test_filename_must_be_one_visible_path_segment(self) -> None:
        self.assertEqual(_validate_upload_filename("报告.txt"), "报告.txt")
        for value in ("", ".env", "../secret", "nested/file.txt", "a\\b.txt"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate_upload_filename(value)


class SupervisorRegressionTests(unittest.TestCase):
    def test_valid_scrypt_record_is_accepted(self) -> None:
        self.assertTrue(
            _valid_password_record(
                {
                    "algorithm": "scrypt",
                    "salt": "a" * 16,
                    "hash": "b" * 32,
                    "n": 1 << 14,
                    "r": 8,
                    "p": 1,
                    "dklen": 64,
                }
            )
        )

    def test_only_fixed_admin_username_has_admin_role(self) -> None:
        registry = {
            "version": 1,
            "users": {
                "first": {"role": "admin"},
                "admin": {"role": "user"},
            },
        }
        self.assertTrue(_normalize_user_roles(registry))
        self.assertEqual(registry["users"]["admin"]["role"], "admin")
        self.assertEqual(registry["users"]["first"]["role"], "user")
        self.assertFalse(_normalize_user_roles(registry))

    def test_bootstrap_admin_password_is_required_and_validated(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                _admin_password_from_environment()
        with patch.dict(os.environ, {"DSH_ADMIN_PASSWORD": "short"}, clear=True):
            with self.assertRaises(RuntimeError):
                _admin_password_from_environment()
        with patch.dict(
            os.environ,
            {"DSH_ADMIN_PASSWORD": "strong-test-password"},
            clear=True,
        ):
            self.assertEqual(
                _admin_password_from_environment(),
                "strong-test-password",
            )


if __name__ == "__main__":
    unittest.main()

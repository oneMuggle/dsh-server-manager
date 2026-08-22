from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from common.storage.users import (
    RegistryError,
    add_user,
    get_user,
    initialize_registry,
    list_users,
    load_registry,
    replace_registry,
    update_user,
)


def _user(username: str, uid: int, port: int) -> dict:
    return {
        "username": username,
        "os_user": f"dsh-{username}",
        "uid": uid,
        "port": port,
        "role": "admin" if username == "admin" else "user",
        "password": {
            "algorithm": "scrypt",
            "salt": "test-salt",
            "hash": "test-hash",
            "n": 1 << 14,
            "r": 8,
            "p": 1,
            "dklen": 64,
        },
        "password_version": 1,
        "created_at": f"2026-08-21T00:00:0{uid % 10}+00:00",
        "disabled": False,
    }


class RegistryDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "users.db"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_adds_and_queries_user_by_primary_key(self) -> None:
        initialize_registry(self.database)
        alice = _user("alice", 20001, 3102)
        add_user(self.database, alice)

        self.assertEqual(get_user(self.database, "alice"), alice)
        self.assertIsNone(get_user(self.database, "missing"))

        with sqlite3.connect(self.database) as connection:
            query_plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM users WHERE username = ?",
                ("alice",),
            ).fetchall()
        self.assertIn("INDEX", " ".join(str(column) for row in query_plan for column in row))

    def test_initialization_accepts_only_the_sqlite_path(self) -> None:
        unsupported_source = self.root / "old-user-data"
        unsupported_source.write_text(
            '{"version":1,"users":{}}',
            encoding="utf-8",
        )

        with self.assertRaises(TypeError):
            initialize_registry(self.database, unsupported_source)

    def test_replaces_registry_atomically_and_enforces_uniqueness(self) -> None:
        initialize_registry(self.database)
        alice = _user("alice", 20001, 3102)
        bob = _user("bob", 20002, 3103)
        replace_registry(
            self.database,
            {"version": 1, "users": {"alice": alice, "bob": bob}},
        )

        self.assertEqual(set(load_registry(self.database)["users"]), {"alice", "bob"})
        self.assertEqual(
            [user["username"] for user in list_users(self.database)],
            ["alice", "bob"],
        )
        with self.assertRaises(RegistryError):
            add_user(self.database, _user("alice", 20003, 3104))

    def test_updates_one_user_without_rewriting_the_registry(self) -> None:
        initialize_registry(self.database)
        alice = _user("alice", 20001, 3102)
        bob = _user("bob", 20002, 3103)
        replace_registry(
            self.database,
            {"version": 1, "users": {"alice": alice, "bob": bob}},
        )
        updated = {**alice, "disabled": True, "password_version": 2}

        self.assertEqual(update_user(self.database, updated), updated)
        self.assertEqual(get_user(self.database, "bob"), bob)
        with self.assertRaises(LookupError):
            update_user(self.database, _user("missing", 20003, 3104))


if __name__ == "__main__":
    unittest.main()

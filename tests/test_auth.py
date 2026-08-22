from __future__ import annotations

import unittest

from common.security import hash_password, verify_password
from gateway.services.auth import (
    FixedWindowLimiter,
    make_session,
    parse_session,
)


class PasswordTests(unittest.TestCase):
    def test_scrypt_password_round_trip(self) -> None:
        record = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", record))
        self.assertFalse(verify_password("wrong password", record))


class SessionTests(unittest.TestCase):
    def test_signed_session_round_trip_and_tamper_rejection(self) -> None:
        secret = b"test-secret"
        token = make_session("alice", 3, secret, 60)
        self.assertEqual(
            parse_session(token, secret),
            {"exp": parse_session(token, secret)["exp"], "pv": 3, "sub": "alice"},
        )
        encoded, signature = token.split(".")
        replacement = "A" if signature[0] != "A" else "B"
        self.assertIsNone(parse_session(f"{encoded}.{replacement}{signature[1:]}", secret))


class LimiterTests(unittest.TestCase):
    def test_fixed_window_enforces_attempt_limit(self) -> None:
        limiter = FixedWindowLimiter(attempts=2, seconds=60)
        self.assertTrue(limiter.allow("client"))
        self.assertTrue(limiter.allow("client"))
        self.assertFalse(limiter.allow("client"))


if __name__ == "__main__":
    unittest.main()

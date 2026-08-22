from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any


SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return {
        "algorithm": "scrypt",
        "salt": _b64encode(salt),
        "hash": _b64encode(digest),
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "dklen": SCRYPT_DKLEN,
    }


def verify_password(password: str, record: dict[str, Any]) -> bool:
    try:
        if record.get("algorithm") != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(str(record["salt"])),
            n=int(record["n"]),
            r=int(record["r"]),
            p=int(record["p"]),
            dklen=int(record.get("dklen", SCRYPT_DKLEN)),
        )
        expected = _b64decode(str(record["hash"]))
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)

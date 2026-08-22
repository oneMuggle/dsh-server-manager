from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SpoolResult:
    name: str
    path: Path
    size: int


class SpoolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def receive_spool(
    stream: AsyncIterator[bytes],
    root: Path,
    *,
    suffix: str,
    max_bytes: int,
    content_length: str | None,
    allow_empty: bool,
) -> SpoolResult:
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise SpoolError("invalid_length", "invalid content length") from error
        if declared_size < 0:
            raise SpoolError("invalid_length", "invalid content length")
        if declared_size == 0 and not allow_empty:
            raise SpoolError("declared_empty", "empty content")
        if declared_size > max_bytes:
            raise SpoolError("declared_too_large", "declared content is too large")

    name = f"{secrets.token_hex(16)}{suffix}"
    path = root / name
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    size = 0
    try:
        async for chunk in stream:
            size += len(chunk)
            if size > max_bytes:
                raise SpoolError("too_large", "streamed content is too large")
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise SpoolError("write_failed", "failed to write spool file")
                remaining = remaining[written:]
        if size == 0 and not allow_empty:
            raise SpoolError("empty", "empty content")
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return SpoolResult(name=name, path=path, size=size)

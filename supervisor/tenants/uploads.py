from __future__ import annotations

import errno
import os
import pwd
import re
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import Any


UPLOAD_SPOOL_PATTERN = re.compile(r"^[0-9a-f]{32}\.upload$")


def upload_directory_parts(value: str) -> tuple[str, ...]:
    if value in {"", "."}:
        return ()
    if len(value) > 512 or "\\" in value:
        raise ValueError("无效的目标目录")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("目标目录必须相对于 workspace")
    parts = path.parts
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise ValueError("目标目录不能包含隐藏目录、. 或 ..")
    if any(len(part.encode("utf-8")) > 255 for part in parts):
        raise ValueError("目标目录名称过长")
    return parts


def validate_upload_filename(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or value.startswith(".")
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or len(value.encode("utf-8")) > 255
    ):
        raise ValueError("无效的文件名")
    return value


def _open_owned_directory(
    parent_fd: int,
    name: str,
    uid: int,
    gid: int,
    create: bool = True,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise ValueError("目标目录不存在")
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.chown(name, uid, gid, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("目标目录不是安全的普通目录") from error
        raise
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid:
        os.close(descriptor)
        raise ValueError("目标目录不属于当前用户")
    return descriptor


def list_upload_directories(
    user: dict[str, Any],
    directory: str,
    *,
    users_root: Path,
) -> dict[str, Any]:
    parts = upload_directory_parts(directory)
    uid = int(user["uid"])
    gid = pwd.getpwnam(str(user["os_user"])).pw_gid
    tenant_root = users_root / str(user["username"])
    workspace_fd = -1
    target_fd = -1
    root_fd = os.open(
        tenant_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        home_fd = _open_owned_directory(root_fd, "home", uid, gid, create=False)
    finally:
        os.close(root_fd)
    try:
        workspace_fd = _open_owned_directory(
            home_fd,
            "workspace",
            uid,
            gid,
            create=False,
        )
    finally:
        os.close(home_fd)

    try:
        directory_fd = workspace_fd
        target_fd = directory_fd
        for part in parts:
            next_fd = _open_owned_directory(
                directory_fd,
                part,
                uid,
                gid,
                create=False,
            )
            if directory_fd != workspace_fd:
                os.close(directory_fd)
            directory_fd = next_fd
            target_fd = directory_fd

        names: list[str] = []
        with os.scandir(target_fd) as entries:
            for entry in entries:
                if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if metadata.st_uid == uid:
                    names.append(entry.name)
        names.sort(key=str.casefold)
        truncated = len(names) > 2000
        if truncated:
            names = names[:2000]
    finally:
        if target_fd >= 0 and target_fd != workspace_fd:
            os.close(target_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)

    return {
        "directory": "." if not parts else "/".join(parts),
        "directories": names,
        "truncated": truncated,
    }


def install_uploaded_file(
    user: dict[str, Any],
    spool_name: str,
    directory: str,
    filename: str,
    expected_size: int,
    *,
    users_root: Path,
    upload_root: Path,
    gateway_user: str,
    max_upload_bytes: int,
) -> dict[str, Any]:
    if not UPLOAD_SPOOL_PATTERN.fullmatch(spool_name):
        raise ValueError("无效的上传临时文件")
    if expected_size < 0 or expected_size > max_upload_bytes:
        raise ValueError("上传文件大小无效")
    parts = upload_directory_parts(directory)
    filename = validate_upload_filename(filename)
    uid = int(user["uid"])
    gid = pwd.getpwnam(str(user["os_user"])).pw_gid
    gateway_uid = pwd.getpwnam(gateway_user).pw_uid

    source_path = upload_root / spool_name
    source_fd = os.open(
        source_path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    workspace_fd = -1
    target_fd = -1
    temporary_name = f".upload-{secrets.token_hex(12)}.tmp"
    try:
        source_metadata = os.fstat(source_fd)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or source_metadata.st_uid != gateway_uid
            or source_metadata.st_size != expected_size
        ):
            raise ValueError("上传临时文件校验失败")

        tenant_root = users_root / str(user["username"])
        root_fd = os.open(
            tenant_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            home_fd = _open_owned_directory(root_fd, "home", uid, gid)
        finally:
            os.close(root_fd)
        try:
            workspace_fd = _open_owned_directory(home_fd, "workspace", uid, gid)
        finally:
            os.close(home_fd)

        directory_fd = workspace_fd
        target_fd = directory_fd
        for part in parts:
            next_fd = _open_owned_directory(directory_fd, part, uid, gid)
            if directory_fd != workspace_fd:
                os.close(directory_fd)
            directory_fd = next_fd
            target_fd = directory_fd

        destination_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=target_fd,
        )
        try:
            while True:
                chunk = os.read(source_fd, 64 * 1024)
                if not chunk:
                    break
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(destination_fd, remaining)
                    if written == 0:
                        raise OSError("failed to write uploaded file")
                    remaining = remaining[written:]
            os.fchmod(destination_fd, 0o600)
            os.fchown(destination_fd, uid, gid)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=target_fd,
            dst_dir_fd=target_fd,
        )
        os.fsync(target_fd)
    except BaseException:
        if target_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=target_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if target_fd >= 0 and target_fd != workspace_fd:
            os.close(target_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)
        os.close(source_fd)
        source_path.unlink(missing_ok=True)

    normalized_directory = "." if not parts else "/".join(parts)
    return {
        "directory": normalized_directory,
        "filename": filename,
        "size": expected_size,
    }

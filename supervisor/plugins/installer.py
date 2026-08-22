from __future__ import annotations


MAX_PROCESS_OUTPUT = 32 * 1024


def bounded_output(
    stdout: bytes,
    stderr: bytes,
    limit: int = MAX_PROCESS_OUTPUT,
) -> str:
    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
    if len(combined) > limit:
        combined = combined[-limit:]
        prefix = "…输出过长，仅保留末尾内容…\n"
    else:
        prefix = ""
    return prefix + combined.decode("utf-8", errors="replace").strip()

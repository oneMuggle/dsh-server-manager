from __future__ import annotations

import asyncio
import os
import signal

from .config import SupervisorConfig


def gateway_command(config: SupervisorConfig) -> tuple[str, ...]:
    return (
        "runuser",
        "-u",
        config.gateway_user,
        "--",
        "env",
        "-i",
        f"PYTHONPATH={config.install_root}",
        f"DSH_STATE_ROOT={config.state_root}",
        f"DSH_CONTROL_SOCKET={config.control_socket}",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "/opt/venv/bin/python",
        "-m",
        "uvicorn",
        "gateway.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(config.gateway_port),
        "--workers",
        "1",
        "--no-access-log",
    )


async def start_gateway(config: SupervisorConfig) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *gateway_command(config),
        cwd=str(config.install_root),
        start_new_session=True,
    )


async def stop_gateway(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=8)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()

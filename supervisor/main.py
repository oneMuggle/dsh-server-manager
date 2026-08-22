from __future__ import annotations

import asyncio
import logging
import os
import signal

from .application import FirewallError, Supervisor


logging.basicConfig(
    level=os.environ.get("DSM_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dsm.supervisor")


async def main() -> int:
    if os.geteuid() != 0:
        LOGGER.error("supervisor must run as root")
        return 1
    supervisor = Supervisor()
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stopped.set)
    try:
        await supervisor.start()
        assert supervisor.gateway is not None
        gateway_exit = asyncio.create_task(supervisor.gateway.wait())
        stop_wait = asyncio.create_task(stopped.wait())
        fatal_wait = asyncio.create_task(supervisor.fatal.wait())
        done, pending = await asyncio.wait(
            {gateway_exit, stop_wait, fatal_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if gateway_exit in done and not stopped.is_set():
            LOGGER.error(
                "gateway exited unexpectedly with status %s",
                gateway_exit.result(),
            )
            return int(gateway_exit.result() or 1)
        if fatal_wait in done:
            return 1
        return 0
    except FirewallError:
        LOGGER.exception("tenant network isolation is unavailable")
        return 1
    except RuntimeError as error:
        LOGGER.error("startup failed: %s", error)
        return 1
    finally:
        await supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

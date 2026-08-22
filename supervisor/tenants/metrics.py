from __future__ import annotations

from pathlib import Path


def resident_memory_by_uid(
    uids: set[int],
    proc_root: Path = Path("/proc"),
) -> dict[int, int]:
    """Return the summed resident memory of every process owned by each UID."""
    totals = {uid: 0 for uid in uids}
    if not totals:
        return totals

    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return totals

    for process in processes:
        if not process.name.isdecimal():
            continue
        try:
            status = (process / "status").read_text(errors="replace")
        except OSError:
            continue

        uid: int | None = None
        resident_kib = 0
        try:
            for line in status.splitlines():
                if line.startswith("Uid:"):
                    uid = int(line.split()[1])
                elif line.startswith("VmRSS:"):
                    resident_kib = int(line.split()[1])
        except (IndexError, ValueError):
            continue

        if uid in totals:
            totals[uid] += resident_kib * 1024

    return totals

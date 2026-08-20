from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass


class FirewallError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TenantNetwork:
    uid: int
    port: int


def _run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["iptables", "-w", "5", *arguments],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def apply_firewall(tenants: list[TenantNetwork], gateway_port: int = 8080) -> None:
    try:
        if _run("-N", "DSH_TENANTS", check=False).returncode != 0:
            _run("-F", "DSH_TENANTS")
        if _run("-C", "OUTPUT", "-p", "tcp", "-j", "DSH_TENANTS", check=False).returncode != 0:
            _run("-I", "OUTPUT", "1", "-p", "tcp", "-j", "DSH_TENANTS")

        for tenant in tenants:
            _run(
                "-A", "DSH_TENANTS",
                "-p", "tcp",
                "-m", "owner", "--uid-owner", str(tenant.uid),
                "-d", "127.0.0.0/8",
                "--dport", str(tenant.port),
                "-j", "ACCEPT",
            )
            for other in tenants:
                if other.port == tenant.port:
                    continue
                _run(
                    "-A", "DSH_TENANTS",
                    "-p", "tcp",
                    "-m", "owner", "--uid-owner", str(tenant.uid),
                    "-d", "127.0.0.0/8",
                    "--dport", str(other.port),
                    "-j", "REJECT",
                )
            gateway_destinations = {"127.0.0.0/8"}
            try:
                gateway_destinations.add(f"{socket.gethostbyname(socket.gethostname())}/32")
            except OSError:
                pass
            for destination in gateway_destinations:
                _run(
                    "-A", "DSH_TENANTS",
                    "-p", "tcp",
                    "-m", "owner", "--uid-owner", str(tenant.uid),
                    "-d", destination,
                    "--dport", str(gateway_port),
                    "-j", "REJECT",
                )
        _run("-A", "DSH_TENANTS", "-j", "RETURN")
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise FirewallError(f"unable to install tenant firewall: {detail.strip()}") from error

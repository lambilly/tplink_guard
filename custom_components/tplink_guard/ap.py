"""AP reachability probes for TPLink Guard.

APs are managed by the router's AC controller and never show up in the
router's host_info table, so their online state is checked directly over
the LAN. ICMP is tried first (works even for thin APs without a management
listener); TCP ports are a fallback for hosts that block echo requests.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

_LOGGER = logging.getLogger(__name__)

_PING_AVAILABLE: bool | None = None
_TCP_PORTS = (80, 443, 22)


async def _async_tcp_open(ip: str, port: int, timeout: float) -> bool:
    """Return True when a TCP connection to ``ip:port`` can be opened."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError):  # pragma: no cover - best effort
        pass
    return True


async def _async_icmp_ping(ip: str, timeout: float) -> bool:
    """Return True when the system ``ping`` utility gets a reply."""
    global _PING_AVAILABLE
    if _PING_AVAILABLE is False:
        return False
    flags = (
        ("-n", "1", "-w", "1000")
        if sys.platform == "win32"
        else ("-c", "1", "-W", "1")
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            *flags,
            ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        _PING_AVAILABLE = False
        _LOGGER.debug("ping utility unavailable, using TCP probe only")
        return False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout + 1)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return proc.returncode == 0


async def async_probe_ap(ip: str, timeout: float = 2.0) -> bool:
    """Probe one AP; never raises to the coordinator."""
    if not ip:
        return False
    if await _async_icmp_ping(ip, timeout):
        return True
    results = await asyncio.gather(
        *(_async_tcp_open(ip, port, timeout) for port in _TCP_PORTS),
        return_exceptions=True,
    )
    return any(result is True for result in results)


async def async_probe_aps(
    aps: list[dict[str, str]], timeout: float = 2.0
) -> list[dict[str, Any]]:
    """Probe all APs concurrently and return stable status rows."""
    if not aps:
        return []
    results = await asyncio.gather(
        *(async_probe_ap(str(ap.get("ip") or ""), timeout) for ap in aps),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for ap, online in zip(aps, results):
        rows.append(
            {
                "ip": str(ap.get("ip") or ""),
                "name": str(ap.get("name") or ap.get("ip") or ""),
                "online": online is True,
            }
        )
    return rows

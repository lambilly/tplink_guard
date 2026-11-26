"""Async API client for TP-Link SLP based routers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import unquote

import aiohttp

from .const import DEFAULT_TIMEOUT, ENCODE_KEY1, ENCODE_KEY2

_LOGGER = logging.getLogger(__name__)


class RouterAuthError(Exception):
    """Raised when the router rejects the supplied credentials."""


class RouterConnectionError(Exception):
    """Raised when the router cannot be reached or returns unexpected data."""


def encode_password(password: str) -> str:
    """Encode a password exactly like $.su.orgAuthPwd in the router UI."""
    key1 = ENCODE_KEY1
    key2 = ENCODE_KEY2
    length = max(len(password), len(key1))
    encoded: list[str] = []
    for index in range(length):
        left = right = 187
        if index >= len(password):
            right = ord(key1[index])
        elif index >= len(key1):
            left = ord(password[index])
        else:
            left = ord(password[index])
            right = ord(key1[index])
        encoded.append(key2[(left ^ right) % len(key2)])
    return "".join(encoded)


def _find_stok(node: Any) -> str | None:
    """Find the session token anywhere in a JSON-like response."""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() == "stok" and isinstance(value, str) and value:
                return value
            found = _find_stok(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_stok(item)
            if found:
                return found
    return None


def _iter_lists(node: Any):
    """Yield every list found in a JSON-like response."""
    if isinstance(node, list):
        yield node
        for item in node:
            yield from _iter_lists(item)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_lists(value)


def _decode(value: Any) -> Any:
    """Decode URL-encoded Chinese strings returned by the router."""
    if isinstance(value, str) and "%" in value:
        return unquote(value)
    return value


def _unwrap_row(item: Any) -> dict[str, Any] | None:
    """Unwrap router rows shaped like {"host_info_1": {...}}."""
    if not isinstance(item, dict):
        return None
    if "ip" in item or "ipaddr" in item:
        return item
    for value in item.values():
        if isinstance(value, dict) and ("ip" in value or "ipaddr" in value):
            return value
    return None


def _extract_rows(
    data: Any, required: tuple[tuple[str, ...], ...]
) -> list[dict[str, Any]]:
    """Find and unwrap rows that contain the required field groups."""
    for candidate in _iter_lists(data):
        rows = [row for raw in candidate if (row := _unwrap_row(raw)) is not None]
        if not rows:
            continue
        keys = {str(key).lower() for row in rows[:5] for key in row}
        if all(any(name in keys for name in group) for group in required):
            return [{key: _decode(value) for key, value in row.items()} for row in rows]
    return []


def _extract_hosts(data: Any) -> list[dict[str, Any]]:
    """Extract host_info rows from a response, tolerating wrapper shapes."""
    return _extract_rows(data, (("ip", "ipaddr"), ("mac", "macaddr")))


def _extract_static(data: Any) -> list[dict[str, Any]]:
    """Extract dhcp_static rows from a response."""
    return _extract_rows(data, (("ip", "ipaddr"), ("mac", "macaddr")))


class RouterClient:
    """Small client for the router's local SLP JSON API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"
        self._username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._stok: str | None = None

    @property
    def stok(self) -> str | None:
        return self._stok

    async def async_login(self) -> str:
        """Log in and cache the session token."""
        payload = {
            "login": {
                "username": self._username,
                "password": encode_password(self._password),
            },
            "method": "do",
        }
        try:
            async with self._session.post(
                self._base + "/", json=payload, timeout=self._timeout
            ) as response:
                text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise RouterConnectionError(str(err)) from err

        if response.status in (401, 403):
            raise RouterAuthError("router rejected credentials")

        try:
            data = json.loads(text)
        except (TypeError, ValueError) as err:
            raise RouterConnectionError("router returned non-JSON login response") from err

        stok = _find_stok(data)
        if not stok:
            error_code = data.get("error_code") if isinstance(data, dict) else None
            if error_code is not None:
                raise RouterAuthError(f"login error_code={error_code}")
            raise RouterConnectionError("no stok in login response")
        self._stok = stok
        return stok

    async def _async_query(self, payload: dict[str, Any]) -> Any:
        """POST a DS payload and return decoded JSON."""
        if not self._stok:
            await self.async_login()
        url = f"{self._base}/stok={self._stok}/ds"
        try:
            async with self._session.post(
                url, json=payload, timeout=self._timeout
            ) as response:
                if response.status in (401, 403):
                    self._stok = None
                    raise RouterAuthError("session token expired")
                return await response.json(content_type=None)
        except RouterAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise RouterConnectionError(str(err)) from err

    async def async_get_host_info(self) -> list[dict[str, Any]]:
        """Return the router's online host list.

        The host_info table keeps saved records for terminals that went
        offline (``state: "offline"``), e.g. a laptop that left home long
        ago. Only rows the router currently marks online are returned;
        rows without a state field are kept (older firmware).
        """
        data = await self._async_query(
            {"host_management": {"name": "host_info"}, "method": "get"}
        )
        hosts = _extract_hosts(data)
        hosts = [
            host
            for host in hosts
            if str(host.get("state", "online")).lower() in ("online", "on", "1")
        ]
        _LOGGER.debug("host_info rows: %s", len(hosts))
        return hosts

    async def async_get_static_assignments(self) -> list[dict[str, Any]]:
        """Return the router's static DHCP assignment list."""
        data = await self._async_query(
            {"dhcpd": {"name": "dhcp_static"}, "method": "get"}
        )
        rows = _extract_static(data)
        _LOGGER.debug("dhcp_static rows: %s", len(rows))
        return rows

    async def async_test_connection(self) -> list[dict[str, Any]]:
        """Validate credentials and API access in one call."""
        await self.async_login()
        return await self.async_get_host_info()

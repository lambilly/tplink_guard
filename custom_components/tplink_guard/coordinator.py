"""Data update coordinator for TPLink Guard."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RouterAuthError, RouterClient, RouterConnectionError
from .const import ATTR_LAST_UPDATE, ATTR_WATCHLIST, ATTR_WHITELIST_COUNT, DOMAIN
from .whitelist import (
    Whitelist,
    load_monitored,
    load_whitelist,
    split_watch_note,
    summarize_device,
    whitelist_mtime,
)

_LOGGER = logging.getLogger(__name__)


class TPLinkGuardCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the router and classify online devices against the whitelist."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: RouterClient,
        whitelist_path: str | None,
        watchlist_path: str | None,
        match_by: str,
        scan_interval: int,
        monitored_path: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.whitelist_path = whitelist_path
        self.watchlist_path = watchlist_path
        self.match_by = match_by
        self.whitelist: Whitelist = load_whitelist(whitelist_path)
        self.watchlist: Whitelist = load_whitelist(watchlist_path)
        self._whitelist_mtime = whitelist_mtime(whitelist_path)
        self._watchlist_mtime = whitelist_mtime(watchlist_path)
        self.monitored_path = monitored_path
        self.monitored: list[dict[str, str]] = load_monitored(monitored_path)
        self._monitored_mtime = whitelist_mtime(monitored_path)
        self.devices: dict[str, dict[str, Any]] = {}

    async def async_reload_whitelist(self) -> None:
        """Re-read both lists and refresh all entity states."""
        self.whitelist = load_whitelist(self.whitelist_path)
        self.watchlist = load_whitelist(self.watchlist_path)
        self._whitelist_mtime = whitelist_mtime(self.whitelist_path)
        self._watchlist_mtime = whitelist_mtime(self.watchlist_path)
        self.monitored = load_monitored(self.monitored_path)
        self._monitored_mtime = whitelist_mtime(self.monitored_path)
        await self.async_request_refresh()

    def _async_refresh_lists_if_changed(self) -> None:
        current = whitelist_mtime(self.whitelist_path)
        if current and current != self._whitelist_mtime:
            _LOGGER.debug("Whitelist changed, reloading")
            self.whitelist = load_whitelist(self.whitelist_path)
            self._whitelist_mtime = current
        current = whitelist_mtime(self.watchlist_path)
        if current and current != self._watchlist_mtime:
            _LOGGER.debug("Watchlist changed, reloading")
            self.watchlist = load_whitelist(self.watchlist_path)
            self._watchlist_mtime = current
        current = whitelist_mtime(self.monitored_path)
        if current and current != self._monitored_mtime:
            _LOGGER.debug("Monitored list changed, reloading")
            self.monitored = load_monitored(self.monitored_path)
            self._monitored_mtime = current

    async def _async_update_data(self) -> dict[str, Any]:
        self._async_refresh_lists_if_changed()

        try:
            hosts = await self.client.async_get_host_info()
        except RouterAuthError:
            # The token may have expired. Re-login once and retry immediately.
            try:
                await self.client.async_login()
                hosts = await self.client.async_get_host_info()
            except (RouterAuthError, RouterConnectionError) as err:
                raise UpdateFailed(f"Router authentication failed: {err}") from err
        except RouterConnectionError as err:
            raise UpdateFailed(f"Cannot reach router: {err}") from err

        devices: list[dict[str, Any]] = []
        for host in hosts:
            mac = str(host.get("mac") or host.get("macaddr") or "")
            ip = str(host.get("ip") or "")
            known = self.whitelist.is_known(mac, ip, self.match_by)
            # A watched device must be in the whitelist first.
            watched = known and self.watchlist.is_known(mac, ip, self.match_by)
            device = summarize_device(host, known, self.whitelist.display_name(mac))
            device["watched"] = watched
            devices.append(device)

        self.devices = {
            device["mac"]: device for device in devices if device.get("mac")
        }

        unknown = [device for device in devices if not device["known"]]
        phone_online = [device for device in devices if device["watched"]]
        phone_offline = self.watchlist.count - len(phone_online)
        if unknown:
            _LOGGER.debug("Unknown online devices: %s", [d["ip"] for d in unknown])

        static_devices: list[dict[str, Any]] = []
        try:
            static_rows = await self.client.async_get_static_assignments()
            for row in static_rows:
                static_devices.append(
                    {
                        "mac": str(row.get("mac") or ""),
                        "ip": str(row.get("ip") or ""),
                        "note": str(row.get("note") or ""),
                        "enable": str(row.get("enable") or ""),
                    }
                )
        except (RouterAuthError, RouterConnectionError) as err:
            _LOGGER.debug("Static assignments unavailable: %s", err)

        static_enabled = sum(
            1
            for device in static_devices
            if device["enable"].lower() in ("on", "1", "true")
        )

        # Full watched roster with human-friendly names, resolved from the
        # watchlist file notes. Only entries that are also in the whitelist
        # count as watched, matching the online classification above.
        watchlist_devices: list[dict[str, Any]] = []
        for mac, entry in self.watchlist.entries.items():
            if not self.whitelist.is_known(mac, entry.get("ip", ""), self.match_by):
                continue
            name, nickname, device_name = split_watch_note(entry.get("note") or "")
            watchlist_devices.append(
                {
                    "mac": mac,
                    "ip": entry.get("ip", ""),
                    "name": name,
                    "nickname": nickname,
                    "device": device_name,
                }
            )

        return {
            "devices": devices,
            "online_count": len(devices),
            "known_count": len(devices) - len(unknown),
            "unknown_count": len(unknown),
            "unknown_devices": unknown,
            "phone_count": self.watchlist.count,
            "phone_online_count": len(phone_online),
            "phone_offline_count": max(phone_offline, 0),
            "phone_online": phone_online,
            ATTR_WATCHLIST: watchlist_devices,
            "static_count": len(static_devices),
            "static_enabled_count": static_enabled,
            "static_devices": static_devices,
            ATTR_WHITELIST_COUNT: self.whitelist.count,
            ATTR_LAST_UPDATE: datetime.now(),
        }

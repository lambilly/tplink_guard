"""Data update coordinator for TPLink Guard."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RouterAuthError, RouterClient, RouterConnectionError
from .ap import async_probe_ap, async_probe_aps
from .const import (
    AP_PROBE_TIMEOUT,
    ATTR_LAST_UPDATE,
    ATTR_WATCHLIST,
    ATTR_WHITELIST_COUNT,
    CHECKLIST_GROUPS,
    DOMAIN,
    MAC_CHECKLIST_GROUPS,
    SESSION_YIELD_MINUTES,
)
from .whitelist import (
    Whitelist,
    load_aps,
    load_checklist,
    load_checklist_by_mac,
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
        ap_path: str | None = None,
        gateway_path: str | None = None,
        asr_path: str | None = None,
        server_path: str | None = None,
        router_path: str | None = None,
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
        self.ap_path = ap_path
        self.aps: list[dict[str, str]] = load_aps(ap_path)
        self._ap_mtime = whitelist_mtime(ap_path)
        # Generic list-driven checklists: gateway / ASR / server / router.
        # Each group has its own TXT file and produces its own count sensor.
        self.checklist_paths: dict[str, str | None] = {
            "gateway": gateway_path,
            "asr": asr_path,
            "server": server_path,
            "router": router_path,
        }
        self.checklists: dict[str, list[dict[str, str]]] = {
            name: self._read_checklist(name) for name, _, _ in CHECKLIST_GROUPS
        }
        self._checklist_mtimes: dict[str, float] = {
            name: whitelist_mtime(self.checklist_paths[name])
            for name, _, _ in CHECKLIST_GROUPS
        }
        self.devices: dict[str, dict[str, Any]] = {}
        # Fetched once on the first successful refresh; the model never
        # changes until a reboot with different firmware.
        self.router_info: dict[str, Any] | None = None
        # Session-yield state: the router allows ONE active local admin
        # session per account (bound to the last login's source IP). When
        # a human logs into the web UI the integration's next poll gets a
        # 401; instead of re-logging in immediately (which kicks the human
        # back), stop polling for a while and keep serving the last data.
        self._last_good_data: dict[str, Any] | None = None
        self._yield_until: datetime | None = None

    def _read_checklist(self, name: str) -> list[dict[str, str]]:
        """Read a checklist with the loader matching its entry format."""
        path = self.checklist_paths[name]
        if name in MAC_CHECKLIST_GROUPS:
            return load_checklist_by_mac(path)
        return load_checklist(path)

    async def async_reload_whitelist(self) -> None:
        """Re-read both lists and refresh all entity states."""
        self.whitelist = load_whitelist(self.whitelist_path)
        self.watchlist = load_whitelist(self.watchlist_path)
        self._whitelist_mtime = whitelist_mtime(self.whitelist_path)
        self._watchlist_mtime = whitelist_mtime(self.watchlist_path)
        self.monitored = load_monitored(self.monitored_path)
        self._monitored_mtime = whitelist_mtime(self.monitored_path)
        self.aps = load_aps(self.ap_path)
        self._ap_mtime = whitelist_mtime(self.ap_path)
        for name, _, _ in CHECKLIST_GROUPS:
            self.checklists[name] = self._read_checklist(name)
            self._checklist_mtimes[name] = whitelist_mtime(self.checklist_paths[name])
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
        current = whitelist_mtime(self.ap_path)
        if current and current != self._ap_mtime:
            _LOGGER.debug("AP list changed, reloading")
            self.aps = load_aps(self.ap_path)
            self._ap_mtime = current
        for name, _, _ in CHECKLIST_GROUPS:
            current = whitelist_mtime(self.checklist_paths[name])
            if current and current != self._checklist_mtimes[name]:
                _LOGGER.debug("%s list changed, reloading", name.title())
                self.checklists[name] = self._read_checklist(name)
                self._checklist_mtimes[name] = current

    async def _async_checklist_snapshot(self, group: str) -> dict[str, Any]:
        """Aggregate one checklist from direct probes and HA entity states.

        IP entries are probed directly (no third-party tracker needed);
        entity entries are read from the HA state machine. Neither touches
        the router admin session, so this keeps working during the
        session-yield period.
        """
        entries = self.checklists.get(group) or []
        if not entries:
            return {
                f"{group}_all_count": 0,
                f"{group}_online_count": 0,
                f"{group}_offline_count": 0,
                f"{group}_devices": [],
            }
        probe_indexes = [
            index
            for index, item in enumerate(entries)
            if item.get("kind") == "probe"
        ]
        probe_results = await asyncio.gather(
            *(
                async_probe_ap(
                    str(entries[index].get("target") or ""),
                    timeout=AP_PROBE_TIMEOUT,
                )
                for index in probe_indexes
            ),
            return_exceptions=True,
        )
        online_by_index = {
            index: result is True
            for index, result in zip(probe_indexes, probe_results)
        }

        devices: list[dict[str, Any]] = []
        for index, item in enumerate(entries):
            if item.get("kind") == "probe":
                online = online_by_index.get(index, False)
                state = "online" if online else "offline"
            else:
                entity = self.hass.states.get(item.get("entity_id") or "")
                state = str(entity.state) if entity is not None else ""
                if entity is None:
                    online = False
                elif item.get("op") == "ne":
                    online = state != item.get("expected")
                else:
                    online = state == item.get("expected")
            devices.append(
                {
                    "entity_id": item.get("entity_id") or "",
                    "name": item.get("name") or "",
                    "state": state,
                    "online": online,
                }
            )
        online_count = sum(1 for device in devices if device["online"])
        return {
            f"{group}_all_count": len(devices),
            f"{group}_online_count": online_count,
            f"{group}_offline_count": len(devices) - online_count,
            f"{group}_devices": devices,
        }

    async def _async_mac_checklist_snapshot(
        self, group: str, devices: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Resolve an IP/MAC checklist against the router snapshot or LAN.

        * ``mac`` entries are matched against the router's online host table;
        * ``probe`` entries are pinged over the LAN (routers/switches never
          appear in their own host table).

        No HA entity state is consulted. Computed after ``devices`` is built
        because MAC matching needs the fresh router snapshot.
        """
        entries = self.checklists.get(group) or []
        if not entries:
            return {
                f"{group}_all_count": 0,
                f"{group}_online_count": 0,
                f"{group}_offline_count": 0,
                f"{group}_devices": [],
            }
        by_mac = {
            str(device.get("mac") or ""): device
            for device in devices
            if device.get("mac")
        }
        # Probe IP entries concurrently to keep the refresh fast.
        probe_indexes = [
            index
            for index, item in enumerate(entries)
            if item.get("kind") == "probe"
        ]
        probe_results = await asyncio.gather(
            *(
                async_probe_ap(
                    str(entries[index].get("target") or ""),
                    timeout=AP_PROBE_TIMEOUT,
                )
                for index in probe_indexes
            ),
            return_exceptions=True,
        )
        probe_online = {
            index: result is True
            for index, result in zip(probe_indexes, probe_results)
        }

        result_devices: list[dict[str, Any]] = []
        for index, item in enumerate(entries):
            kind = item.get("kind")
            mac = item.get("mac") or ""
            name_cn = item.get("name_cn") or ""
            name_en = item.get("name_en") or ""
            matched: dict[str, Any] | None = None
            if kind == "mac":
                matched = by_mac.get(mac)
                online = matched is not None
            elif kind == "probe":
                online = probe_online.get(index, False)
            else:
                # Legacy entity-id rows are no longer loaded from the lists.
                online = False
            result_devices.append(
                {
                    "mac": mac,
                    "name": name_cn or name_en or item.get("target", ""),
                    "name_cn": name_cn,
                    "name_en": name_en,
                    "entity_id": item.get("entity_id") or "",
                    "ip": str(matched.get("ip") or "")
                    if matched
                    else (item.get("target") if kind == "probe" else ""),
                    "hostname": str(matched.get("hostname") or "") if matched else "",
                    "state": "online" if online else "offline",
                    "online": online,
                }
            )
        online_count = sum(1 for device in result_devices if device["online"])
        return {
            f"{group}_all_count": len(result_devices),
            f"{group}_online_count": online_count,
            f"{group}_offline_count": len(result_devices) - online_count,
            f"{group}_devices": result_devices,
        }

    async def _async_ap_snapshot(self) -> dict[str, Any]:
        """Probe APs directly; independent of the router admin session."""
        ap_devices = await async_probe_aps(self.aps, timeout=AP_PROBE_TIMEOUT)
        online_count = sum(1 for ap in ap_devices if ap["online"])
        return {
            "ap_all_count": len(ap_devices),
            "ap_online_count": online_count,
            "ap_offline_count": len(ap_devices) - online_count,
            "ap_devices": ap_devices,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        self._async_refresh_lists_if_changed()
        # APs are probed over the LAN, so this works even while the router
        # admin session is yielded to a human web-UI login.
        ap_data = await self._async_ap_snapshot()
        checklist_data: dict[str, Any] = {}
        for name, _, _ in CHECKLIST_GROUPS:
            if name in MAC_CHECKLIST_GROUPS:
                # MAC-based groups are matched against the router host table
                # below, after the fresh device snapshot is available.
                continue
            checklist_data.update(await self._async_checklist_snapshot(name))

        # Session yield: while another client owns the admin session, keep
        # serving the last snapshot without touching the router at all.
        if self._yield_until is not None:
            if datetime.now() < self._yield_until and self._last_good_data:
                return {**self._last_good_data, **ap_data, **checklist_data}
            # Grace period is over: take the session back proactively so
            # the following query does not fail (and yield again).
            self._yield_until = None
            try:
                await self.client.async_login()
            except RouterConnectionError as err:
                raise UpdateFailed(f"Cannot reach router: {err}") from err
            except RouterAuthError as err:
                raise UpdateFailed(f"Router authentication failed: {err}") from err

        try:
            hosts = await self.client.async_get_host_info()
        except RouterAuthError:
            if self._last_good_data:
                # We HAD a working session that is now gone — someone else
                # (web UI login) took over the single-session router.
                # Yield instead of stealing it back immediately. Note: the
                # client clears its stok on auth errors, so we cannot check
                # client.stok here; a prior successful snapshot is the
                # reliable "session was working" signal.
                self._yield_until = datetime.now() + timedelta(
                    minutes=SESSION_YIELD_MINUTES
                )
                _LOGGER.warning(
                    "Router admin session was taken over by another client "
                    "(likely a web UI login). Yielding for %s minutes to let "
                    "the human work; entity data will stay on the last values "
                    "until then.",
                    SESSION_YIELD_MINUTES,
                )
                return {**self._last_good_data, **ap_data, **checklist_data}
            # No prior data (startup) — this is a genuine login failure,
            # retry once as before.
            try:
                await self.client.async_login()
                hosts = await self.client.async_get_host_info()
            except (RouterAuthError, RouterConnectionError) as err:
                raise UpdateFailed(f"Router authentication failed: {err}") from err
        except RouterConnectionError as err:
            raise UpdateFailed(f"Cannot reach router: {err}") from err

        # Router model / hardware / firmware — one-time fetch, non-fatal.
        if self.router_info is None:
            try:
                self.router_info = await self.client.async_get_device_info()
            except (RouterAuthError, RouterConnectionError) as err:
                _LOGGER.debug("Router device info unavailable: %s", err)
                self.router_info = {}

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

        # MAC-based checklists (e.g. ASR) match their TXT MAC list directly
        # against the router's online host table just built.
        mac_checklist_data: dict[str, Any] = {}
        for name in MAC_CHECKLIST_GROUPS:
            mac_checklist_data.update(await self._async_mac_checklist_snapshot(name, devices))

        unknown = [device for device in devices if not device["known"]]
        phone_online = [device for device in devices if device["watched"]]
        phone_offline = self.watchlist.count - len(phone_online)
        if unknown:
            _LOGGER.debug("Unknown online devices: %s", [d["ip"] for d in unknown])

        # Monitored terminals: terminals that own a device_tracker entity.
        monitored_macs = {item.get("mac") for item in self.monitored if item.get("mac")}
        monitored_online_count = sum(
            1 for device in devices if device.get("mac") in monitored_macs
        )
        monitored_offline_count = max(len(monitored_macs) - monitored_online_count, 0)
        # Detail rows for the merged Monitored Online Count entity: the
        # secondary count entities become attributes instead of entities.
        monitored_devices: list[dict[str, Any]] = []
        for item in self.monitored:
            mac = str(item.get("mac") or "")
            matched = next((device for device in devices if device.get("mac") == mac), None)
            monitored_devices.append(
                {
                    "mac": mac,
                    "entity_id": str(item.get("entity_id") or ""),
                    "name": str(item.get("name") or item.get("entity_id") or mac),
                    "ip": str(matched.get("ip") or "") if matched else "",
                    "hostname": str(matched.get("hostname") or "") if matched else "",
                    "state": "online" if matched else "offline",
                    "online": matched is not None,
                }
            )

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

        router_info = self.router_info or {}
        router_model = " ".join(
            part
            for part in (
                str(router_info.get("model") or ""),
                str(router_info.get("hardware_version") or ""),
            )
            if part
        )

        data = {
            **ap_data,
            **checklist_data,
            **mac_checklist_data,
            "devices": devices,
            "router_model": router_model or "unknown",
            "router_info": router_info,
            "online_count": len(devices),
            "known_count": len(devices) - len(unknown),
            "unknown_count": len(unknown),
            "unknown_devices": unknown,
            "phone_count": self.watchlist.count,
            "phone_online_count": len(phone_online),
            "phone_offline_count": max(phone_offline, 0),
            "phone_online": phone_online,
            "monitored_count": len(monitored_macs),
            "monitored_online_count": monitored_online_count,
            "monitored_offline_count": monitored_offline_count,
            "monitored_devices": monitored_devices,
            ATTR_WATCHLIST: watchlist_devices,
            "static_count": len(static_devices),
            "static_enabled_count": static_enabled,
            "static_devices": static_devices,
            ATTR_WHITELIST_COUNT: self.whitelist.count,
            ATTR_LAST_UPDATE: datetime.now(),
        }
        # Snapshot for the session-yield period.
        self._last_good_data = dict(data)
        return data

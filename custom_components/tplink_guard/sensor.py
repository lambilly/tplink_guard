"""Sensor entities for TPLink Guard."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DEVICES,
    ATTR_LAST_UPDATE,
    ATTR_WATCHLIST,
    ATTR_WHITELIST_COUNT,
    DOMAIN,
)
from .coordinator import TPLinkGuardCoordinator


# English object ids -> entity_id is <platform>.tplink_guard_<object_id>
OBJECT_IDS: dict[str, str] = {
    "online_count": "online_terminal_count",
    "known_count": "whitelist_online_count",
    "unknown_count": "unknown_terminal_count",
    "unknown_ips": "unknown_terminal_ip",
    "whitelist_count": "whitelist_terminal_count",
    "static_count": "static_ip_count",
    "watched_online_count": "watched_online_terminal_count",
    "watched_offline_count": "watched_offline_terminal_count",
    "watched_ips": "watched_terminal_ip",
    # Guest visit sensor keeps the legacy Node-RED identifier:
    # entity_id is sensor.guest_visit_count (no tplink_guard prefix).
    "guest_visit": "guest_visit_count",
}

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="online_count", name="Online Terminal Count", icon="mdi:router-network"
    ),
    SensorEntityDescription(
        key="known_count", name="Whitelist Online Count", icon="mdi:check-network"
    ),
    SensorEntityDescription(
        key="unknown_count",
        name="Unknown Terminal Count",
        icon="mdi:alert-circle-outline",
    ),
    SensorEntityDescription(
        key="unknown_ips", name="Unknown Terminal IP", icon="mdi:ip-network"
    ),
    SensorEntityDescription(
        key="whitelist_count",
        name="Whitelist Terminal Count",
        icon="mdi:format-list-bulleted",
    ),
    SensorEntityDescription(
        key="static_count",
        name="Static IP Count",
        icon="mdi:ip-network-outline",
    ),
    SensorEntityDescription(
        key="watched_online_count",
        name="Watched Online Terminal Count",
        icon="mdi:eye-outline",
    ),
    SensorEntityDescription(
        key="watched_offline_count",
        name="Watched Offline Terminal Count",
        icon="mdi:eye-off-outline",
    ),
    SensorEntityDescription(
        key="watched_ips",
        name="Watched Terminal IP",
        icon="mdi:eye",
    ),
    SensorEntityDescription(
        key="guest_visit",
        name="Guest Visit Count",
        icon="mdi:account-arrow-left-outline",
    ),
)

# Attribute keys of the guest visit sensor, identical to the legacy
# Node-RED flow output so dashboards keep working unchanged.
GUEST_ATTRS = (
    "guest_all_count",
    "guest_all_list",
    "guest_home_count",
    "guest_home_list",
    "guest_nick_list",
    "guest_out_count",
    "guest_out_list",
    "guest_detail",
    "update_time",
)


_GUEST_MAC_CLEAN = re.compile(r"[^0-9a-fA-F]")


def _clean_mac(value: Any) -> str:
    """Normalize a MAC to 12 uppercase hex digits (empty if invalid)."""
    cleaned = _GUEST_MAC_CLEAN.sub("", str(value or "")).upper()
    return cleaned if len(cleaned) == 12 else ""


def guest_stats(data: dict[str, Any]) -> dict[str, Any]:
    """Compute guest visit statistics from coordinator data.

    Mirrors the legacy Node-RED flow: a watched terminal that is online
    counts as being at home. Output fields keep the exact legacy names.
    """
    watchlist = data.get(ATTR_WATCHLIST) or []
    online_macs = {
        _clean_mac(device.get("mac"))
        for device in data.get("watched_online") or []
    }

    all_list: list[str] = []
    home_list: list[str] = []
    out_list: list[str] = []
    nick_list: list[str] = []
    for entry in watchlist:
        mac = _clean_mac(entry.get("mac"))
        name = str(entry.get("name") or entry.get("nickname") or "").strip() or mac
        nickname = str(entry.get("nickname") or entry.get("name") or "").strip() or name
        all_list.append(name)
        if mac and mac in online_macs:
            home_list.append(name)
            nick_list.append(nickname)
        else:
            out_list.append(name)

    home_count = len(home_list)
    return {
        "guest_all_count": len(all_list),
        "guest_all_list": "｜".join(all_list) or "客人",
        "guest_home_count": home_count,
        "guest_home_list": "｜".join(home_list) or "客人",
        "guest_nick_list": "｜".join(nick_list) or "客人",
        "guest_out_count": len(out_list),
        "guest_out_list": "｜".join(out_list) or "客人",
        "guest_detail": f"{home_count}个客人来访。" if home_count else "",
        "update_time": _format_time(data.get(ATTR_LAST_UPDATE)),
    }


def _format_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value else ""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: TPLinkGuardCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TPLinkGuardSensor(coordinator, entry.entry_id, description)
        for description in SENSOR_DESCRIPTIONS
    )


class TPLinkGuardSensor(CoordinatorEntity, SensorEntity):
    """Representation of a TPLink Guard sensor."""

    entity_description: SensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TPLinkGuardCoordinator,
        entry_id: str,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_suggested_object_id = f"tplink_guard_{OBJECT_IDS[description.key]}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="TPLink Guard",
            manufacturer="TP-Link",
        )
        if description.key == "guest_visit":
            # Keep the legacy Node-RED identity: entity_id sensor.guest_visit_count
            # with friendly name 来访客人 (no device prefix).
            self._attr_suggested_object_id = OBJECT_IDS[description.key]
            self._attr_has_entity_name = False
            self._attr_name = description.name
            self._attr_native_unit_of_measurement = "人"

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        key = self.entity_description.key
        if key == "guest_visit":
            return guest_stats(data)["guest_home_count"]
        if key == "unknown_ips":
            ips = [device["ip"] for device in data.get("unknown_devices", []) if device.get("ip")]
            return ", ".join(ips) if ips else "none"
        if key == "watched_ips":
            ips = [device["ip"] for device in data.get("watched_online", []) if device.get("ip")]
            return ", ".join(ips) if ips else "none"
        return data.get(key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        if self.entity_description.key == "guest_visit":
            return guest_stats(data)
        if self.entity_description.key == "unknown_ips":
            return {
                ATTR_DEVICES: data.get("unknown_devices", []),
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        if self.entity_description.key == "watched_ips":
            return {
                ATTR_DEVICES: data.get("watched_online", []),
                ATTR_WATCHLIST: data.get(ATTR_WATCHLIST, []),
                "watched_count": data.get("watched_count"),
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        if self.entity_description.key == "online_count":
            return {ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE)}
        if self.entity_description.key == "whitelist_count":
            return {ATTR_WHITELIST_COUNT: data.get(ATTR_WHITELIST_COUNT)}
        if self.entity_description.key == "static_count":
            return {
                "enabled": data.get("static_enabled_count"),
                "limit": 80,
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        return {}

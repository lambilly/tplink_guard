"""Sensor entities for TPLink Guard."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DEVICES,
    ATTR_LAST_UPDATE,
    ATTR_WATCHLIST,
    ATTR_WHITELIST_COUNT,
    DOMAIN,
)
from .coordinator import TPLinkGuardCoordinator

_LOGGER = logging.getLogger(__name__)


# English object ids -> entity_id is <platform>.tplink_guard_<object_id>
OBJECT_IDS: dict[str, str] = {
    "online_count": "online_all_count",
    # One entity per group: the other counts / IP lists are attributes.
    # Whitelist / static / unknown counts now live on online_all_count.
    "watched_online_count": "phone_online_count",
    "monitored_online_count": "device_online_count",
    # Guest visit sensor follows HA's natural prefixed id:
    # sensor.tplink_guard_guest_visit_count.
    "guest_visit": "guest_visit_count",
    "router_model": "router_model",
    "ap_online_count": "ap_online_count",
    "gateway_online_count": "gateway_online_count",
    "asr_online_count": "asr_online_count",
    "server_online_count": "server_online_count",
    "router_online_count": "router_online_count",
}

# List-driven online-count groups. Attribute names mirror the legacy
# Node-RED sensors so dashboards only need the entity id changed.
CHECKLIST_SENSORS: dict[str, tuple[str, str]] = {
    "gateway_online_count": ("gateway", "mdi:network-outline"),
    "asr_online_count": ("asr", "mdi:microphone-settings"),
    "server_online_count": ("server", "mdi:nas"),
    "router_online_count": ("router", "mdi:server-network"),
}

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="online_count", name="Online All Count", icon="mdi:router-network"
    ),
    SensorEntityDescription(
        key="watched_online_count",
        name="Phone Online Count",
        icon="mdi:eye-outline",
    ),
    SensorEntityDescription(
        key="guest_visit",
        name="Guest Visit Count",
        icon="mdi:account-arrow-left-outline",
    ),
    SensorEntityDescription(
        key="monitored_online_count",
        name="Device Online Count",
        icon="mdi:monitor-eye",
    ),
    SensorEntityDescription(
        key="router_model",
        name="Router Model",
        icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="ap_online_count",
        name="AP Online Count",
        icon="mdi:wifi-marker",
    ),
    SensorEntityDescription(
        key="gateway_online_count",
        name="Gateway Online Count",
        icon="mdi:network-outline",
    ),
    SensorEntityDescription(
        key="asr_online_count",
        name="ASR Online Count",
        icon="mdi:microphone-settings",
    ),
    SensorEntityDescription(
        key="server_online_count",
        name="Server Online Count",
        icon="mdi:nas",
    ),
    SensorEntityDescription(
        key="router_online_count",
        name="Router Online Count",
        icon="mdi:server-network",
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
        for device in data.get("phone_online") or []
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
    registry = async_get_entity_registry(hass)
    entities = []
    for description in SENSOR_DESCRIPTIONS:
        entity = TPLinkGuardSensor(coordinator, entry.entry_id, description)
        if description.key == "guest_visit":
            _pin_guest_identity(registry, entry, entity)
        entities.append(entity)
    async_add_entities(entities)


def _pin_guest_identity(registry, entry: ConfigEntry, entity) -> None:
    """Pin the guest sensor display name in the registry.

    The entity_id follows HA 2026.11's natural scheme
    (sensor.tplink_guard_guest_visit_count); Node-RED references the
    prefixed id, so only the display name is pinned here — renaming the
    name alone never regenerates the id.
    """
    entry_obj = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        entity.unique_id,
        config_entry=entry,
        original_name=entity._attr_name,
    )
    if entry_obj.name != "Guest Visit Count":
        try:
            registry.async_update_entity(entry_obj.entity_id, name="Guest Visit Count")
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Could not pin guest sensor display name", exc_info=True
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
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        # No "tplink_guard_" prefix: without a suggested id HA 2026.11
        # prepends the device name ("TPLink Guard") to the generated id.
        self._attr_suggested_object_id = OBJECT_IDS[description.key]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="TPLink Guard",
            manufacturer="TP-Link",
        )
        if description.key == "ap_online_count":
            # Match the legacy Node-RED sensor's "个" unit so dashboards
            # can switch entity ids without changing their formatting.
            self._attr_native_unit_of_measurement = "个"
        elif description.key in CHECKLIST_SENSORS:
            # Legacy units: ASR "个", the others "台".
            self._attr_native_unit_of_measurement = (
                "个" if description.key == "asr_online_count" else "台"
            )
        if description.key == "guest_visit":
            # Attached to the TPLink Guard device; entity id follows the
            # natural prefixed scheme (sensor.tplink_guard_guest_visit_count,
            # used by Node-RED). Display name pinned as "Guest Visit Count".
            self._attr_has_entity_name = False
            self._attr_name = description.name
            self._attr_native_unit_of_measurement = "人"

    @property
    def device_info(self) -> DeviceInfo:
        """Device card info, enriched with the router model when known."""
        info = DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="TPLink Guard",
            manufacturer="TP-Link",
        )
        data = self.coordinator.data or {}
        if data.get("router_model") and data["router_model"] != "unknown":
            info["model"] = str(data["router_model"])
        return info

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        key = self.entity_description.key
        if key == "guest_visit":
            return guest_stats(data)["guest_home_count"]
        # The coordinator data keys were renamed watched_* -> phone_*; the
        # entity key (and thus unique id) stays stable for the registry.
        if key == "watched_online_count":
            return data.get("phone_online_count")
        return data.get(key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        key = self.entity_description.key

        # Device group (formerly "monitored"): one online-count sensor, the
        # remaining figures are attributes with the same device_* prefix.
        if key == "monitored_online_count":
            devices = data.get("monitored_devices") or []
            names = [
                str(device.get("name") or device.get("mac") or "") for device in devices
            ]
            online = [
                name for device, name in zip(devices, names) if device.get("online")
            ]
            offline = [
                name for device, name in zip(devices, names) if not device.get("online")
            ]
            return {
                "device_online_count": data.get("monitored_online_count"),
                "device_offline_count": data.get("monitored_offline_count"),
                "device_all_count": len(devices),
                "device_all_list": "｜".join(names) or "无",
                "device_online_list": "｜".join(online) or "无",
                "device_offline_list": "｜".join(offline) or "无",
                "update_time": _format_time(data.get(ATTR_LAST_UPDATE)),
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        if key == "watched_online_count":
            online_devices = data.get("phone_online") or []
            roster = data.get(ATTR_WATCHLIST) or []
            online_macs = {str(device.get("mac") or "") for device in online_devices}
            roster_names = [
                str(item.get("nickname") or item.get("name") or "") for item in roster
            ]
            online_names = [
                str(
                    device.get("display_name")
                    or device.get("hostname")
                    or device.get("mac")
                    or ""
                )
                for device in online_devices
            ]
            offline_names = [
                str(item.get("nickname") or item.get("name") or "")
                for item in roster
                if str(item.get("mac") or "") not in online_macs
            ]
            return {
                ATTR_DEVICES: online_devices,
                "phone_all_count": len(roster),
                "phone_online_count": data.get("phone_online_count"),
                "phone_offline_count": data.get("phone_offline_count"),
                "phone_all_list": "｜".join(roster_names) or "无",
                "phone_online_list": "｜".join(online_names) or "无",
                "phone_offline_list": "｜".join(offline_names) or "无",
                "update_time": _format_time(data.get(ATTR_LAST_UPDATE)),
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        if key == "ap_online_count":
            # Legacy Node-RED produced sensor.ap_online_count with these
            # attributes; keep them identical so dashboards can switch
            # entity ids without changing any template logic.
            aps = data.get("ap_devices") or []
            names = [str(ap.get("name") or ap.get("ip") or "") for ap in aps]
            online = [name for ap, name in zip(aps, names) if ap.get("online")]
            offline = [name for ap, name in zip(aps, names) if not ap.get("online")]
            return {
                "ap_all_count": len(aps),
                "ap_all_list": "｜".join(names) or "无",
                "ap_online_count": len(online),
                "ap_online_list": "｜".join(online) or "无",
                "ap_offline_count": len(offline),
                "ap_offline_list": "｜".join(offline) or "无",
                "update_time": _format_time(data.get(ATTR_LAST_UPDATE)),
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        checklist = CHECKLIST_SENSORS.get(self.entity_description.key)
        if checklist:
            # Mirror the legacy Node-RED sensor attributes for every group:
            # gateway / asr / server / router all share the same shape.
            group = checklist[0]
            devices = data.get(f"{group}_devices") or []
            names = [
                str(device.get("name") or device.get("entity_id") or "")
                for device in devices
            ]
            online = [
                name for device, name in zip(devices, names) if device.get("online")
            ]
            offline = [
                name for device, name in zip(devices, names) if not device.get("online")
            ]
            return {
                f"{group}_all_count": len(devices),
                f"{group}_all_list": "｜".join(names) or "无",
                f"{group}_online_count": len(online),
                f"{group}_online_list": "｜".join(online) or "无",
                f"{group}_offline_count": len(offline),
                f"{group}_offline_list": "｜".join(offline) or "无",
                "update_time": _format_time(data.get(ATTR_LAST_UPDATE)),
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        if self.entity_description.key == "router_model":
            info = data.get("router_info") or {}
            return {
                "hardware_version": info.get("hardware_version", ""),
                "firmware_version": info.get("firmware_version", ""),
                "router_mac": info.get("mac", ""),
                ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
            }
        if key == "guest_visit":
            return guest_stats(data)
        if key == "online_count":
            # Online All Count is the single home for the terminal overview:
            # the former Whitelist / Static IP / Unknown Terminal Count
            # sensors became attributes so one entity covers the whole picture.
            attrs: dict[str, Any] = {ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE)}

            # Whitelist group (formerly Whitelist Online Count).
            whitelist_devices = [
                device for device in data.get("devices") or [] if device.get("known")
            ]
            total = int(data.get(ATTR_WHITELIST_COUNT) or 0)
            online_count = int(data.get("known_count") or 0)
            entries = (
                getattr(getattr(self.coordinator, "whitelist", None), "entries", {}) or {}
            )
            all_names = [
                str(entry.get("hostname") or entry.get("note") or "")
                .split(",")[0]
                .strip()
                for entry in entries.values()
            ]
            online_names = [
                str(
                    device.get("display_name")
                    or device.get("hostname")
                    or device.get("mac")
                    or ""
                )
                for device in whitelist_devices
            ]
            online_macs = {str(device.get("mac") or "") for device in whitelist_devices}
            offline_names = [
                str(entry.get("hostname") or entry.get("note") or "")
                .split(",")[0]
                .strip()
                for mac, entry in entries.items()
                if str(mac) not in online_macs
            ]
            attrs[ATTR_WHITELIST_COUNT] = total
            attrs.update(
                {
                    "whitelist_terminal_count": total,
                    "whitelist_online_count": online_count,
                    "whitelist_offline_count": max(total - online_count, 0),
                    "whitelist_all_count": total,
                    "whitelist_all_list": "｜".join(n for n in all_names if n) or "无",
                    "whitelist_online_list": "｜".join(online_names) or "无",
                    "whitelist_offline_list": "｜".join(n for n in offline_names if n)
                    or "无",
                }
            )

            # Static IP group (formerly Static IP Count).
            attrs.update(
                {
                    "static_ip_count": data.get("static_count"),
                    "static_ip_enabled": data.get("static_enabled_count"),
                    "static_ip_limit": 80,
                }
            )

            # Unknown terminal group (formerly Unknown Terminal Count):
            # renamed to unknown_online_count, with its IP list alongside.
            unknown_devices = data.get("unknown_devices") or []
            unknown_ips = [
                str(device.get("ip") or "")
                for device in unknown_devices
                if device.get("ip")
            ]
            attrs.update(
                {
                    "unknown_online_count": data.get("unknown_count"),
                    "unknown_ip_list": ", ".join(unknown_ips) if unknown_ips else "none",
                }
            )
            attrs["update_time"] = _format_time(data.get(ATTR_LAST_UPDATE))
            return attrs
        return {}

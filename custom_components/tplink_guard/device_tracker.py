"""Device tracker entities for monitored terminals.

Each line of the monitored list file (``lists/monitored.txt`` inside the
integration folder) defines one terminal that gets its own device_tracker
entity showing whether it is online (home) or offline (not_home).

Line format: ``MAC, entity_id, 中文名称``. Both the entity id and the
Chinese name are optional; missing values are derived from the router
hostname (falling back to the MAC).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.device_tracker import SourceType

try:  # HA >= 2025.x: the entity class lives on the package root.
    from homeassistant.components.device_tracker import TrackerEntity
except ImportError:  # pragma: no cover - older HA releases
    from homeassistant.components.device_tracker.config_entry import TrackerEntity

DeviceTrackerEntity = TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_LAST_UPDATE, DOMAIN
from .coordinator import TPLinkGuardCoordinator
from .whitelist import normalize_mac

_LOGGER = logging.getLogger(__name__)

_SLUG_CLEAN = re.compile(r"[^0-9a-z_\-]+")


def _slugify(value: str) -> str:
    """Best-effort slug for an entity id fragment."""
    text = str(value or "").strip().lower().replace(" ", "_")
    return _SLUG_CLEAN.sub("_", text).strip("_")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device trackers for monitored terminals."""
    coordinator: TPLinkGuardCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[MonitoredTerminalTracker] = []
    for item in coordinator.monitored:
        entities.append(
            MonitoredTerminalTracker(coordinator, entry.entry_id, item)
        )
    async_add_entities(entities)


class MonitoredTerminalTracker(CoordinatorEntity, DeviceTrackerEntity):
    """Track one monitored terminal by its MAC presence on the router."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: TPLinkGuardCoordinator,
        entry_id: str,
        item: dict[str, str],
    ) -> None:
        super().__init__(coordinator)
        self._mac = normalize_mac(item.get("mac"))
        custom_id = str(item.get("entity_id") or "").strip()
        custom_name = str(item.get("name") or "").strip()

        # Entity id: prefer the custom id from the TXT; otherwise derive
        # from the router hostname (if the device is currently known)
        # and finally fall back to the MAC.
        if custom_id:
            id_part = custom_id.split(".")[-1].strip()
        else:
            hostname = self._lookup_hostname()
            id_part = _slugify(hostname) if hostname else f"terminal_{self._mac[-6:].lower()}"
        self._attr_name = id_part or f"terminal_{self._mac[-6:].lower()}"

        # Friendly name shown in the UI: prefer the custom Chinese name,
        # then the hostname, then the MAC.
        hostname = self._lookup_hostname()
        self._friendly = custom_name or hostname or self._mac

        self._attr_unique_id = f"{entry_id}_monitored_{self._mac}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="TPLink Guard",
            manufacturer="TP-Link",
        )
        self._attr_icon = "mdi:monitor"
        self._attr_source_type = SourceType.ROUTER

    def _lookup_hostname(self) -> str:
        """Best-effort hostname lookup from the latest coordinator data."""
        data = self.coordinator.data or {}
        for device in data.get("devices") or []:
            if device.get("mac") == self._mac and device.get("hostname"):
                return str(device["hostname"])
        return ""

    @property
    def is_connected(self) -> bool:
        """True when the terminal is currently online on the router."""
        data = self.coordinator.data or {}
        return any(
            device.get("mac") == self._mac
            for device in data.get("devices") or []
        )

    @property
    def state(self) -> str | None:
        """home/not_home; HA 2026.11 no longer derives this from is_connected."""
        return STATE_HOME if self.is_connected else STATE_NOT_HOME

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        device = next(
            (
                d
                for d in data.get("devices") or []
                if d.get("mac") == self._mac
            ),
            None,
        )
        return {
            "mac": self._mac,
            "ip": device.get("ip", "") if device else "",
            "hostname": device.get("hostname", "") if device else "",
            ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
        }

    async def async_added_to_hass(self) -> None:
        """Apply the custom Chinese friendly name from the registry."""
        await super().async_added_to_hass()
        registry = async_get_entity_registry(self.hass)
        registry_entry = registry.async_get(self.entity_id)
        if registry_entry and registry_entry.name != self._friendly:
            # HA >= 2026.11 renamed EntityRegistry.async_update to
            # async_update_entity.
            if hasattr(registry, "async_update_entity"):
                registry.async_update_entity(self.entity_id, name=self._friendly)
            else:  # pragma: no cover - older HA releases
                registry.async_update(self.entity_id, name=self._friendly)

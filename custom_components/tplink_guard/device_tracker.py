"""Device tracker entities for monitored terminals.

Each line of the monitored list file (``lists/monitored.txt`` inside the
integration folder) defines one terminal that gets its own device_tracker
entity showing whether it is online (home) or offline (not_home).

Line format: ``MAC, entity_id, 备注``. The second field decides the entity
id (``device_tracker.<entity_id>``) and the entity/friendly name. The third
field is a plain-text note for documentation only and is never sent to HA.
Both the entity id and the note are optional; a missing entity id is
derived from the router hostname (falling back to the MAC).
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
    registry = async_get_entity_registry(hass)
    entities: list[MonitoredTerminalTracker] = []
    for item in coordinator.monitored:
        entity = MonitoredTerminalTracker(coordinator, entry.entry_id, item)
        _pin_tracker_identity(registry, entry, entity)
        entities.append(entity)
    async_add_entities(entities)


def _pin_tracker_identity(registry, entry: ConfigEntry, entity) -> None:
    """Pin tracker entity ids and names in the registry.

    HA 2026.11 derives ids from "<device name> <entity name>" and ignores
    suggested_object_id, so the id is pinned explicitly here — the same
    mechanism as the guest sensor: a registry entry with an explicitly set
    id and name keeps both stable across restarts, even with a device
    attached. The entity id becomes device_tracker.<second TXT field>.
    """
    wanted = f"device_tracker.{entity._attr_name}"
    entry_obj = registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        entity.unique_id,
        config_entry=entry,
        original_name=entity._attr_name,
    )
    updates: dict[str, str] = {}
    if entry_obj.entity_id != wanted:
        updates["new_entity_id"] = wanted
    if entry_obj.name != entity._attr_name:
        updates["name"] = entity._attr_name
    if updates:
        try:
            registry.async_update_entity(entry_obj.entity_id, **updates)
        except Exception:  # noqa: BLE001 - e.g. id already taken
            _LOGGER.warning(
                "Could not pin tracker identity to %s", wanted, exc_info=True
            )


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

        # Entity id and friendly name both come from the second TXT field;
        # the third field is a comment only and never reaches HA.
        if custom_id:
            id_part = custom_id.split(".")[-1].strip()
        else:
            hostname = self._lookup_hostname()
            id_part = _slugify(hostname) if hostname else f"terminal_{self._mac[-6:].lower()}"
        self._attr_name = id_part or f"terminal_{self._mac[-6:].lower()}"
        # Without a suggested object id HA 2026.11 prepends the device
        # name ("TPLink Guard") to the generated entity id; the real id is
        # pinned by _pin_tracker_identity anyway.
        self._attr_suggested_object_id = self._attr_name

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

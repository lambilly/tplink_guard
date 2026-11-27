"""Device tracker entities for device terminals.

Each line of the device list file (``lists/device.txt`` inside the
integration folder) defines one terminal that gets its own device_tracker
entity showing whether it is online (home) or offline (not_home).

Line format: ``MAC, entity_name, display_name``. The second field builds
the entity id (``device_tracker.tplink_guard_<entity_name>``); the third
field is the friendly/display name (Chinese allowed). If the display name
is missing the entity name is shown; if the entity name is missing it is
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
    """Pin tracker entity ids and display names in the registry.

    HA 2026.11 ignores suggested_object_id and derives ids from
    "<device name> <entity name>"; a pure name change also regenerates the
    id. Pinning both the id and the display name explicitly (the same
    mechanism as the guest sensor) keeps them independent and stable:
    the id comes from the second TXT field, the display name from the
    third (Chinese allowed).
    """
    wanted = f"device_tracker.tplink_guard_{entity._attr_name}"
    entry_obj = registry.async_get_or_create(
        "device_tracker",
        DOMAIN,
        entity.unique_id,
        config_entry=entry,
        original_name=entity._friendly,
    )
    updates: dict[str, str] = {}
    if entry_obj.entity_id != wanted:
        updates["new_entity_id"] = wanted
    if entry_obj.name != entity._friendly:
        updates["name"] = entity._friendly
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
        display_name = str(item.get("name") or "").strip()

        # Second TXT field -> entity id (device_tracker.tplink_guard_<name>);
        # third TXT field -> friendly/display name, Chinese allowed.
        if custom_id:
            id_part = custom_id.split(".")[-1].strip()
        else:
            hostname = self._lookup_hostname()
            id_part = _slugify(hostname) if hostname else f"terminal_{self._mac[-6:].lower()}"
        self._attr_name = id_part or f"terminal_{self._mac[-6:].lower()}"
        self._friendly = display_name or self._attr_name
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
    def force_update(self) -> bool:
        """Disable forced updates so last_changed survives idle cycles.

        TrackerEntity returns ``not self.should_poll`` — True for us — which
        makes HA treat every coordinator write as a state change and resets
        last_changed (the "state duration" timer) every refresh even when
        the state stays home/not_home. Regular dedup behaviour is wanted
        here: the duration only restarts when the state really changes.
        """
        return False

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

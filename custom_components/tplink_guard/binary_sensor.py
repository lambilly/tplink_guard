"""Binary sensor entities for TPLink Guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_DEVICES, ATTR_LAST_UPDATE, DOMAIN
from .coordinator import TPLinkGuardCoordinator


# English object ids -> entity_id is <platform>.tplink_guard_<object_id>
OBJECT_IDS: dict[str, str] = {
    "unknown_present": "unknown_terminal_present",
    "watched_online": "phone_online",
}


@dataclass(frozen=True, kw_only=True)
class TPLinkGuardBinaryDescription(BinarySensorEntityDescription):
    """Describe a TPLink Guard binary sensor."""

    count_key: str
    devices_key: str


DESCRIPTIONS: tuple[TPLinkGuardBinaryDescription, ...] = (
    TPLinkGuardBinaryDescription(
        key="unknown_present",
        name="Unknown Terminal Present",
        icon="mdi:shield-alert",
        count_key="unknown_count",
        devices_key="unknown_devices",
    ),
    TPLinkGuardBinaryDescription(
        key="watched_online",
        name="Phone Online",
        icon="mdi:bell-outline",
        translation_key="watched_online",
        count_key="phone_online_count",
        devices_key="phone_online",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors from a config entry."""
    coordinator: TPLinkGuardCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TPLinkGuardBinarySensor(coordinator, entry.entry_id, description)
        for description in DESCRIPTIONS
    )


class TPLinkGuardBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Alert entity backed by a coordinator count."""

    entity_description: TPLinkGuardBinaryDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TPLinkGuardCoordinator,
        entry_id: str,
        description: TPLinkGuardBinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        # Diagnostic entities stay standalone (no device association):
        # HA 2026.11 builds the entity id as "<device name> <entity name>",
        # so a device would force the tplink_guard_ prefix. Without a
        # device the id is derived from the entity name alone.
        self._attr_device_info = None

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data or {}
        return bool(data.get(self.entity_description.count_key, 0))

    @property
    def icon(self) -> str:
        if self.entity_description.key == "watched_online":
            return "mdi:bell-ring" if self.is_on else "mdi:bell-outline"
        return self.entity_description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            ATTR_DEVICES: data.get(self.entity_description.devices_key, []),
            ATTR_LAST_UPDATE: data.get(ATTR_LAST_UPDATE),
        }

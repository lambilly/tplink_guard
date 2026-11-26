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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_DEVICES, ATTR_LAST_UPDATE, DOMAIN
from .coordinator import TPLinkGuardCoordinator

import logging

_LOGGER = logging.getLogger(__name__)


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
    registry = async_get_entity_registry(hass)
    entities = []
    for description in DESCRIPTIONS:
        entity = TPLinkGuardBinarySensor(coordinator, entry.entry_id, description)
        _pin_binary_identity(registry, entry, entity)
        entities.append(entity)
    async_add_entities(entities)


def _pin_binary_identity(registry, entry: ConfigEntry, entity) -> None:
    """Pin binary sensor display names in the registry.

    Entities are attached to the TPLink Guard device (visible in the
    integration/device page). A pinned registry name keeps the entity_id
    stable across restarts — HA 2026.11 only regenerates auto-generated
    ids, not user-named ones (same mechanism as the device_trackers).
    Entity ids use the natural prefixed form
    binary_sensor.tplink_guard_<name> (as referenced by Node-RED).
    """
    name = entity.entity_description.name
    entry_obj = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        entity.unique_id,
        config_entry=entry,
        original_name=name,
    )
    if entry_obj.name != name:
        try:
            registry.async_update_entity(entry_obj.entity_id, name=name)
        except Exception:  # noqa: BLE001 - e.g. id already taken
            _LOGGER.warning(
                "Could not pin binary sensor name for %s",
                entity.entity_description.key,
                exc_info=True,
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
        # Attached to the integration device so the entities show up under
        # the TPLink Guard device page; ids stay pinned via
        # _pin_binary_identity.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="TPLink Guard",
            manufacturer="TP-Link",
        )

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

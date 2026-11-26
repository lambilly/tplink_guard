"""The TPLink Guard integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RouterClient
from .const import (
    CONF_MATCH_BY,
    CONF_WATCHLIST_PATH,
    CONF_WHITELIST_PATH,
    DEFAULT_MATCH_BY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    PLATFORMS,
    SERVICE_RELOAD_WHITELIST,
)
from .coordinator import TPLinkGuardCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration-level services."""

    async def _async_reload_whitelist(call) -> None:
        for coordinator in hass.data.get(DOMAIN, {}).values():
            await coordinator.async_reload_whitelist()

    if not hass.services.has_service(DOMAIN, SERVICE_RELOAD_WHITELIST):
        hass.services.async_register(
            DOMAIN, SERVICE_RELOAD_WHITELIST, _async_reload_whitelist
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TPLink Guard from a config entry."""
    session = async_get_clientsession(hass)
    client = RouterClient(
        session,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        timeout=DEFAULT_TIMEOUT,
    )
    options = entry.options
    coordinator = TPLinkGuardCoordinator(
        hass,
        client,
        whitelist_path=options.get(
            CONF_WHITELIST_PATH,
            entry.data.get(CONF_WHITELIST_PATH),
        ),
        watchlist_path=options.get(
            CONF_WATCHLIST_PATH,
            entry.data.get(CONF_WATCHLIST_PATH),
        ),
        match_by=options.get(CONF_MATCH_BY, entry.data.get(CONF_MATCH_BY, DEFAULT_MATCH_BY)),
        scan_interval=options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded

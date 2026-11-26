"""Config flow for the TPLink Guard integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlowWithReload
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .api import RouterAuthError, RouterClient, RouterConnectionError
from .const import (
    CONF_MATCH_BY,
    CONF_MONITORED_PATH,
    CONF_WATCHLIST_PATH,
    CONF_WHITELIST_PATH,
    DEFAULT_HOST,
    DEFAULT_LISTS_RELDIR,
    DEFAULT_MATCH_BY,
    DEFAULT_MONITORED_FILENAME,
    DEFAULT_PHONE_FILENAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DEFAULT_WHITELIST_FILENAME,
    DOMAIN,
    MATCH_MODES,
)

_LOGGER = logging.getLogger(__name__)


def _user_schema(
    default_whitelist: str, default_phone: str, default_monitored: str
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
            vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=86400)
            ),
            vol.Optional(
                CONF_WHITELIST_PATH, default=default_whitelist
            ): str,
            vol.Optional(
                CONF_WATCHLIST_PATH, default=default_phone
            ): str,
            vol.Optional(
                CONF_MONITORED_PATH, default=default_monitored
            ): str,
            vol.Optional(CONF_MATCH_BY, default=DEFAULT_MATCH_BY): vol.In(MATCH_MODES),
        }
    )


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=86400)),
            vol.Optional(
                CONF_WHITELIST_PATH,
                default=current.get(CONF_WHITELIST_PATH, ""),
            ): str,
            vol.Optional(
                CONF_WATCHLIST_PATH,
                default=current.get(CONF_WATCHLIST_PATH, ""),
            ): str,
            vol.Optional(
                CONF_MONITORED_PATH,
                default=current.get(CONF_MONITORED_PATH, ""),
            ): str,
            vol.Optional(
                CONF_MATCH_BY,
                default=current.get(CONF_MATCH_BY, DEFAULT_MATCH_BY),
            ): vol.In(MATCH_MODES),
        }
    )


class TPLinkGuardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        default_whitelist = self.hass.config.path(DEFAULT_LISTS_RELDIR, DEFAULT_WHITELIST_FILENAME)
        default_phone = self.hass.config.path(DEFAULT_LISTS_RELDIR, DEFAULT_PHONE_FILENAME)
        default_monitored = self.hass.config.path(DEFAULT_LISTS_RELDIR, DEFAULT_MONITORED_FILENAME)

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            )
            self._abort_if_unique_id_configured()

            client = RouterClient(
                async_get_clientsession(self.hass),
                host=user_input[CONF_HOST],
                port=user_input[CONF_PORT],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await asyncio.wait_for(client.async_test_connection(), timeout=20)
            except RouterAuthError:
                errors["base"] = "invalid_auth"
            except (RouterConnectionError, asyncio.TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Unexpected error validating router connection")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"TPLink Guard ({user_input[CONF_HOST]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(
                default_whitelist, default_phone, default_monitored
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TPLinkGuardOptionsFlow:
        return TPLinkGuardOptionsFlow()


class TPLinkGuardOptionsFlow(OptionsFlowWithReload):
    """Handle editable options; HA reloads the entry when they change."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(current)
        )

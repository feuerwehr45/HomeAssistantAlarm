"""Config flow for the GroupAlarm integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GroupAlarmAuthError,
    GroupAlarmClient,
    GroupAlarmConnectionError,
    GroupAlarmError,
    GroupAlarmNotFoundError,
)
from .const import CONF_UUID, DEFAULT_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_BASE_URL)
            ): str,
            vol.Required(CONF_UUID, default=defaults.get(CONF_UUID, "")): str,
            vol.Required(CONF_API_KEY, default=""): str,
        }
    )


async def _async_validate(
    hass, base_url: str, uuid: str, api_key: str
) -> dict[str, Any]:
    """Call /status and return the response, or raise a GroupAlarmError subclass."""
    session = async_get_clientsession(hass)
    client = GroupAlarmClient(session, base_url, uuid, api_key)
    return await client.async_get_status()


class GroupAlarmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GroupAlarm."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = user_input[CONF_HOST].rstrip("/")
            uuid = user_input[CONF_UUID].strip()
            api_key = user_input[CONF_API_KEY].strip()

            await self.async_set_unique_id(uuid)
            self._abort_if_unique_id_configured()

            try:
                status = await _async_validate(self.hass, base_url, uuid, api_key)
            except GroupAlarmAuthError:
                errors["base"] = "invalid_auth"
            except GroupAlarmNotFoundError:
                errors["base"] = "not_found"
            except (GroupAlarmConnectionError, GroupAlarmError):
                errors["base"] = "cannot_connect"
            else:
                title = status.get("connectionName") or "GroupAlarm"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: base_url,
                        CONF_UUID: uuid,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input), errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauth triggered when the API key is rejected or the connection vanished."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            base_url = reauth_entry.data[CONF_HOST]
            uuid = reauth_entry.data[CONF_UUID]
            api_key = user_input[CONF_API_KEY].strip()

            try:
                await _async_validate(self.hass, base_url, uuid, api_key)
            except GroupAlarmAuthError:
                errors["base"] = "invalid_auth"
            except GroupAlarmNotFoundError:
                errors["base"] = "not_found"
            except (GroupAlarmConnectionError, GroupAlarmError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={**reauth_entry.data, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
            description_placeholders={"uuid": reauth_entry.data[CONF_UUID]},
        )

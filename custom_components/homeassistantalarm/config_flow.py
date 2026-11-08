"""Config flow for the HomeAssistantAlarm integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GroupAlarmAuthError,
    GroupAlarmClient,
    GroupAlarmConnectionError,
    GroupAlarmError,
    GroupAlarmNotFoundError,
    GroupAlarmRateLimitError,
)
from .const import (
    CONF_MAX_AGE_MINUTES,
    CONF_TEST_ACTIVATES_STATUS,
    CONF_UUID,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_TEST_ACTIVATES_STATUS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _connection_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_BASE_URL)
            ): str,
            vol.Required(CONF_UUID, default=defaults.get(CONF_UUID, "")): str,
            # never pre-filled: the key is a secret
            vol.Required(CONF_API_KEY): str,
        }
    )


def _clean(user_input: dict[str, Any]) -> tuple[str, str, str]:
    return (
        user_input[CONF_HOST].strip().rstrip("/"),
        user_input[CONF_UUID].strip(),
        user_input[CONF_API_KEY].strip(),
    )


async def _async_validate(
    hass: HomeAssistant, base_url: str, uuid: str, api_key: str
) -> dict[str, Any]:
    """Call /status and return the response, or raise a GroupAlarmError subclass."""
    session = async_get_clientsession(hass)
    client = GroupAlarmClient(session, base_url, uuid, api_key)
    return await client.async_get_status()


async def _async_validate_errors(
    hass: HomeAssistant, base_url: str, uuid: str, api_key: str
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Validate and translate errors to form error keys."""
    try:
        return await _async_validate(hass, base_url, uuid, api_key), {}
    except GroupAlarmAuthError:
        return None, {"base": "invalid_auth"}
    except GroupAlarmNotFoundError:
        return None, {"base": "not_found"}
    except GroupAlarmRateLimitError:
        return None, {"base": "rate_limited"}
    except (GroupAlarmConnectionError, GroupAlarmError):
        return None, {"base": "cannot_connect"}


class GroupAlarmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HomeAssistantAlarm."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return HomeAssistantAlarmOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url, uuid, api_key = _clean(user_input)

            await self.async_set_unique_id(uuid)
            self._abort_if_unique_id_configured()

            status, errors = await _async_validate_errors(
                self.hass, base_url, uuid, api_key
            )
            if status is not None:
                title = status.get("connectionName") or "HomeAssistantAlarm"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: base_url,
                        CONF_UUID: uuid,
                        CONF_API_KEY: api_key,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=_connection_schema(user_input), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Change server, UUID and key of the existing entry.

        Keeps entry_id and with it the overall entities and their entity ids
        (unique ids are derived from the entry_id). The stored alarm state is
        bound to server + UUID and is discarded on the reload if either
        changed (see GroupAlarmConnection.async_start).
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url, uuid, api_key = _clean(user_input)

            if any(
                other.unique_id == uuid and other.entry_id != entry.entry_id
                for other in self._async_current_entries(include_ignore=False)
            ):
                return self.async_abort(reason="already_configured")

            status, errors = await _async_validate_errors(
                self.hass, base_url, uuid, api_key
            )
            if status is not None:
                self.hass.config_entries.async_update_entry(entry, unique_id=uuid)
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_HOST: base_url,
                        CONF_UUID: uuid,
                        CONF_API_KEY: api_key,
                    },
                    reason="reconfigure_successful",
                )

        defaults = user_input or {
            CONF_HOST: entry.data.get(CONF_HOST, DEFAULT_BASE_URL),
            CONF_UUID: entry.data.get(CONF_UUID, ""),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(defaults),
            errors=errors,
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

            status, errors = await _async_validate_errors(
                self.hass, base_url, uuid, api_key
            )
            if status is not None:
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


class HomeAssistantAlarmOptionsFlow(config_entries.OptionsFlow):
    """Options: max. alarm age for events, whether tests set the status."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            # Read live by the connection, no reload (and no new stream) needed
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MAX_AGE_MINUTES,
                        default=options.get(
                            CONF_MAX_AGE_MINUTES, DEFAULT_MAX_AGE_MINUTES
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
                    vol.Required(
                        CONF_TEST_ACTIVATES_STATUS,
                        default=options.get(
                            CONF_TEST_ACTIVATES_STATUS, DEFAULT_TEST_ACTIVATES_STATUS
                        ),
                    ): bool,
                }
            ),
        )

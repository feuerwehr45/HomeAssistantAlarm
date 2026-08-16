"""The GroupAlarm integration.

Connects to a GroupAlarm server as documented in
docs/homeassistant-integration-api.md: an initial catch-up poll followed by
a long-lived SSE stream for realtime alarms, with automatic reconnect.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GroupAlarmAuthError,
    GroupAlarmClient,
    GroupAlarmConnectionError,
    GroupAlarmError,
    GroupAlarmNotFoundError,
)
from .const import CONF_UUID, PLATFORMS
from .coordinator import GroupAlarmConnection

@dataclass
class GroupAlarmRuntimeData:
    """Data stored on the config entry while it is loaded."""

    connection: GroupAlarmConnection
    status: dict


GroupAlarmConfigEntry = ConfigEntry[GroupAlarmRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: GroupAlarmConfigEntry) -> bool:
    """Set up GroupAlarm from a config entry."""
    session = async_get_clientsession(hass)
    client = GroupAlarmClient(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_UUID],
        entry.data[CONF_API_KEY],
    )

    try:
        status = await client.async_get_status()
    except GroupAlarmAuthError as err:
        raise ConfigEntryAuthFailed("Invalid API key") from err
    except GroupAlarmNotFoundError as err:
        raise ConfigEntryAuthFailed("Connection no longer exists") from err
    except (GroupAlarmConnectionError, GroupAlarmError) as err:
        raise ConfigEntryNotReady(f"Could not reach GroupAlarm server: {err}") from err

    connection = GroupAlarmConnection(hass, entry, client)
    await connection.async_start()

    entry.runtime_data = GroupAlarmRuntimeData(connection=connection, status=status)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GroupAlarmConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.connection.async_stop()
    return unload_ok

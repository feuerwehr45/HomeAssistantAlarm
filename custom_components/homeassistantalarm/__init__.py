"""The HomeAssistantAlarm integration.

Connects to the AlarmServer as documented in
docs/homeassistant-integration-api.md: an initial catch-up poll followed by
a long-lived SSE stream for realtime alarms, with automatic reconnect.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GroupAlarmAuthError,
    GroupAlarmClient,
    GroupAlarmConnectionError,
    GroupAlarmError,
    GroupAlarmNotFoundError,
    GroupAlarmRateLimitError,
)
from .const import CONF_UUID, DOMAIN, PLATFORMS, org_device_identifier
from .coordinator import GroupAlarmConnection


@dataclass
class GroupAlarmRuntimeData:
    """Data stored on the config entry while it is loaded."""

    connection: GroupAlarmConnection


GroupAlarmConfigEntry = ConfigEntry[GroupAlarmRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: GroupAlarmConfigEntry) -> bool:
    """Set up HomeAssistantAlarm from a config entry."""
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
    except GroupAlarmRateLimitError as err:
        # HA retries ConfigEntryNotReady with its own backoff - no reauth
        raise ConfigEntryNotReady(f"Server is rate limiting: {err}") from err
    except (GroupAlarmConnectionError, GroupAlarmError) as err:
        raise ConfigEntryNotReady(f"Could not reach the AlarmServer: {err}") from err

    connection = GroupAlarmConnection(
        hass, entry, client, initial_organizations=status.get("subscribedOrganizations", [])
    )
    await connection.async_start()

    entry.runtime_data = GroupAlarmRuntimeData(connection=connection)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GroupAlarmConfigEntry) -> bool:
    """Unload a config entry.

    async_stop() waits until the SSE connection is closed, so a reload or
    reconfigure never leaves an old stream behind next to the new one.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.connection.async_stop()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GroupAlarmConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting organization devices that are no longer subscribed.

    The connection device itself (overall sensors) cannot be removed, nor can
    the device of an organization that is still subscribed.
    """
    subscribed: set[str] = set()
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is not None:
        subscribed = runtime_data.connection.organization_uuids
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == entry.entry_id:
            return False
        if any(
            identifier == org_device_identifier(entry.entry_id, org_uuid)
            for org_uuid in subscribed
        ):
            return False
    return True

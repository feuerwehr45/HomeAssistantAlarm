"""Binary sensor platform for HomeAssistantAlarm.

Just the connection's own connectivity status. Kept as a separate,
deliberately always-available entity (see GroupAlarmConnectivitySensor)
instead of relying on users to notice that all the alarm sensors quietly
turned unavailable at once.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GroupAlarmConfigEntry
from .const import DOMAIN, SIGNAL_AVAILABILITY


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GroupAlarmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the connectivity binary sensor for this config entry."""
    async_add_entities([GroupAlarmConnectivitySensor(entry, entry.runtime_data.connection)])


class GroupAlarmConnectivitySensor(BinarySensorEntity):
    """Whether the poll/stream connection to the GroupAlarm server is up.

    Deliberately does NOT tie its own `available` to
    GroupAlarmConnection.available the way the sensor.py entities do -
    those go unavailable exactly when the connection drops, which is
    correct for "we don't have fresh alarm data right now" but would mean
    there's no entity left to actually show that the connection is down.
    This one always reports something (on = connected, off = not).
    """

    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, connection) -> None:
        self._entry = entry
        self._connection = connection
        self._attr_name = "Verbindung"
        self._attr_unique_id = f"{entry.entry_id}_connectivity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="HomeAssistantAlarm",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return self._connection.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AVAILABILITY.format(entry_id=self._entry.entry_id),
                self._handle_availability,
            )
        )

    @callback
    def _handle_availability(self, _available: bool) -> None:
        self.async_write_ha_state()

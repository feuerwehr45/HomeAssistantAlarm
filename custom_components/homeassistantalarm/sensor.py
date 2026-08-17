"""Sensor platform for HomeAssistantAlarm.

Alarms are primarily surfaced as the `homeassistantalarm_alarm` HA event
(fired by the GroupAlarmConnection for every new alarm) - that's what automations
should trigger on. These sensors just mirror the last alarm text per
organization (and overall) for dashboards/history, as suggested in
docs/homeassistant-integration-api.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import GroupAlarmConfigEntry
from .const import (
    ALARM_STATUS_OVERALL_KEY,
    DOMAIN,
    SIGNAL_ALARM,
    SIGNAL_ALARM_STATUS,
    SIGNAL_AVAILABILITY,
    SIGNAL_NEW_ORGANIZATIONS,
    STATE_ALARM,
    STATE_NO_ALARM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GroupAlarmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GroupAlarm sensors: one overall + one per subscribed organization.

    New organizations added to the connection after setup are picked up in
    the background (see GroupAlarmConnection._async_refresh_organizations)
    and get their sensor added here too, without requiring a reload.
    """
    runtime = entry.runtime_data
    connection = runtime.connection
    known_org_uuids: set[str] = set()

    entities: list[SensorEntity] = [
        GroupAlarmLastAlarmSensor(entry, connection),
        GroupAlarmOverallStatusSensor(entry, connection),
    ]
    for org in connection.organizations:
        entities.append(
            GroupAlarmOrganizationSensor(entry, connection, org["uuid"], org["name"])
        )
        entities.append(
            GroupAlarmOrganizationStatusSensor(entry, connection, org["uuid"], org["name"])
        )
        known_org_uuids.add(org["uuid"])

    async_add_entities(entities)

    @callback
    def _handle_new_organizations(new_orgs: list[dict[str, Any]]) -> None:
        new_entities: list[SensorEntity] = []
        for org in new_orgs:
            if org["uuid"] in known_org_uuids:
                continue
            new_entities.append(
                GroupAlarmOrganizationSensor(entry, connection, org["uuid"], org["name"])
            )
            new_entities.append(
                GroupAlarmOrganizationStatusSensor(entry, connection, org["uuid"], org["name"])
            )
        known_org_uuids.update(org["uuid"] for org in new_orgs)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_NEW_ORGANIZATIONS.format(entry_id=entry.entry_id),
            _handle_new_organizations,
        )
    )


def _alarm_timestamp(alarm: dict[str, Any] | None) -> datetime | None:
    """Parse the alarm's ISO8601 `timestamp` for use as a sensor's native_value.

    Used instead of the alarm text as the last-alarm sensors' state: a
    timestamp is always far below HA's 255-character entity-state limit,
    whereas an alarm message can be arbitrarily long (see the state-length
    bug writeup in docs/DEVELOPMENT.md) - this rules the problem out
    structurally instead of just truncating. The full text remains
    available via the `full_message` attribute, and the sensor being
    device_class=TIMESTAMP also means the UI shows a nicely
    localized/relative "last alarm was at" value directly.
    """
    if not alarm:
        return None
    return dt_util.parse_datetime(alarm.get("timestamp", ""))


def _alarm_attributes(alarm: dict[str, Any] | None) -> dict[str, Any]:
    if not alarm:
        return {}
    attributes: dict[str, Any] = {
        "id": alarm.get("id"),
        "organization": alarm.get("organization"),
        "organization_uuid": alarm.get("organizationUuid"),
        "timestamp": alarm.get("timestamp"),
        "full_message": alarm.get("message"),
    }
    alarm_data = alarm.get("alarmData")
    if alarm_data:
        attributes.update(
            {
                "code": alarm_data.get("code"),
                "stichwort": alarm_data.get("stichwort"),
                "adresse": alarm_data.get("adresse"),
                "ort": alarm_data.get("ort"),
                "zusatz": alarm_data.get("zusatz"),
                "fahrzeuge": alarm_data.get("fahrzeuge"),
                "lat": alarm_data.get("lat"),
                "lon": alarm_data.get("lon"),
                "maps_link": alarm_data.get("maps_link"),
                "prioritaet": alarm_data.get("prioritaet"),
                "datum": alarm_data.get("datum"),
                "uhrzeit": alarm_data.get("uhrzeit"),
            }
        )
    return attributes


class _GroupAlarmBaseSensor(SensorEntity):
    """Common device grouping for all sensors of one config entry."""

    _attr_should_poll = False
    _attr_icon = "mdi:alarm-light"

    def __init__(self, entry: ConfigEntry, connection) -> None:
        self._entry = entry
        self._connection = connection
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="HomeAssistantAlarm",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ALARM.format(entry_id=self._entry.entry_id),
                self._handle_alarm,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AVAILABILITY.format(entry_id=self._entry.entry_id),
                self._handle_availability,
            )
        )

    @property
    def available(self) -> bool:
        return self._connection.available

    @callback
    def _handle_availability(self, _available: bool) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_alarm(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError


class GroupAlarmLastAlarmSensor(_GroupAlarmBaseSensor):
    """Shows the most recent alarm across all subscribed organizations."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: ConfigEntry, connection) -> None:
        super().__init__(entry, connection)
        self._attr_name = "Letzter Alarm"
        self._attr_unique_id = f"{entry.entry_id}_last_alarm"

    @property
    def native_value(self) -> datetime | None:
        return _alarm_timestamp(self._connection.last_alarm)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _alarm_attributes(self._connection.last_alarm)

    @callback
    def _handle_alarm(self, payload: dict[str, Any]) -> None:
        self.async_write_ha_state()


class GroupAlarmOrganizationSensor(_GroupAlarmBaseSensor):
    """Shows the most recent alarm for a single organization."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, entry: ConfigEntry, connection, org_uuid: str, org_name: str
    ) -> None:
        super().__init__(entry, connection)
        self._org_uuid = org_uuid
        self._attr_name = f"{org_name} letzter Alarm"
        self._attr_unique_id = f"{entry.entry_id}_{org_uuid}_last_alarm"

    @property
    def native_value(self) -> datetime | None:
        return _alarm_timestamp(self._connection.last_alarm_by_org.get(self._org_uuid))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _alarm_attributes(self._connection.last_alarm_by_org.get(self._org_uuid))

    @callback
    def _handle_alarm(self, payload: dict[str, Any]) -> None:
        if payload.get("organizationUuid") == self._org_uuid:
            self.async_write_ha_state()


class _GroupAlarmStatusSensorBase(_GroupAlarmBaseSensor):
    """Common logic for the "Alarm" / "Kein Alarm" status sensors.

    Unlike the last-alarm text sensors, these don't react to SIGNAL_ALARM
    directly - GroupAlarmConnection._activate_alarm_status() fires
    SIGNAL_ALARM_STATUS both when a key becomes active and (after
    ALARM_ACTIVE_DURATION) when it auto-resets, which is all the state
    changes this sensor has.
    """

    _attr_icon = "mdi:alarm-light"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ALARM_STATUS.format(entry_id=self._entry.entry_id),
                self._handle_status,
            )
        )

    @callback
    def _handle_alarm(self, payload: dict[str, Any]) -> None:
        pass  # state only changes via SIGNAL_ALARM_STATUS, see class docstring

    @callback
    def _handle_status(self, key: str) -> None:
        raise NotImplementedError

    def _native_value_for(self, key: str) -> str:
        return STATE_ALARM if key in self._connection.active_alarm_keys else STATE_NO_ALARM


class GroupAlarmOverallStatusSensor(_GroupAlarmStatusSensorBase):
    """"Alarm"/"Kein Alarm" across all subscribed organizations."""

    def __init__(self, entry: ConfigEntry, connection) -> None:
        super().__init__(entry, connection)
        self._attr_name = "Alarmstatus"
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str:
        return self._native_value_for(ALARM_STATUS_OVERALL_KEY)

    @callback
    def _handle_status(self, key: str) -> None:
        if key == ALARM_STATUS_OVERALL_KEY:
            self.async_write_ha_state()


class GroupAlarmOrganizationStatusSensor(_GroupAlarmStatusSensorBase):
    """"Alarm"/"Kein Alarm" for a single organization."""

    def __init__(
        self, entry: ConfigEntry, connection, org_uuid: str, org_name: str
    ) -> None:
        super().__init__(entry, connection)
        self._org_uuid = org_uuid
        self._attr_name = f"{org_name} Alarmstatus"
        self._attr_unique_id = f"{entry.entry_id}_{org_uuid}_status"

    @property
    def native_value(self) -> str:
        return self._native_value_for(self._org_uuid)

    @callback
    def _handle_status(self, key: str) -> None:
        if key == self._org_uuid:
            self.async_write_ha_state()

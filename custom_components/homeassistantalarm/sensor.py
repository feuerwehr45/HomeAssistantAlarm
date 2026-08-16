"""Sensor platform for HomeAssistantAlarm.

Alarms are primarily surfaced as the `homeassistantalarm_alarm` HA event
(fired by the GroupAlarmConnection for every new alarm) - that's what automations
should trigger on. These sensors just mirror the last alarm text per
organization (and overall) for dashboards/history, as suggested in
docs/homeassistant-integration-api.md.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GroupAlarmConfigEntry
from .const import DOMAIN, SIGNAL_ALARM, SIGNAL_AVAILABILITY, SIGNAL_NEW_ORGANIZATIONS


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

    entities: list[SensorEntity] = [GroupAlarmLastAlarmSensor(entry, connection)]
    for org in connection.organizations:
        entities.append(
            GroupAlarmOrganizationSensor(entry, connection, org["uuid"], org["name"])
        )
        known_org_uuids.add(org["uuid"])

    async_add_entities(entities)

    @callback
    def _handle_new_organizations(new_orgs: list[dict[str, Any]]) -> None:
        new_entities = [
            GroupAlarmOrganizationSensor(entry, connection, org["uuid"], org["name"])
            for org in new_orgs
            if org["uuid"] not in known_org_uuids
        ]
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


def _alarm_attributes(alarm: dict[str, Any] | None) -> dict[str, Any]:
    if not alarm:
        return {}
    return {
        "id": alarm.get("id"),
        "organization": alarm.get("organization"),
        "organization_uuid": alarm.get("organizationUuid"),
        "timestamp": alarm.get("timestamp"),
        "raw_alarm": alarm.get("rawAlarm"),
    }


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

    def __init__(self, entry: ConfigEntry, connection) -> None:
        super().__init__(entry, connection)
        self._attr_name = "Letzter Alarm"
        self._attr_unique_id = f"{entry.entry_id}_last_alarm"

    @property
    def native_value(self) -> str | None:
        alarm = self._connection.last_alarm
        return alarm.get("message") if alarm else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _alarm_attributes(self._connection.last_alarm)

    @callback
    def _handle_alarm(self, payload: dict[str, Any]) -> None:
        self.async_write_ha_state()


class GroupAlarmOrganizationSensor(_GroupAlarmBaseSensor):
    """Shows the most recent alarm for a single organization."""

    def __init__(
        self, entry: ConfigEntry, connection, org_uuid: str, org_name: str
    ) -> None:
        super().__init__(entry, connection)
        self._org_uuid = org_uuid
        self._attr_name = f"{org_name} letzter Alarm"
        self._attr_unique_id = f"{entry.entry_id}_{org_uuid}_last_alarm"

    @property
    def native_value(self) -> str | None:
        alarm = self._connection.last_alarm_by_org.get(self._org_uuid)
        return alarm.get("message") if alarm else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _alarm_attributes(self._connection.last_alarm_by_org.get(self._org_uuid))

    @callback
    def _handle_alarm(self, payload: dict[str, Any]) -> None:
        if payload.get("organizationUuid") == self._org_uuid:
            self.async_write_ha_state()

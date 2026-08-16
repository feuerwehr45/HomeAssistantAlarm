"""Device triggers for HomeAssistantAlarm.

Makes "new alarm" discoverable in the automation editor's device trigger
picker (Trigger -> Device -> the HomeAssistantAlarm connection), instead of
requiring users to manually configure a generic Event trigger with the
`homeassistantalarm_alarm` event type. Optionally restricts the trigger to
one subscribed organization.

Delegates to the core "event" trigger platform, the documented pattern for
device triggers backed by an HA event (see Home Assistant's device
automation developer docs) - not yet exercised against a running HA
instance, see docs/DEVELOPMENT.md.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr, selector
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_ALARM

CONF_ORGANIZATION_UUID = "organization_uuid"

TRIGGER_TYPE_ALARM = "alarm"
TRIGGER_TYPES = {TRIGGER_TYPE_ALARM}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(CONF_ORGANIZATION_UUID): cv.string,
    }
)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List device triggers for a HomeAssistantAlarm connection."""
    registry = dr.async_get(hass)
    if registry.async_get(device_id) is None:
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_TYPE_ALARM,
        }
    ]


def _organizations_for_device(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """Look up the subscribed organizations of the config entry behind a device."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return []
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is not None:
            return runtime_data.connection.organizations
    return []


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Offer an optional organization filter, populated from the config entry."""
    organizations = _organizations_for_device(hass, config[CONF_DEVICE_ID])
    if not organizations:
        return {"extra_fields": vol.Schema({})}
    return {
        "extra_fields": vol.Schema(
            {
                vol.Optional(CONF_ORGANIZATION_UUID): selector.selector(
                    {
                        "select": {
                            "options": [
                                {"value": org["uuid"], "label": org["name"]}
                                for org in organizations
                            ],
                            "mode": "dropdown",
                        }
                    }
                )
            }
        )
    }


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action,
    trigger_info,
) -> CALLBACK_TYPE:
    """Attach the device trigger by delegating to the generic event trigger."""
    config = TRIGGER_SCHEMA(config)

    event_data: dict[str, Any] = {}
    if CONF_ORGANIZATION_UUID in config:
        event_data["organizationUuid"] = config[CONF_ORGANIZATION_UUID]

    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_ALARM,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )

"""Constants for the HomeAssistantAlarm integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "homeassistantalarm"

CONF_UUID = "uuid"

DEFAULT_BASE_URL = "https://alarm.feuerwehr45.de"
DEFAULT_POLL_LIMIT = 50

# Options (options flow). Read live from entry.options, no reload needed.
CONF_MAX_AGE_MINUTES = "max_age_minutes"
# Same default as `alarm.maxAgeMinutes` on the AlarmServer. 0 disables the check.
DEFAULT_MAX_AGE_MINUTES = 15
CONF_TEST_ACTIVATES_STATUS = "test_activates_status"
# Agreed with Thomas (2026-11-08): test alarms set the status sensors by
# default, so automations on the status can be tried out. Automations that
# must not react to tests filter on the `test` field themselves.
DEFAULT_TEST_ACTIVATES_STATUS = True

# Further platforms (e.g. Platform.BUTTON for the alarm monitors planned with
# AlarmServer M10b) only need to be added here plus a module of that name.
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

EVENT_ALARM = "homeassistantalarm_alarm"
EVENT_NOTIFICATION = "homeassistantalarm_notification"

SIGNAL_ALARM = "homeassistantalarm_alarm_{entry_id}"
SIGNAL_AVAILABILITY = "homeassistantalarm_availability_{entry_id}"
SIGNAL_NEW_ORGANIZATIONS = "homeassistantalarm_new_organizations_{entry_id}"
SIGNAL_ALARM_STATUS = "homeassistantalarm_status_{entry_id}"

ORGANIZATION_REFRESH_INTERVAL = timedelta(minutes=15)

MAX_RECENT_IDS = 50
MIN_BACKOFF = 1
MAX_BACKOFF = 30
# Wait after the server locked us out (too many failed logins per IP and
# minute) when no Retry-After is known, e.g. for the SSE `error` event.
RATE_LIMIT_WAIT = 60
STREAM_HEARTBEAT_TIMEOUT = 60  # server heartbeat is every 25s

STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = "homeassistantalarm_{entry_id}"

# How long the per-organization/overall "Alarmstatus" sensor stays on
# STATE_ALARM after a new alarm before auto-resetting to STATE_NO_ALARM. A
# new alarm for the same key restarts this window rather than stacking.
ALARM_ACTIVE_DURATION = timedelta(minutes=5)
ALARM_STATUS_OVERALL_KEY = "overall"
STATE_ALARM = "Alarm"
STATE_NO_ALARM = "Kein Alarm"


def org_device_identifier(entry_id: str, org_uuid: str) -> str:
    """Device identifier of the per-organization device of a config entry."""
    return f"{entry_id}_{org_uuid}"

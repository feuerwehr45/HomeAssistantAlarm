"""Constants for the GroupAlarm integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "homeassistantalarm"

CONF_UUID = "uuid"

DEFAULT_BASE_URL = "https://api.groupalarm.org"
DEFAULT_POLL_LIMIT = 50

PLATFORMS = [Platform.SENSOR]

EVENT_ALARM = "homeassistantalarm_alarm"

SIGNAL_ALARM = "homeassistantalarm_alarm_{entry_id}"
SIGNAL_AVAILABILITY = "homeassistantalarm_availability_{entry_id}"
SIGNAL_NEW_ORGANIZATIONS = "homeassistantalarm_new_organizations_{entry_id}"

ORGANIZATION_REFRESH_INTERVAL = timedelta(minutes=15)

MAX_RECENT_IDS = 50
MIN_BACKOFF = 1
MAX_BACKOFF = 30
STREAM_HEARTBEAT_TIMEOUT = 60  # server heartbeat is every 25s

STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = "homeassistantalarm_{entry_id}"

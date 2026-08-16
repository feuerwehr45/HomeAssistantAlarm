"""Constants for the GroupAlarm integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "groupalarm"

CONF_UUID = "uuid"

DEFAULT_BASE_URL = "https://api.groupalarm.org"
DEFAULT_POLL_LIMIT = 50

PLATFORMS = [Platform.SENSOR]

EVENT_ALARM = "groupalarm_alarm"

SIGNAL_ALARM = "groupalarm_alarm_{entry_id}"
SIGNAL_AVAILABILITY = "groupalarm_availability_{entry_id}"

MAX_RECENT_IDS = 50
MIN_BACKOFF = 1
MAX_BACKOFF = 30
STREAM_HEARTBEAT_TIMEOUT = 60  # server heartbeat is every 25s

STORAGE_VERSION = 1
STORAGE_KEY_TEMPLATE = "groupalarm_{entry_id}"

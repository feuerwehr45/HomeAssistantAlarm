"""Background push connection to the AlarmServer.

Implements the strategy documented in docs/homeassistant-integration-api.md:
poll to catch up on (re)connect, then hold the SSE stream open for realtime
events, deduplicating by alarm id and reconnecting with exponential backoff.

Rules on top of the plain API contract (see docs/DEVELOPMENT.md):
- Without a stored state (new entry, other server/UUID) the first /poll only
  takes over `latestId` and fills the sensors silently - no event, no status.
- The stored state is bound to base URL + UUID; a mismatch discards it.
- Alarms older than the `max_age_minutes` option only update the sensors.
- The profile probe (`id <= 0`) is neither deduplicated nor used as
  `latest_id`, and its random organization is not stored per organization.
- Rate limiting (HTTP 429 / lock-out `error` event) waits, never reauths.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import (
    GroupAlarmAuthError,
    GroupAlarmClient,
    GroupAlarmConnectionError,
    GroupAlarmError,
    GroupAlarmNotFoundError,
    GroupAlarmRateLimitError,
)
from .const import (
    ALARM_ACTIVE_DURATION,
    ALARM_STATUS_OVERALL_KEY,
    CONF_MAX_AGE_MINUTES,
    CONF_TEST_ACTIVATES_STATUS,
    DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_POLL_LIMIT,
    DEFAULT_TEST_ACTIVATES_STATUS,
    EVENT_ALARM,
    EVENT_NOTIFICATION,
    MAX_BACKOFF,
    MAX_RECENT_IDS,
    MIN_BACKOFF,
    ORGANIZATION_REFRESH_INTERVAL,
    RATE_LIMIT_WAIT,
    SIGNAL_ALARM,
    SIGNAL_ALARM_STATUS,
    SIGNAL_AVAILABILITY,
    SIGNAL_NEW_ORGANIZATIONS,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def is_test_alarm(alarm: dict[str, Any]) -> bool:
    """`test` on the top level or in alarmData (both are set by the AlarmServer)."""
    alarm_data = alarm.get("alarmData")
    return bool(
        alarm.get("test")
        or (isinstance(alarm_data, dict) and alarm_data.get("test"))
    )


def is_probe(alarm: dict[str, Any]) -> bool:
    """The "Testen" probe from the profile: id -1, never stored on the server."""
    alarm_id = alarm.get("id")
    return not isinstance(alarm_id, int) or isinstance(alarm_id, bool) or alarm_id <= 0


class GroupAlarmConnection:
    """Owns the client, the poll/stream loop and the last-seen alarm state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: GroupAlarmClient,
        initial_organizations: list[dict[str, Any]],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.available = True
        self.last_alarm: dict[str, Any] | None = None
        self.last_alarm_by_org: dict[str, dict[str, Any]] = {}
        self.organizations: list[dict[str, Any]] = list(initial_organizations)
        self.active_alarm_keys: set[str] = set()

        self._store: Store = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_TEMPLATE.format(entry_id=entry.entry_id)
        )
        # None = no baseline for this server/UUID yet -> initial sync
        self._latest_id: int | None = None
        self._recent_ids: deque[int] = deque(maxlen=MAX_RECENT_IDS)
        self._task: asyncio.Task | None = None
        self._unsub_org_refresh: CALLBACK_TYPE | None = None
        self._org_refresh_task: asyncio.Task | None = None
        self._status_reset_unsubs: dict[str, CALLBACK_TYPE] = {}
        self._stopped = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Load persisted state and start the background poll/stream loop."""
        stored = await self._store.async_load()
        if stored:
            if (
                stored.get("base_url") == self.client.base_url
                and stored.get("uuid") == self.client.uuid
            ):
                self._latest_id = stored.get("latest_id")
                self.last_alarm = stored.get("last_alarm")
                self.last_alarm_by_org = stored.get("last_alarm_by_org", {})
            else:
                # Other server or other connection (e.g. reconfigured from the
                # old GroupAlarm V2): its ids mean nothing here. A larger old
                # id would silently block /poll, so start over.
                _LOGGER.info(
                    "Stored state of %s belongs to another server/connection, "
                    "starting with an initial sync",
                    self.entry.title,
                )
                await self._async_save()
        self._task = self.hass.async_create_background_task(
            self._async_run(), f"homeassistantalarm-{self.entry.entry_id}"
        )
        self._unsub_org_refresh = async_track_time_interval(
            self.hass, self._handle_org_refresh_interval, ORGANIZATION_REFRESH_INTERVAL
        )

    async def async_stop(self) -> None:
        """Cancel the background loop and wait until the stream is closed."""
        self._stopped = True
        if self._unsub_org_refresh is not None:
            self._unsub_org_refresh()
            self._unsub_org_refresh = None
        for unsub in self._status_reset_unsubs.values():
            unsub()
        self._status_reset_unsubs.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    @property
    def max_age_minutes(self) -> int:
        return int(
            self.entry.options.get(CONF_MAX_AGE_MINUTES, DEFAULT_MAX_AGE_MINUTES)
        )

    @property
    def test_activates_status(self) -> bool:
        return bool(
            self.entry.options.get(
                CONF_TEST_ACTIVATES_STATUS, DEFAULT_TEST_ACTIVATES_STATUS
            )
        )

    # ------------------------------------------------------------------
    # Organizations
    # ------------------------------------------------------------------

    @property
    def organization_uuids(self) -> set[str]:
        return {org["uuid"] for org in self.organizations}

    @callback
    def _handle_org_refresh_interval(self, now) -> None:
        if self._org_refresh_task is not None and not self._org_refresh_task.done():
            return
        self._org_refresh_task = self.entry.async_create_background_task(
            self.hass,
            self._async_refresh_organizations(),
            f"homeassistantalarm-orgs-{self.entry.entry_id}",
        )

    async def _async_refresh_organizations(self) -> None:
        """Sync the organization list with /status and announce new ones."""
        try:
            status = await self.client.async_get_status()
        except GroupAlarmError as err:
            _LOGGER.debug("Could not refresh organization list: %s", err)
            return

        current = list(status.get("subscribedOrganizations", []))
        known_uuids = self.organization_uuids
        new_orgs = [org for org in current if org["uuid"] not in known_uuids]
        # Replace the list so unsubscribed organizations drop out (their
        # devices then become removable, see async_remove_config_entry_device)
        self.organizations = current
        if not new_orgs:
            return

        _LOGGER.info(
            "Found %d newly subscribed organization(s) for %s",
            len(new_orgs),
            self.entry.title,
        )
        async_dispatcher_send(
            self.hass,
            SIGNAL_NEW_ORGANIZATIONS.format(entry_id=self.entry.entry_id),
            new_orgs,
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "base_url": self.client.base_url,
                "uuid": self.client.uuid,
                "latest_id": self._latest_id,
                "last_alarm": self.last_alarm,
                "last_alarm_by_org": self.last_alarm_by_org,
            }
        )

    def _set_available(self, available: bool) -> None:
        if self.available != available:
            self.available = available
            async_dispatcher_send(
                self.hass, SIGNAL_AVAILABILITY.format(entry_id=self.entry.entry_id), available
            )

    def _alarm_age_minutes(self, alarm: dict[str, Any]) -> float | None:
        timestamp = alarm.get("timestamp")
        parsed: datetime | None = (
            dt_util.parse_datetime(timestamp) if isinstance(timestamp, str) else None
        )
        if parsed is None:
            return None
        return (dt_util.utcnow() - parsed).total_seconds() / 60

    def _store_in_sensors(self, alarm: dict[str, Any]) -> None:
        """Update the last-alarm state (overall and, if known, per organization)."""
        self.last_alarm = alarm
        org_uuid = alarm.get("organizationUuid")
        # The probe carries a random organization UUID: keeping it would leave
        # a phantom entry in the store forever
        if org_uuid and not (is_probe(alarm) and org_uuid not in self.organization_uuids):
            self.last_alarm_by_org[org_uuid] = alarm
        async_dispatcher_send(
            self.hass, SIGNAL_ALARM.format(entry_id=self.entry.entry_id), alarm
        )

    @callback
    def _handle_alarm(self, payload: dict[str, Any], *, source: str) -> bool:
        """Process one alarm from `source` ("poll" or "stream").

        Returns False for a duplicate that was skipped.
        """
        probe = is_probe(payload)
        if not probe:
            alarm_id: int = payload["id"]
            if alarm_id in self._recent_ids:
                return False
            self._recent_ids.append(alarm_id)
            if self._latest_id is None or alarm_id > self._latest_id:
                self._latest_id = alarm_id

        test = is_test_alarm(payload)
        alarm = {**payload, "test": test}
        org_uuid = alarm.get("organizationUuid")

        if (
            not probe
            and org_uuid
            and org_uuid not in self.organization_uuids
        ):
            # Subscribed after our last /status check: pick up its sensors now
            self._handle_org_refresh_interval(None)

        self._store_in_sensors(alarm)

        max_age = self.max_age_minutes
        age = self._alarm_age_minutes(alarm)
        if max_age > 0 and age is not None and age > max_age:
            if source == "stream":
                _LOGGER.warning(
                    "Alarm %s arrived via stream but is %.0f minutes old (limit %d) - "
                    "only updating sensors. If this happens for fresh alarms, check "
                    "the clock of Home Assistant and the server",
                    alarm.get("id"),
                    age,
                    max_age,
                )
            else:
                _LOGGER.info(
                    "Caught-up alarm %s is %.0f minutes old (limit %d) - "
                    "only updating sensors, no event",
                    alarm.get("id"),
                    age,
                    max_age,
                )
            return True

        self.hass.bus.async_fire(EVENT_ALARM, alarm)

        if test and not self.test_activates_status:
            return True
        self._activate_alarm_status(ALARM_STATUS_OVERALL_KEY)
        if org_uuid and org_uuid in self.organization_uuids:
            self._activate_alarm_status(org_uuid)
        return True

    @callback
    def _handle_notification(self, payload: dict[str, Any]) -> None:
        self.hass.bus.async_fire(
            EVENT_NOTIFICATION,
            {
                "type": payload.get("type"),
                "message": payload.get("message"),
                "timestamp": payload.get("timestamp"),
            },
        )

    def _activate_alarm_status(self, key: str) -> None:
        """Flip a status key to "active" and (re)start its 5-minute reset timer.

        A second alarm for the same key while it's already active restarts
        the window instead of stacking - the status sensor just reflects
        "was there an alarm in roughly the last 5 minutes".
        """
        existing_unsub = self._status_reset_unsubs.pop(key, None)
        if existing_unsub is not None:
            existing_unsub()

        was_active = key in self.active_alarm_keys
        self.active_alarm_keys.add(key)

        @callback
        def _reset(_now) -> None:
            self._status_reset_unsubs.pop(key, None)
            self.active_alarm_keys.discard(key)
            async_dispatcher_send(
                self.hass, SIGNAL_ALARM_STATUS.format(entry_id=self.entry.entry_id), key
            )

        self._status_reset_unsubs[key] = async_call_later(
            self.hass, ALARM_ACTIVE_DURATION.total_seconds(), _reset
        )
        if not was_active:
            async_dispatcher_send(
                self.hass, SIGNAL_ALARM_STATUS.format(entry_id=self.entry.entry_id), key
            )

    # ------------------------------------------------------------------
    # Poll / stream loop
    # ------------------------------------------------------------------

    async def _async_initial_sync(self) -> None:
        """First contact with this server/UUID: take over the state silently.

        Without since_id the server returns the newest alarms. They only go
        into the sensors (so these show something) - firing events for them
        would replay up to 50 old alarms into automations.
        """
        data = await self.client.async_poll(limit=DEFAULT_POLL_LIMIT)
        alarms = sorted(
            (a for a in data.get("alarms", []) if not is_probe(a)),
            key=lambda a: a["id"],
        )
        for alarm in alarms:
            self._recent_ids.append(alarm["id"])
            self._store_in_sensors({**alarm, "test": is_test_alarm(alarm)})
        latest = data.get("latestId")
        self._latest_id = latest if isinstance(latest, int) else (
            alarms[-1]["id"] if alarms else 0
        )
        _LOGGER.info(
            "Initial sync for %s: took over %d alarm(s) without events, latest id %s",
            self.entry.title,
            len(alarms),
            self._latest_id,
        )
        await self._async_save()

    async def _async_catch_up(self) -> None:
        """Poll everything since the last known id."""
        if self._latest_id is None:
            await self._async_initial_sync()
            return
        while True:
            data = await self.client.async_poll(
                since_id=self._latest_id, limit=DEFAULT_POLL_LIMIT
            )
            alarms = data.get("alarms", [])
            for alarm in alarms:
                self._handle_alarm(alarm, source="poll")
            latest = data.get("latestId")
            if isinstance(latest, int):
                self._latest_id = max(self._latest_id or 0, latest)
            await self._async_save()
            # keep polling until we've drained the backlog
            if len(alarms) < DEFAULT_POLL_LIMIT:
                break

    async def _async_run(self) -> None:
        backoff = MIN_BACKOFF
        while not self._stopped:
            wait: float = backoff
            try:
                await self._async_catch_up()
                self._set_available(True)

                async for event_name, payload in self.client.async_stream():
                    if event_name == "connected":
                        _LOGGER.debug(
                            "Stream connected: %s", payload.get("connectionName")
                        )
                        self._set_available(True)
                        backoff = MIN_BACKOFF
                    elif event_name == "alarm":
                        self._handle_alarm(payload, source="stream")
                        await self._async_save()
                    elif event_name == "notification":
                        self._handle_notification(payload)
                    else:
                        _LOGGER.debug("Ignoring unknown stream event %s", event_name)
                    # "error" events raise inside async_stream()

                # Clean stream end (server restart, or a newer stream of the
                # same connection displaced this one): reconnect quickly
                backoff = MIN_BACKOFF
                wait = backoff
            except GroupAlarmRateLimitError as err:
                retry_after = (
                    err.retry_after if err.retry_after is not None else RATE_LIMIT_WAIT
                )
                wait = max(retry_after, backoff)
                _LOGGER.warning(
                    "Server locked out %s after too many failed attempts (%s), "
                    "waiting %ss",
                    self.entry.title,
                    err,
                    wait,
                )
                self._set_available(False)
            except GroupAlarmAuthError:
                _LOGGER.error(
                    "Authentication failed for entry %s - starting reauth",
                    self.entry.title,
                )
                self._set_available(False)
                self.entry.async_start_reauth(self.hass)
                wait = MAX_BACKOFF
            except GroupAlarmNotFoundError:
                _LOGGER.error(
                    "Connection %s no longer exists on the server",
                    self.entry.title,
                )
                self._set_available(False)
                self.entry.async_start_reauth(self.hass)
                wait = MAX_BACKOFF
            except (GroupAlarmConnectionError, GroupAlarmError) as err:
                _LOGGER.warning(
                    "Connection issue for %s: %s (retrying in %ss)",
                    self.entry.title,
                    err,
                    backoff,
                )
                self._set_available(False)
                backoff = min(backoff * 2, MAX_BACKOFF)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in connection loop")
                self._set_available(False)
                backoff = min(backoff * 2, MAX_BACKOFF)

            if self._stopped:
                break
            await asyncio.sleep(wait)

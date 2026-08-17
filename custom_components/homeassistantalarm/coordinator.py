"""Background push connection to a GroupAlarm server.

Implements the strategy documented in docs/homeassistant-integration-api.md:
poll to catch up on (re)connect, then hold the SSE stream open for realtime
events, deduplicating by alarm id and reconnecting with exponential backoff.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from .api import (
    GroupAlarmAuthError,
    GroupAlarmClient,
    GroupAlarmConnectionError,
    GroupAlarmError,
    GroupAlarmNotFoundError,
)
from .const import (
    ALARM_ACTIVE_DURATION,
    ALARM_STATUS_OVERALL_KEY,
    DEFAULT_POLL_LIMIT,
    EVENT_ALARM,
    MAX_BACKOFF,
    MAX_RECENT_IDS,
    MIN_BACKOFF,
    ORGANIZATION_REFRESH_INTERVAL,
    SIGNAL_ALARM,
    SIGNAL_ALARM_STATUS,
    SIGNAL_AVAILABILITY,
    SIGNAL_NEW_ORGANIZATIONS,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


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
        self._latest_id = 0
        self._recent_ids: deque[int] = deque(maxlen=MAX_RECENT_IDS)
        self._task: asyncio.Task | None = None
        self._unsub_org_refresh: CALLBACK_TYPE | None = None
        self._status_reset_unsubs: dict[str, CALLBACK_TYPE] = {}
        self._stopped = False

    async def async_start(self) -> None:
        """Load persisted state and start the background poll/stream loop."""
        stored = await self._store.async_load()
        if stored:
            self._latest_id = stored.get("latest_id", 0)
            self.last_alarm = stored.get("last_alarm")
            self.last_alarm_by_org = stored.get("last_alarm_by_org", {})
        self._task = self.hass.async_create_background_task(
            self._async_run(), f"homeassistantalarm-{self.entry.entry_id}"
        )
        self._unsub_org_refresh = async_track_time_interval(
            self.hass, self._handle_org_refresh_interval, ORGANIZATION_REFRESH_INTERVAL
        )

    async def async_stop(self) -> None:
        """Cancel the background loop."""
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

    @callback
    def _handle_org_refresh_interval(self, now) -> None:
        self.hass.async_create_task(self._async_refresh_organizations())

    async def _async_refresh_organizations(self) -> None:
        """Check /status for newly subscribed organizations and announce them."""
        try:
            status = await self.client.async_get_status()
        except GroupAlarmError as err:
            _LOGGER.debug("Could not refresh organization list: %s", err)
            return

        known_uuids = {org["uuid"] for org in self.organizations}
        new_orgs = [
            org
            for org in status.get("subscribedOrganizations", [])
            if org["uuid"] not in known_uuids
        ]
        if not new_orgs:
            return

        self.organizations.extend(new_orgs)
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

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
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

    def _handle_alarm(self, payload: dict[str, Any]) -> None:
        alarm_id = payload.get("id")
        if alarm_id is not None:
            if alarm_id in self._recent_ids:
                return
            self._recent_ids.append(alarm_id)
            if alarm_id > self._latest_id:
                self._latest_id = alarm_id

        self.last_alarm = payload
        org_uuid = payload.get("organizationUuid")
        if org_uuid:
            self.last_alarm_by_org[org_uuid] = payload

        self.hass.bus.async_fire(EVENT_ALARM, payload)
        async_dispatcher_send(
            self.hass, SIGNAL_ALARM.format(entry_id=self.entry.entry_id), payload
        )

        self._activate_alarm_status(ALARM_STATUS_OVERALL_KEY)
        if org_uuid:
            self._activate_alarm_status(org_uuid)

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

    async def _async_catch_up(self) -> None:
        """Poll everything since the last known id."""
        while True:
            data = await self.client.async_poll(
                since_id=self._latest_id, limit=DEFAULT_POLL_LIMIT
            )
            for alarm in data.get("alarms", []):
                self._handle_alarm(alarm)
            self._latest_id = data.get("latestId", self._latest_id)
            await self._async_save()
            # keep polling until we've drained the backlog
            if len(data.get("alarms", [])) < DEFAULT_POLL_LIMIT:
                break

    async def _async_run(self) -> None:
        backoff = MIN_BACKOFF
        while not self._stopped:
            try:
                await self._async_catch_up()
                self._set_available(True)

                async for event_name, payload in self.client.async_stream():
                    if event_name == "connected":
                        _LOGGER.debug(
                            "Stream connected: %s", payload.get("connectionName")
                        )
                        self._set_available(True)
                    elif event_name == "alarm":
                        self._handle_alarm(payload)
                        await self._async_save()
                    # "error" events raise inside async_stream() and land in except below

                backoff = MIN_BACKOFF  # clean stream end, reset backoff
            except GroupAlarmAuthError:
                _LOGGER.error(
                    "Authentication failed for entry %s - starting reauth",
                    self.entry.title,
                )
                self._set_available(False)
                self.entry.async_start_reauth(self.hass)
                await asyncio.sleep(MAX_BACKOFF)
                continue
            except GroupAlarmNotFoundError:
                _LOGGER.error(
                    "Connection %s no longer exists on the server",
                    self.entry.title,
                )
                self._set_available(False)
                self.entry.async_start_reauth(self.hass)
                await asyncio.sleep(MAX_BACKOFF)
                continue
            except (GroupAlarmConnectionError, GroupAlarmError) as err:
                _LOGGER.warning(
                    "Connection issue for %s: %s (retrying in %ss)",
                    self.entry.title,
                    err,
                    backoff,
                )
                self._set_available(False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in connection loop")
                self._set_available(False)

            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

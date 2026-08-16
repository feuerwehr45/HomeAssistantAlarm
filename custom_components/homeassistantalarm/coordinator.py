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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .api import (
    GroupAlarmAuthError,
    GroupAlarmClient,
    GroupAlarmConnectionError,
    GroupAlarmError,
    GroupAlarmNotFoundError,
)
from .const import (
    DEFAULT_POLL_LIMIT,
    MAX_BACKOFF,
    MAX_RECENT_IDS,
    MIN_BACKOFF,
    SIGNAL_ALARM,
    SIGNAL_AVAILABILITY,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class GroupAlarmConnection:
    """Owns the client, the poll/stream loop and the last-seen alarm state."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: GroupAlarmClient
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.available = True
        self.last_alarm: dict[str, Any] | None = None
        self.last_alarm_by_org: dict[str, dict[str, Any]] = {}

        self._store: Store = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_TEMPLATE.format(entry_id=entry.entry_id)
        )
        self._latest_id = 0
        self._recent_ids: deque[int] = deque(maxlen=MAX_RECENT_IDS)
        self._task: asyncio.Task | None = None
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

    async def async_stop(self) -> None:
        """Cancel the background loop."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

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

        self.hass.bus.async_fire("groupalarm_alarm", payload)
        async_dispatcher_send(
            self.hass, SIGNAL_ALARM.format(entry_id=self.entry.entry_id), payload
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
                            "GroupAlarm stream connected: %s", payload.get("connectionName")
                        )
                        self._set_available(True)
                    elif event_name == "alarm":
                        self._handle_alarm(payload)
                        await self._async_save()
                    # "error" events raise inside async_stream() and land in except below

                backoff = MIN_BACKOFF  # clean stream end, reset backoff
            except GroupAlarmAuthError:
                _LOGGER.error(
                    "GroupAlarm authentication failed for entry %s - starting reauth",
                    self.entry.title,
                )
                self._set_available(False)
                self.entry.async_start_reauth(self.hass)
                await asyncio.sleep(MAX_BACKOFF)
                continue
            except GroupAlarmNotFoundError:
                _LOGGER.error(
                    "GroupAlarm connection %s no longer exists on the server",
                    self.entry.title,
                )
                self._set_available(False)
                self.entry.async_start_reauth(self.hass)
                await asyncio.sleep(MAX_BACKOFF)
                continue
            except (GroupAlarmConnectionError, GroupAlarmError) as err:
                _LOGGER.warning(
                    "GroupAlarm connection issue for %s: %s (retrying in %ss)",
                    self.entry.title,
                    err,
                    backoff,
                )
                self._set_available(False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in GroupAlarm connection loop")
                self._set_available(False)

            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

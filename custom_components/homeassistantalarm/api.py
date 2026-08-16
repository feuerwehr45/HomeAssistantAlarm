"""Thin async client for the GroupAlarm HomeAssistant API.

See docs/homeassistant-integration-api.md in the project repository for the
full API reference this client implements.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class GroupAlarmError(Exception):
    """Base error for the GroupAlarm API."""


class GroupAlarmAuthError(GroupAlarmError):
    """Invalid or missing API key."""


class GroupAlarmNotFoundError(GroupAlarmError):
    """Unknown connection UUID."""


class GroupAlarmConnectionError(GroupAlarmError):
    """Network-level error talking to the GroupAlarm server."""


class GroupAlarmClient:
    """Talks to the `/api/homeassistant/{uuid}/...` endpoints of a GroupAlarm server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        uuid: str,
        api_key: str,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._uuid = uuid
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def _url(self, path: str) -> str:
        return f"{self._base_url}/api/homeassistant/{self._uuid}/{path}"

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status in (200,):
            return
        try:
            payload = await resp.json()
            message = payload.get("error", "")
        except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
            message = ""
        if resp.status == 401:
            raise GroupAlarmAuthError(message or "invalid api key")
        if resp.status == 404:
            raise GroupAlarmNotFoundError(message or "connection not found")
        raise GroupAlarmError(f"Unexpected status {resp.status}: {message}")

    async def async_get_status(self) -> dict[str, Any]:
        """Validate credentials and return connection metadata."""
        try:
            async with self._session.get(
                self._url("status"),
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                await self._raise_for_status(resp)
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise GroupAlarmConnectionError(str(err)) from err

    async def async_poll(
        self, since_id: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        """Fetch alarms with id > since_id. Returns {"latestId": int, "alarms": [...]}."""
        params = {"since_id": since_id, "limit": limit}
        try:
            async with self._session.get(
                self._url("poll"),
                headers=self._headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                await self._raise_for_status(resp)
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise GroupAlarmConnectionError(str(err)) from err

    async def async_stream(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Open the SSE stream and yield (event_name, payload) tuples.

        Auth errors do not arrive as an HTTP status (the server already
        committed a 200 before it can check credentials) - they arrive as
        an `error` SSE event instead, raised here as GroupAlarmAuthError.
        """
        headers = {**self._headers, "Accept": "text/event-stream"}
        try:
            async with self._session.get(
                self._url("stream"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_read=60, sock_connect=10
                ),
            ) as resp:
                if resp.status == 401:
                    raise GroupAlarmAuthError("invalid api key")
                if resp.status == 404:
                    raise GroupAlarmNotFoundError("connection not found")
                if resp.status != 200:
                    raise GroupAlarmError(f"Unexpected status {resp.status}")

                event_name: str | None = None
                data_lines: list[str] = []
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8").rstrip("\n").rstrip("\r")
                    if line.startswith(":"):
                        continue  # heartbeat comment
                    if line == "":
                        if event_name and data_lines:
                            payload = json.loads("\n".join(data_lines))
                            yield event_name, payload
                            if event_name == "error":
                                raise GroupAlarmAuthError(
                                    payload.get("error", "stream auth error")
                                )
                        event_name, data_lines = None, []
                        continue
                    if line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:") :].strip())
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise GroupAlarmConnectionError(str(err)) from err

"""Thin async client for the HomeAssistant API of the AlarmServer.

See docs/homeassistant-integration-api.md in the project repository for the
full API reference this client implements. Class names still carry the
historical "GroupAlarm" prefix; the API is unchanged in shape.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .const import STREAM_HEARTBEAT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# Text of the SSE `error` event while the server locks out the client IP
# (HomeAssistantAccess.authenticate on the AlarmServer). Matched loosely: if
# the wording ever changes, the error falls back to GroupAlarmStreamRejected
# and the following catch-up /poll classifies it via its HTTP status.
_RATE_LIMIT_MARKERS = ("zu viele", "too many", "später erneut")
_AUTH_MARKERS = ("ungültiger api-key", "header fehlt", "invalid api key")
_NOT_FOUND_MARKERS = ("nicht gefunden", "ungültige uuid", "not found")


class GroupAlarmError(Exception):
    """Base error for the AlarmServer API."""


class GroupAlarmAuthError(GroupAlarmError):
    """Invalid or missing API key."""


class GroupAlarmNotFoundError(GroupAlarmError):
    """Unknown or malformed connection UUID."""


class GroupAlarmConnectionError(GroupAlarmError):
    """Network-level error talking to the server."""


class GroupAlarmRateLimitError(GroupAlarmError):
    """Server locked us out after too many failed attempts (HTTP 429).

    Not an authentication problem: wait `retry_after` seconds (if known)
    and try again, never start a reauth for this.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GroupAlarmStreamRejectedError(GroupAlarmError):
    """The stream answered with an `error` event we could not classify."""


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form, not used by the AlarmServer
    return seconds if seconds >= 0 else None


def classify_stream_error(message: str) -> GroupAlarmError:
    """Map the text of an SSE `error` event to an exception."""
    lowered = message.lower()
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return GroupAlarmRateLimitError(message)
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return GroupAlarmAuthError(message)
    if any(marker in lowered for marker in _NOT_FOUND_MARKERS):
        return GroupAlarmNotFoundError(message)
    return GroupAlarmStreamRejectedError(message or "stream rejected")


class GroupAlarmClient:
    """Talks to the `/api/homeassistant/{uuid}/...` endpoints of the server."""

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

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def uuid(self) -> str:
        return self._uuid

    def _url(self, path: str) -> str:
        return f"{self._base_url}/api/homeassistant/{self._uuid}/{path}"

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status == 200:
            return
        try:
            payload = await resp.json(content_type=None)
            message = payload.get("error", "") if isinstance(payload, dict) else ""
        except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
            message = ""
        if resp.status == 401:
            raise GroupAlarmAuthError(message or "invalid api key")
        if resp.status in (400, 404):
            raise GroupAlarmNotFoundError(message or "connection not found")
        if resp.status == 429:
            raise GroupAlarmRateLimitError(
                message or "too many failed attempts",
                _parse_retry_after(resp.headers.get("Retry-After")),
            )
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
        self, since_id: int | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """Fetch alarms with id > since_id. Returns {"latestId": int, "alarms": [...]}.

        `since_id=None` omits the parameter: the server then returns the
        newest `limit` alarms (initial sync).
        """
        params: dict[str, int] = {"limit": limit}
        if since_id is not None:
            params["since_id"] = since_id
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
        an `error` SSE event, raised here via classify_stream_error() as
        auth, not-found, rate-limit or unclassified rejection.

        The HTTP connection is closed explicitly when the generator ends for
        any reason (including cancellation on unload/reload), so there is
        never more than one open stream per config entry.
        """
        headers = {**self._headers, "Accept": "text/event-stream"}
        resp: aiohttp.ClientResponse | None = None
        try:
            resp = await self._session.get(
                self._url("stream"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_read=STREAM_HEARTBEAT_TIMEOUT,
                    sock_connect=10,
                ),
            )
            if resp.status != 200:
                await self._raise_for_status(resp)

            event_name: str | None = None
            data_lines: list[str] = []
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").rstrip("\n").rstrip("\r")
                if line.startswith(":"):
                    continue  # heartbeat comment
                if line == "":
                    if event_name and data_lines:
                        try:
                            payload = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError:
                            _LOGGER.warning(
                                "Ignoring %s event with invalid JSON", event_name
                            )
                            payload = None
                        if event_name == "error":
                            message = (
                                payload.get("error", "")
                                if isinstance(payload, dict)
                                else ""
                            )
                            raise classify_stream_error(message)
                        if isinstance(payload, dict):
                            yield event_name, payload
                    event_name, data_lines = None, []
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].strip())
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise GroupAlarmConnectionError(str(err)) from err
        finally:
            if resp is not None:
                resp.close()

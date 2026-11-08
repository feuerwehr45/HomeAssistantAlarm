"""Fixtures: a small fake AlarmServer (aiohttp) and helpers.

The fake server mirrors the HomeAssistant API of the AlarmServer
(docs/homeassistant-integration-api.md): /status, /poll (latestId =
max(newest id, since_id)), /stream with connected/alarm/notification/error
events, 401/404/429 for /status and /poll, error events on /stream.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homeassistantalarm.const import CONF_UUID, DOMAIN

UUID_A = "3f9c2b1a-4e2d-4a3b-9c1a-1234567890ab"
UUID_B = "7d1e0f2a-1111-4c2b-8d3e-abcdefabcdef"
KEY_A = "key-a"
KEY_B = "key-b"
ENTRY_ID = "entry1"

ORG1 = {"uuid": "9b1c1a2b-0000-4000-8000-000000000001", "name": "FF Musterstadt"}
ORG2 = {"uuid": "9b1c1a2b-0000-4000-8000-000000000002", "name": "DLRG Musterstadt"}
ORG3 = {"uuid": "9b1c1a2b-0000-4000-8000-000000000003", "name": "FF Neu"}

RATE_LIMIT_TEXT = "Zu viele Fehlversuche. Bitte später erneut versuchen."


def iso(minutes_ago: float = 0) -> str:
    """Timestamp in the server's format (UTC, `Z`)."""
    ts = dt_util.utcnow() - timedelta(minutes=minutes_ago)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def make_alarm(
    alarm_id: int,
    org: dict[str, str] = ORG1,
    minutes_ago: float = 0,
    test: bool = False,
) -> dict[str, Any]:
    return {
        "id": alarm_id,
        "organizationUuid": org["uuid"],
        "organization": org["name"],
        "message": f"Alarm {alarm_id}",
        "timestamp": iso(minutes_ago),
        "alarmData": {
            "code": "B2.01",
            "stichwort": "Gebäudebrand",
            "adresse": "Musterstraße 1",
            "ort": "Musterstadt",
            "zusatz": "",
            "fahrzeuge": "HLF 1",
            "lat": None,
            "lon": None,
            "maps_link": None,
            "prioritaet": "",
            "datum": "08.11.2026",
            "uhrzeit": "12:00",
            "test": test,
        },
        "test": test,
    }


def make_probe() -> dict[str, Any]:
    """Probe from the profile: id -1, random organization, test true."""
    import uuid as _uuid

    alarm = make_alarm(-1, {"uuid": str(_uuid.uuid4()), "name": "Test-Organisation"}, test=True)
    return alarm


class FakeAlarmServer:
    """In-process stand-in for the AlarmServer's HomeAssistant endpoints."""

    def __init__(self) -> None:
        self.connections: dict[str, dict[str, Any]] = {
            UUID_A: {"key": KEY_A, "name": "Zuhause", "orgs": [ORG1, ORG2]},
            UUID_B: {"key": KEY_B, "name": "Zuhause neu", "orgs": [ORG3]},
        }
        self.alarms: list[dict[str, Any]] = []
        self.poll_requests: list[tuple[str, int | None, float]] = []
        self.stream_error: str | None = None
        self.retry_after: int | None = None  # set -> /status and /poll answer 429
        self.streams_opened = 0
        self._streams: list[tuple[str, asyncio.Queue]] = []
        app = web.Application()
        app.router.add_get("/api/homeassistant/{uuid}/status", self._status)
        app.router.add_get("/api/homeassistant/{uuid}/poll", self._poll)
        app.router.add_get("/api/homeassistant/{uuid}/stream", self._stream)
        self._server = TestServer(app, host="127.0.0.1")

    @property
    def base_url(self) -> str:
        return str(self._server.make_url("")).rstrip("/")

    @property
    def open_streams(self) -> int:
        return len(self._streams)

    def open_streams_for(self, uuid: str) -> int:
        return sum(1 for u, _ in self._streams if u == uuid)

    async def start(self) -> None:
        await self._server.start_server()

    async def stop(self) -> None:
        await self.end_streams()
        await self._server.close()

    # -- test controls -------------------------------------------------

    async def push(self, event: str, data: dict[str, Any]) -> None:
        for _, queue in list(self._streams):
            await queue.put((event, data))

    async def end_streams(self) -> None:
        for _, queue in list(self._streams):
            await queue.put(None)

    # -- handlers ------------------------------------------------------

    def _check(self, request: web.Request) -> tuple[str | None, web.Response | None]:
        """Same order as HomeAssistantAccess.authenticate."""
        if self.retry_after is not None:
            return None, web.json_response(
                {"error": RATE_LIMIT_TEXT},
                status=429,
                headers={"Retry-After": str(self.retry_after)},
            )
        uuid = request.match_info["uuid"]
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None, web.json_response(
                {"error": "Authorization: Bearer <api_key> Header fehlt"}, status=401
            )
        conn = self.connections.get(uuid)
        if conn is None:
            return None, web.json_response({"error": "Verbindung nicht gefunden"}, status=404)
        if auth[len("Bearer ") :] != conn["key"]:
            return None, web.json_response({"error": "Ungültiger API-Key"}, status=401)
        return uuid, None

    async def _status(self, request: web.Request) -> web.Response:
        uuid, error = self._check(request)
        if error is not None:
            return error
        conn = self.connections[uuid]
        return web.json_response(
            {"connectionName": conn["name"], "subscribedOrganizations": conn["orgs"]}
        )

    async def _poll(self, request: web.Request) -> web.Response:
        uuid, error = self._check(request)
        raw_since = request.query.get("since_id")
        self.poll_requests.append(
            (request.match_info["uuid"], int(raw_since) if raw_since else None,
             asyncio.get_running_loop().time())
        )
        if error is not None:
            return error
        since = int(raw_since) if raw_since else 0
        limit = int(request.query.get("limit", 50))
        stored = sorted((a for a in self.alarms if a["id"] > 0), key=lambda a: a["id"])
        if since == 0:
            found = stored[-limit:]
        else:
            found = [a for a in stored if a["id"] > since][:limit]
        latest = max([a["id"] for a in found] + [since])
        return web.json_response({"latestId": latest, "alarms": found})

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        self.streams_opened += 1

        async def send(event: str, data: dict[str, Any]) -> None:
            await resp.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())

        uuid, error = self._check(request)
        if error is not None or self.stream_error is not None:
            message = self.stream_error or json.loads(error.body)["error"]
            await send("error", {"error": message})
            return resp

        queue: asyncio.Queue = asyncio.Queue()
        item = (uuid, queue)
        self._streams.append(item)
        try:
            await send("connected", {"connectionName": self.connections[uuid]["name"]})
            await resp.write(b": ping\n\n")
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), 0.05)
                except asyncio.TimeoutError:
                    transport = request.transport
                    if transport is None or transport.is_closing():
                        break
                    continue
                if message is None:
                    break
                await send(*message)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._streams.remove(item)
        return resp


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def fast_timings():
    """Short backoff/wait times so reconnect paths run within a test."""
    base = "custom_components.homeassistantalarm.coordinator"
    with (
        patch(f"{base}.MIN_BACKOFF", 0.05),
        patch(f"{base}.MAX_BACKOFF", 0.2),
        patch(f"{base}.RATE_LIMIT_WAIT", 0.3),
    ):
        yield


@pytest.fixture
async def server(hass: HomeAssistant, socket_enabled):
    srv = FakeAlarmServer()
    await srv.start()
    yield srv
    # unload everything first, so no client keeps a stream open
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await srv.stop()


def make_entry(
    server: FakeAlarmServer,
    uuid: str = UUID_A,
    key: str = KEY_A,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="Zuhause",
        unique_id=uuid,
        data={CONF_HOST: server.base_url, CONF_UUID: uuid, CONF_API_KEY: key},
        options=options or {},
    )


async def setup_entry(
    hass: HomeAssistant, server: FakeAlarmServer, entry: MockConfigEntry | None = None
) -> MockConfigEntry:
    entry = entry or make_entry(server)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def wait_for(predicate, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def stored_state(hass_storage: dict[str, Any]) -> dict[str, Any] | None:
    item = hass_storage.get(f"homeassistantalarm_{ENTRY_ID}")
    return item["data"] if item else None


def storage_item(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "minor_version": 1,
        "key": f"homeassistantalarm_{ENTRY_ID}",
        "data": data,
    }

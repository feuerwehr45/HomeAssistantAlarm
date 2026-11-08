"""Poll/stream logic against the fake AlarmServer (tasks 2-8, 10)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.homeassistantalarm.const import (
    CONF_MAX_AGE_MINUTES,
    CONF_TEST_ACTIVATES_STATUS,
    DOMAIN,
    EVENT_ALARM,
    EVENT_NOTIFICATION,
)

from .conftest import (
    ORG1,
    ORG2,
    RATE_LIMIT_TEXT,
    UUID_A,
    FakeAlarmServer,
    iso,
    make_alarm,
    make_entry,
    make_probe,
    setup_entry,
    storage_item,
    stored_state,
    wait_for,
)

LAST = "sensor.letzter_alarm"
STATUS = "sensor.alarmstatus"
ORG1_LAST = "sensor.ff_musterstadt_letzter_alarm"
ORG1_STATUS = "sensor.ff_musterstadt_alarmstatus"
ORG2_LAST = "sensor.dlrg_musterstadt_letzter_alarm"
CONNECTIVITY = "binary_sensor.verbindung"


def ts_state(alarm: dict) -> str:
    return dt_util.parse_datetime(alarm["timestamp"]).isoformat(timespec="seconds")


def state_ts(hass: HomeAssistant, entity_id: str) -> str:
    return dt_util.parse_datetime(hass.states.get(entity_id).state).isoformat(
        timespec="seconds"
    )


def reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == "reauth"
    ]


async def test_initial_sync_fires_no_event(
    hass: HomeAssistant, server: FakeAlarmServer, hass_storage
) -> None:
    """Task 2: new entry, first /poll without since_id, sensors filled, no event."""
    a1 = make_alarm(101, ORG1, minutes_ago=3)
    a2 = make_alarm(102, ORG2, minutes_ago=1)
    server.alarms = [a1, a2]
    events = async_capture_events(hass, EVENT_ALARM)

    await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    assert [(u, s) for u, s, _ in server.poll_requests] == [(UUID_A, None)]
    assert events == []
    assert state_ts(hass, LAST) == ts_state(a2)
    assert state_ts(hass, ORG1_LAST) == ts_state(a1)
    assert state_ts(hass, ORG2_LAST) == ts_state(a2)
    assert hass.states.get(STATUS).state == "Kein Alarm"
    assert hass.states.get(ORG1_STATUS).state == "Kein Alarm"
    assert hass.states.get(CONNECTIVITY).state == "on"
    assert stored_state(hass_storage)["latest_id"] == 102

    # A new alarm afterwards does fire
    a3 = make_alarm(103, ORG1)
    await server.push("alarm", a3)
    await wait_for(lambda: len(events) == 1)
    assert events[0].data["id"] == 103
    assert events[0].data["test"] is False
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "Alarm"
    assert hass.states.get(ORG1_STATUS).state == "Alarm"
    assert state_ts(hass, LAST) == ts_state(a3)
    await wait_for(lambda: stored_state(hass_storage)["latest_id"] == 103)


async def test_restart_catches_up_from_stored_id(
    hass: HomeAssistant, server: FakeAlarmServer, hass_storage
) -> None:
    """Matching stored state: /poll with since_id, new alarms fire."""
    entry = make_entry(server)
    hass_storage[f"homeassistantalarm_{entry.entry_id}"] = storage_item(
        {"base_url": server.base_url, "uuid": UUID_A, "latest_id": 101,
         "last_alarm": None, "last_alarm_by_org": {}}
    )
    server.alarms = [make_alarm(101, minutes_ago=5), make_alarm(102, minutes_ago=1)]
    events = async_capture_events(hass, EVENT_ALARM)

    await setup_entry(hass, server, entry)
    await wait_for(lambda: server.open_streams == 1)

    assert [(u, s) for u, s, _ in server.poll_requests] == [(UUID_A, 101)]
    assert [e.data["id"] for e in events] == [102]


async def test_store_of_other_server_is_discarded(
    hass: HomeAssistant, server: FakeAlarmServer, hass_storage
) -> None:
    """Task 3: state stored for another server (V2 with large ids) is reset."""
    entry = make_entry(server)
    old_alarm = make_alarm(99999, minutes_ago=600)
    hass_storage[f"homeassistantalarm_{entry.entry_id}"] = storage_item(
        {"latest_id": 99999, "last_alarm": old_alarm,
         "last_alarm_by_org": {ORG1["uuid"]: old_alarm}}  # 0.3.0 format
    )
    server.alarms = [make_alarm(5, minutes_ago=2)]
    events = async_capture_events(hass, EVENT_ALARM)

    await setup_entry(hass, server, entry)
    await wait_for(lambda: server.open_streams == 1)

    assert [(u, s) for u, s, _ in server.poll_requests] == [(UUID_A, None)]
    assert events == []
    data = stored_state(hass_storage)
    assert data["latest_id"] == 5
    assert data["base_url"] == server.base_url
    assert data["uuid"] == UUID_A
    assert data["last_alarm"]["id"] == 5

    # Same with a stored state of another base URL
    await hass.config_entries.async_unload(entry.entry_id)
    hass_storage[f"homeassistantalarm_{entry.entry_id}"] = storage_item(
        {"base_url": "https://api.groupalarm.org", "uuid": UUID_A, "latest_id": 99999,
         "last_alarm": None, "last_alarm_by_org": {}}
    )
    server.poll_requests.clear()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: server.open_streams == 1)
    assert [(u, s) for u, s, _ in server.poll_requests] == [(UUID_A, None)]
    assert events == []


async def test_old_alarms_only_update_sensors(
    hass: HomeAssistant, server: FakeAlarmServer, hass_storage
) -> None:
    """Task 4: caught-up or streamed alarms older than max age fire no event."""
    entry = make_entry(server)
    hass_storage[f"homeassistantalarm_{entry.entry_id}"] = storage_item(
        {"base_url": server.base_url, "uuid": UUID_A, "latest_id": 1,
         "last_alarm": None, "last_alarm_by_org": {}}
    )
    old = make_alarm(2, ORG1, minutes_ago=120)
    fresh = make_alarm(3, ORG2, minutes_ago=1)
    server.alarms = [old, fresh]
    events = async_capture_events(hass, EVENT_ALARM)

    await setup_entry(hass, server, entry)
    await wait_for(lambda: server.open_streams == 1)

    assert [e.data["id"] for e in events] == [3]
    assert state_ts(hass, ORG1_LAST) == ts_state(old)
    assert hass.states.get(ORG1_STATUS).state == "Kein Alarm"
    assert state_ts(hass, LAST) == ts_state(fresh)

    # Over the stream the same rule applies
    stale = make_alarm(4, ORG1, minutes_ago=30)
    await server.push("alarm", stale)
    await wait_for(lambda: stored_state(hass_storage)["latest_id"] == 4)
    await hass.async_block_till_done()
    assert [e.data["id"] for e in events] == [3]
    assert state_ts(hass, ORG1_LAST) == ts_state(stale)
    assert hass.states.get(ORG1_STATUS).state == "Kein Alarm"

    # Option 0 disables the check
    hass.config_entries.async_update_entry(entry, options={CONF_MAX_AGE_MINUTES: 0})
    await server.push("alarm", make_alarm(5, ORG1, minutes_ago=30))
    await wait_for(lambda: len(events) == 2)
    assert events[1].data["id"] == 5


async def test_two_probes_in_a_row(
    hass: HomeAssistant, server: FakeAlarmServer, hass_storage
) -> None:
    """Task 5: id -1 twice -> two events, no latest_id, no phantom org."""
    server.alarms = [make_alarm(7, minutes_ago=60)]
    events = async_capture_events(hass, EVENT_ALARM)
    entry = await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    probe1, probe2 = make_probe(), make_probe()
    await server.push("alarm", probe1)
    await server.push("alarm", probe2)
    await wait_for(lambda: len(events) == 2)

    assert [e.data["id"] for e in events] == [-1, -1]
    assert all(e.data["test"] is True for e in events)
    await hass.async_block_till_done()

    connection = entry.runtime_data.connection
    assert connection._latest_id == 7
    assert probe1["organizationUuid"] not in connection.last_alarm_by_org
    assert probe2["organizationUuid"] not in connection.last_alarm_by_org
    last = hass.states.get(LAST)
    assert last.attributes["id"] == -1
    assert last.attributes["test"] is True
    assert last.attributes["organization"] == "Test-Organisation"
    # overall status (default option: tests set the status), orgs untouched
    assert hass.states.get(STATUS).state == "Alarm"
    assert hass.states.get(ORG1_STATUS).state == "Kein Alarm"

    await wait_for(lambda: stored_state(hass_storage)["last_alarm"]["id"] == -1)
    data = stored_state(hass_storage)
    assert data["latest_id"] == 7
    assert probe2["organizationUuid"] not in data["last_alarm_by_org"]


async def test_test_alarm_status_option(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    """Task 6: org test alarm carries test; status depends on the option."""
    events = async_capture_events(hass, EVENT_ALARM)
    entry = await setup_entry(
        hass, server, make_entry(server, options={CONF_TEST_ACTIVATES_STATUS: False})
    )
    await wait_for(lambda: server.open_streams == 1)

    await server.push("alarm", make_alarm(20, ORG1, test=True))
    await wait_for(lambda: len(events) == 1)
    await hass.async_block_till_done()
    assert events[0].data["test"] is True
    assert hass.states.get(ORG1_LAST).attributes["test"] is True
    assert hass.states.get(STATUS).state == "Kein Alarm"
    assert hass.states.get(ORG1_STATUS).state == "Kein Alarm"

    hass.config_entries.async_update_entry(
        entry, options={CONF_TEST_ACTIVATES_STATUS: True}
    )
    await server.push("alarm", make_alarm(21, ORG1, test=True))
    await wait_for(lambda: len(events) == 2)
    await hass.async_block_till_done()
    assert hass.states.get(STATUS).state == "Alarm"
    assert hass.states.get(ORG1_STATUS).state == "Alarm"


async def test_stream_auth_error_starts_reauth(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    """Task 7: a real login error on the stream starts reauth."""
    await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    server.stream_error = "Ungültiger API-Key"
    await server.end_streams()
    await wait_for(lambda: len(reauth_flows(hass)) == 1)
    assert hass.states.get(CONNECTIVITY).state == "off"


async def test_stream_lockout_waits_without_reauth(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    """Task 7: the lock-out error event waits and retries, no reauth."""
    await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    server.stream_error = RATE_LIMIT_TEXT
    opened = server.streams_opened
    await server.end_streams()
    await wait_for(lambda: server.streams_opened >= opened + 2)
    await hass.async_block_till_done()
    assert reauth_flows(hass) == []
    assert hass.states.get(CONNECTIVITY).state == "off"

    server.stream_error = None
    await wait_for(lambda: server.open_streams == 1)
    await wait_for(lambda: hass.states.get(CONNECTIVITY).state == "on")
    assert reauth_flows(hass) == []


async def test_poll_429_respects_retry_after(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    """Task 7: /poll 429 waits Retry-After, no reauth."""
    await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    server.retry_after = 1
    server.poll_requests.clear()
    await server.end_streams()
    await wait_for(lambda: len(server.poll_requests) >= 2, timeout=5)
    first, second = server.poll_requests[0][2], server.poll_requests[1][2]
    assert second - first >= 0.9
    assert reauth_flows(hass) == []

    server.retry_after = None
    await wait_for(lambda: server.open_streams == 1)
    assert reauth_flows(hass) == []


async def test_notification_event(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    """Task 8: notification -> own HA event; unknown events are ignored."""
    notifications = async_capture_events(hass, EVENT_NOTIFICATION)
    alarms = async_capture_events(hass, EVENT_ALARM)
    await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    timestamp = iso()
    await server.push("something_new", {"foo": "bar"})
    await server.push(
        "notification",
        {"type": "NEWS", "message": "Übung am Freitag", "timestamp": timestamp},
    )
    await wait_for(lambda: len(notifications) == 1)
    assert notifications[0].data == {
        "type": "NEWS",
        "message": "Übung am Freitag",
        "timestamp": timestamp,
    }
    assert alarms == []
    # the stream is still up
    await server.push("alarm", make_alarm(30))
    await wait_for(lambda: len(alarms) == 1)


async def test_reload_keeps_a_single_stream(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    """Task 10: reload closes the old stream before the new one opens."""
    entry = await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    for _ in range(3):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert server.open_streams <= 1
        await wait_for(lambda: server.open_streams == 1)
    assert server.streams_opened == 4

    await hass.config_entries.async_unload(entry.entry_id)
    await wait_for(lambda: server.open_streams == 0, timeout=2)


async def test_duplicate_from_stream_and_poll_fires_once(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    """Dedupe by id still works for real alarms."""
    events = async_capture_events(hass, EVENT_ALARM)
    await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    alarm = make_alarm(40)
    await server.push("alarm", alarm)
    await wait_for(lambda: len(events) == 1)
    # reconnect: the catch-up poll delivers it again
    server.alarms.append(alarm)
    await server.end_streams()
    await wait_for(lambda: server.streams_opened == 2 and server.open_streams == 1)
    await server.push("alarm", alarm)
    await server.push("alarm", make_alarm(41))
    await wait_for(lambda: len(events) == 2)
    assert [e.data["id"] for e in events] == [40, 41]

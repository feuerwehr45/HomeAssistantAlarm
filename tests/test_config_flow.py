"""Config, reconfigure (task 9) and options flow, device removal."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.homeassistantalarm import async_remove_config_entry_device
from custom_components.homeassistantalarm.const import (
    CONF_MAX_AGE_MINUTES,
    CONF_TEST_ACTIVATES_STATUS,
    CONF_UUID,
    DEFAULT_BASE_URL,
    DOMAIN,
    EVENT_ALARM,
    org_device_identifier,
)

from .conftest import (
    ENTRY_ID,
    KEY_A,
    KEY_B,
    ORG1,
    ORG3,
    UUID_A,
    UUID_B,
    FakeAlarmServer,
    make_alarm,
    setup_entry,
    stored_state,
    wait_for,
)


async def test_user_flow_defaults_and_creates_entry(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    host_key = next(k for k in schema if k == CONF_HOST)
    assert host_key.default() == DEFAULT_BASE_URL == "https://alarm.feuerwehr45.de"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: server.base_url + "/", CONF_UUID: UUID_A, CONF_API_KEY: "wrong"},
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: server.base_url, CONF_UUID: UUID_A, CONF_API_KEY: KEY_A},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Zuhause"
    assert result["data"][CONF_HOST] == server.base_url
    await hass.async_block_till_done()
    await wait_for(lambda: server.open_streams == 1)


async def test_user_flow_rate_limited(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    server.retry_after = 30
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: server.base_url, CONF_UUID: UUID_A, CONF_API_KEY: KEY_A},
    )
    assert result["errors"] == {"base": "rate_limited"}


async def test_reconfigure_keeps_entities(
    hass: HomeAssistant, server: FakeAlarmServer, hass_storage
) -> None:
    """Task 9: switch UUID/key on the existing entry, entity ids stay."""
    server.alarms = [make_alarm(10, ORG1, minutes_ago=2)]
    events = async_capture_events(hass, EVENT_ALARM)
    entry = await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)

    ent_reg = er.async_get(hass)
    overall = {
        unique_id: ent_reg.async_get_entity_id(platform, DOMAIN, unique_id)
        for platform, unique_id in (
            ("sensor", f"{ENTRY_ID}_last_alarm"),
            ("sensor", f"{ENTRY_ID}_status"),
            ("binary_sensor", f"{ENTRY_ID}_connectivity"),
        )
    }
    assert all(overall.values())

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    # wrong key -> error, entry unchanged
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: server.base_url, CONF_UUID: UUID_B, CONF_API_KEY: "wrong"},
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.unique_id == UUID_A

    server.poll_requests.clear()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: server.base_url, CONF_UUID: UUID_B, CONF_API_KEY: KEY_B},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert entry.entry_id == ENTRY_ID
    assert entry.unique_id == UUID_B
    assert entry.data[CONF_UUID] == UUID_B
    assert entry.data[CONF_API_KEY] == KEY_B

    # exactly one stream, now for the new connection
    await wait_for(lambda: server.open_streams_for(UUID_B) == 1)
    assert server.open_streams_for(UUID_A) == 0
    assert server.open_streams == 1

    # store reset: initial sync without since_id, no events
    assert [(u, s) for u, s, _ in server.poll_requests] == [(UUID_B, None)]
    assert events == []
    assert stored_state(hass_storage)["uuid"] == UUID_B

    # overall entities kept their entity ids
    for platform, unique_id in (
        ("sensor", f"{ENTRY_ID}_last_alarm"),
        ("sensor", f"{ENTRY_ID}_status"),
        ("binary_sensor", f"{ENTRY_ID}_connectivity"),
    ):
        assert ent_reg.async_get_entity_id(platform, DOMAIN, unique_id) == overall[unique_id]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1

    # the new organization has its own device and entities
    dev_reg = dr.async_get(hass)
    new_org_device = dev_reg.async_get_device(
        identifiers={(DOMAIN, org_device_identifier(ENTRY_ID, ORG3["uuid"]))}
    )
    old_org_device = dev_reg.async_get_device(
        identifiers={(DOMAIN, org_device_identifier(ENTRY_ID, ORG1["uuid"]))}
    )
    main_device = dev_reg.async_get_device(identifiers={(DOMAIN, ENTRY_ID)})
    assert new_org_device and old_org_device and main_device

    # old org device can be removed, current ones and the main device not
    assert await async_remove_config_entry_device(hass, entry, old_org_device)
    assert not await async_remove_config_entry_device(hass, entry, new_org_device)
    assert not await async_remove_config_entry_device(hass, entry, main_device)


async def test_reconfigure_to_uuid_of_other_entry_aborts(
    hass: HomeAssistant, server: FakeAlarmServer
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UUID_B,
        data={CONF_HOST: server.base_url, CONF_UUID: UUID_B, CONF_API_KEY: KEY_B},
    )
    other.add_to_hass(hass)  # gets set up too, occupies the unique id
    entry = await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams_for(UUID_A) == 1)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: server.base_url, CONF_UUID: UUID_B, CONF_API_KEY: KEY_B},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.unique_id == UUID_A


async def test_options_flow(hass: HomeAssistant, server: FakeAlarmServer) -> None:
    entry = await setup_entry(hass, server)
    await wait_for(lambda: server.open_streams == 1)
    opened = server.streams_opened

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MAX_AGE_MINUTES: 60, CONF_TEST_ACTIVATES_STATUS: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_MAX_AGE_MINUTES: 60, CONF_TEST_ACTIVATES_STATUS: False}
    connection = entry.runtime_data.connection
    assert connection.max_age_minutes == 60
    assert connection.test_activates_status is False
    # applied live, no reload / new stream
    assert server.streams_opened == opened

"""Tests for inkVision setup, unload, services and the WebSocket coordinator."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from websockets.exceptions import ConnectionClosedError

from custom_components.inkvision.coordinator import InkVisionDataUpdateCoordinator
from custom_components.inkvision.const import DOMAIN

from .conftest import (
    BASE_URL,
    HOST,
    MAC,
    UNIQUE_ID,
    STATUS_PAYLOAD,
    FakeWebSocketFactory,
    mock_device_http,
)

SERVICES = {"set_screen_rotation", "set_custom_screen"}


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> InkVisionDataUpdateCoordinator:
    return entry.runtime_data


def _device_id(hass: HomeAssistant) -> str:
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UNIQUE_ID)})
    assert device is not None
    return device.id


def _state(hass: HomeAssistant, platform: str, entry: MockConfigEntry, suffix: str):
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.unique_id}_{suffix}"
    )
    assert entity_id is not None
    return hass.states.get(entity_id)


async def test_setup_and_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test the entry loads, connects the WebSocket and unloads cleanly."""
    entry = init_integration
    assert entry.state is ConfigEntryState.LOADED
    assert mock_websocket.urls == [f"ws://{HOST}/ws"]
    assert set(hass.services.async_services_for_domain(DOMAIN)) == SERVICES

    websocket = mock_websocket.current
    commands = [command.get("command") for command in websocket.sent]
    assert commands[:2] == ["get_status", "get_sensors"]
    # Firmware reports custom screen slots, so every slot is (re)sent on connect.
    assert commands[2:] == ["set_custom_screen"] * 4

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UNIQUE_ID)})
    assert device is not None
    assert device.name == "Atmos Smart Clock"
    assert device.sw_version == "1.2.3"

    websocket.send.reset_mock()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    # Actions are registered in async_setup and outlive the entries.
    assert set(hass.services.async_services_for_domain(DOMAIN)) == SERVICES
    # Slots are blanked so the device doesn't keep stale text while unloaded.
    assert websocket.sent == [
        {"command": "set_custom_screen", "slot": slot, "text": ""} for slot in range(1, 5)
    ]
    websocket.close.assert_awaited_once()


async def test_services_route_to_their_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test actions reach each device's own entry, and fail for an unloaded one."""
    second_host = "192.168.1.51"
    for path, payload in (
        ("sensors", {}),
        ("status", {"device": "Atmos 2", "mac_address": "11:22:33:44:55:66"}),
        ("display/screens", {}),
        ("buttons/actions", {}),
        ("api/ota/check", {}),
        ("config.json", {}),
    ):
        aioclient_mock.get(f"http://{second_host}/{path}", json=payload)
    second = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: second_host}, unique_id="11:22:33:44:55:66", version=3, minor_version=2
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    second_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "11:22:33:44:55:66")}
    )
    aioclient_mock.post(f"http://{second_host}/display/screens", json={})
    await hass.services.async_call(
        DOMAIN,
        "set_screen_rotation",
        {"device_id": second_device.id, "screens": ["clock"]},
        blocking=True,
    )
    assert str(aioclient_mock.mock_calls[-1][1]) == f"http://{second_host}/display/screens"

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert set(hass.services.async_services_for_domain(DOMAIN)) == SERVICES

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_screen_rotation",
            {"device_id": second_device.id, "screens": ["clock"]},
            blocking=True,
        )
    assert err.value.translation_key == "device_not_loaded"
    assert err.value.translation_placeholders == {"device": "Atmos 2"}


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": 500},
        {"exc": aiohttp.ClientError()},
        {"exc": asyncio.TimeoutError()},
    ],
)
async def test_setup_retry_when_device_unreachable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
    mock_kwargs: dict[str, Any],
) -> None:
    """Test setup is retried when the first poll fails."""
    aioclient_mock.get(f"{BASE_URL}/sensors", **mock_kwargs)
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_websocket.urls == []


async def test_setup_retry_when_status_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test a failing /status also defers setup."""
    aioclient_mock.get(f"{BASE_URL}/sensors", json={})
    aioclient_mock.get(f"{BASE_URL}/status", status=503)
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_tolerates_optional_endpoint_failures(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test older firmware without screens/buttons/OTA/config endpoints still loads."""
    aioclient_mock.get(f"{BASE_URL}/sensors", json={})
    aioclient_mock.get(f"{BASE_URL}/status", json={"mac_address": MAC})
    aioclient_mock.get(f"{BASE_URL}/display/screens", exc=aiohttp.ClientError())
    aioclient_mock.get(f"{BASE_URL}/buttons/actions", exc=aiohttp.ClientError())
    aioclient_mock.get(f"{BASE_URL}/api/ota/check", status=404)
    aioclient_mock.get(f"{BASE_URL}/config.json", exc=aiohttp.ClientError())
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await mock_websocket.wait_listening()

    coordinator = _coordinator(hass, mock_config_entry)
    assert coordinator.data == {
        "sensors": {},
        "status": {"mac_address": MAC},
        "screens": [],
        "ota": {},
        "config": {},
    }
    assert coordinator.button_actions == {}
    # No custom screen support reported: nothing pushed on connect.
    assert [c["command"] for c in mock_websocket.current.sent] == ["get_status", "get_sensors"]

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()


async def test_migrate_entry_removes_deprecated_entities(hass: HomeAssistant) -> None:
    """Test v1 entries lose weather entities and v2 entries lose retired sensors."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=MAC, version=1)
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)

    def register(platform: str, unique_id: str) -> str:
        return entity_registry.async_get_or_create(
            platform, DOMAIN, unique_id, config_entry=entry
        ).entity_id

    weather = register("weather", "forecast_main")
    weather_sensor = register("sensor", f"{entry.entry_id}_weather_condition")
    tvoc = register("sensor", f"{entry.entry_id}_tvoc")
    free_heap = register("sensor", f"{entry.entry_id}_free_heap")
    temperature = register("sensor", f"{entry.entry_id}_temperature")

    with patch("custom_components.inkvision.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 3
    for removed in (weather, weather_sensor, tvoc, free_heap):
        assert entity_registry.async_get(removed) is None
    assert entity_registry.async_get(temperature) is not None


async def test_migrate_entry_noop_without_deprecated_entities(hass: HomeAssistant) -> None:
    """Test migration only bumps the version when nothing needs removing."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=MAC, version=1)
    entry.add_to_hass(hass)

    with patch("custom_components.inkvision.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.version == 3


async def test_migrate_entry_keys_by_mac(hass: HomeAssistant) -> None:
    """Test 3.1 entries move entity unique IDs and the device identifier to the MAC."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=MAC, version=3)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, MAC)}
    )
    temperature = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_temperature", config_entry=entry
    )

    with patch("custom_components.inkvision.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert (entry.version, entry.minor_version) == (3, 2)
    assert entry.unique_id == UNIQUE_ID
    migrated = er.async_get(hass).async_get(temperature.entity_id)
    assert migrated.unique_id == f"{UNIQUE_ID}_temperature"
    assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, UNIQUE_ID)}


async def test_migrate_entry_without_mac_keeps_entry_id(hass: HomeAssistant) -> None:
    """Test entries keyed by host keep entry ID unique IDs and key the device by entry ID."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, unique_id=HOST, version=3)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, HOST)}
    )
    temperature = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_temperature", config_entry=entry
    )

    with patch("custom_components.inkvision.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)

    assert entry.minor_version == 2
    assert entry.unique_id == HOST
    assert er.async_get(hass).async_get(temperature.entity_id).unique_id == temperature.unique_id
    assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, entry.entry_id)}


async def test_websocket_sensor_update(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test pushed sensor values reach the entities."""
    await mock_websocket.current.push(
        {
            "type": "sensor_update",
            "data": {"bme680": {"temperature": 23.4, "humidity": 50.0}},
            "sensors_available": True,
            "timestamp": 1000,
        }
    )
    await hass.async_block_till_done()

    assert _state(hass, "sensor", init_integration, "temperature").state == "23.4"
    coordinator = _coordinator(hass, init_integration)
    assert coordinator.data["sensors"]["sensors_available"] is True
    assert coordinator.data["status"]["last_sensor_update"] == 1000
    # Unrelated sections survive the push.
    assert coordinator.data["config"] == {"timeZone": "Europe/Berlin"}

    await mock_websocket.current.push({"type": "sensor_update", "data": "garbage"})
    assert coordinator.data["sensors"] == {}


async def test_websocket_status_update_merges(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test status pushes merge into the polled status."""
    websocket = mock_websocket.current
    await websocket.push({"type": "status_update", "data": {"brightness": 3}, "timestamp": 7})

    coordinator = _coordinator(hass, init_integration)
    assert coordinator.data["status"]["brightness"] == 3
    assert coordinator.data["status"]["last_status_update"] == 7
    assert coordinator.data["status"]["firmware_id"] == STATUS_PAYLOAD["firmware_id"]

    # With no sensor data cached, a status push triggers a sensor request.
    await websocket.push({"type": "sensor_update", "data": {}})
    websocket.send.reset_mock()
    await websocket.push({"type": "status_update", "data": None})
    assert websocket.sent == [{"command": "get_sensors"}]


async def test_websocket_command_responses(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test replies to get_sensors, get_status and other commands."""
    websocket = mock_websocket.current
    coordinator = _coordinator(hass, init_integration)

    await websocket.push(
        {"command": "get_sensors", "success": True, "data": {"bme680": {"pressure": 990}}}
    )
    assert coordinator.data["sensors"] == {"bme680": {"pressure": 990}}

    await websocket.push({"command": "get_sensors", "success": True, "data": []})
    assert coordinator.data["sensors"] == {}

    await websocket.push({"command": "get_status", "success": True, "data": {"rssi": -70}})
    assert coordinator.data["status"]["rssi"] == -70
    assert coordinator.data["status"]["version"] == "1.2.3"

    await websocket.push({"command": "get_status", "success": True, "data": "x"})
    assert coordinator.data["status"]["rssi"] == -70

    await websocket.push({"command": "set_brightness", "success": True, "data": {"brightness": 12}})
    assert coordinator.data["status"]["brightness"] == 12

    before = coordinator.data
    await websocket.push({"command": "set_custom_screen", "success": True, "data": {"slot": 1}})
    await websocket.push({"command": "set_brightness", "success": False, "data": {"brightness": 1}})
    await websocket.push({"command": "restart", "success": True, "data": {}})
    assert coordinator.data is before


async def test_websocket_ping_and_bad_messages(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test ping replies and that malformed messages don't kill the connection."""
    websocket = mock_websocket.current
    websocket.send.reset_mock()

    await websocket.push({"type": "ping", "timestamp": 42})
    assert websocket.sent == [{"type": "pong", "timestamp": 42}]

    await websocket.push("{not json")
    assert "Received invalid JSON" in caplog.text

    await websocket.push(["a", "list"])
    assert "Error processing WebSocket message" in caplog.text

    await websocket.push({"type": "unknown"})
    assert len(mock_websocket.sockets) == 1


async def test_websocket_reconnects(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a dropped connection polls immediately and reconnects."""
    first = mock_websocket.current
    polls = aioclient_mock.call_count
    mock_websocket.connected.clear()
    mock_websocket.side_effects.append(OSError("unreachable"))

    with patch("custom_components.inkvision.coordinator.WEBSOCKET_RETRY_DELAY", 0):
        await first.messages.put(ConnectionClosedError(None, None))
        second = await mock_websocket.wait_listening()
        await hass.async_block_till_done()

    assert second is not first
    assert "WebSocket connection to 192.168.1.50 lost" in caplog.text
    assert "WebSocket reconnected" in caplog.text
    # The drop triggered an immediate HTTP refresh.
    assert aioclient_mock.call_count > polls
    assert init_integration.state is ConfigEntryState.LOADED


async def test_websocket_unexpected_error_retries(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test an unexpected handler error is logged and the handler carries on."""
    first = mock_websocket.current
    mock_websocket.connected.clear()

    with patch("custom_components.inkvision.coordinator.WEBSOCKET_RETRY_DELAY", 0):
        await first.messages.put(RuntimeError("boom"))
        second = await mock_websocket.wait_listening()

    assert second is not first
    assert "Unexpected error in WebSocket handler" in caplog.text


async def test_send_skipped_when_disconnected_or_closed(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test commands are dropped (and reported) when the socket is not usable."""
    coordinator = _coordinator(hass, init_integration)
    websocket = mock_websocket.current
    websocket.send.reset_mock()

    websocket.closed = lambda: True
    assert await coordinator.send_device_command("restart") is True
    websocket.send.assert_not_awaited()

    websocket.closed = False
    websocket.send.side_effect = OSError("broken pipe")
    assert await coordinator.send_device_command("restart") is True
    assert coordinator._websocket is None

    assert await coordinator.send_device_command("restart") is False


async def test_poll_if_stale(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the stale-poll check only refreshes after UPDATE_INTERVAL."""
    coordinator = _coordinator(hass, init_integration)
    calls = aioclient_mock.call_count

    await coordinator.async_poll_if_stale()
    assert aioclient_mock.call_count == calls

    coordinator._last_poll -= 301
    await coordinator.async_poll_if_stale()
    assert aioclient_mock.call_count > calls


async def test_services_unknown_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test actions reject device IDs that aren't inkVision devices."""
    other_entry = MockConfigEntry(domain="other")
    other_entry.add_to_hass(hass)
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=other_entry.entry_id, identifiers={("other", "x")}
    )

    for device_id in ("missing", foreign.id):
        for service, data in (
            ("set_screen_rotation", {"screens": ["clock"]}),
            ("set_custom_screen", {"slot": 1}),
        ):
            with pytest.raises(ServiceValidationError) as err:
                await hass.services.async_call(
                    DOMAIN, service, {"device_id": device_id, **data}, blocking=True
                )
            assert err.value.translation_key == "device_not_found"


async def test_service_set_screen_rotation(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the screen rotation service posts to the device and reports failures."""
    device_id = _device_id(hass)
    url = f"{BASE_URL}/display/screens"

    aioclient_mock.post(url, json={})
    await hass.services.async_call(
        DOMAIN,
        "set_screen_rotation",
        {"device_id": device_id, "screens": ["clock", "iaq"]},
        blocking=True,
    )
    assert aioclient_mock.mock_calls[-1][2] == {"screens": ["clock", "iaq"]}

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "set_screen_rotation", {"device_id": device_id, "screens": []}, blocking=True
        )
    assert err.value.translation_key == "no_screens"

    for mock_kwargs, key in (
        ({"status": 500}, "device_rejected"),
        ({"exc": aiohttp.ClientError()}, "communication_error"),
        ({"exc": asyncio.TimeoutError()}, "communication_error"),
    ):
        aioclient_mock.clear_requests()
        aioclient_mock.post(url, **mock_kwargs)
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                DOMAIN,
                "set_screen_rotation",
                {"device_id": device_id, "screens": ["clock"]},
                blocking=True,
            )
        assert err.value.translation_key == key


async def test_service_set_custom_screen(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test the custom screen service sends text and rejects unsupported firmware."""
    device_id = _device_id(hass)
    coordinator = _coordinator(hass, init_integration)
    websocket = mock_websocket.current
    websocket.send.reset_mock()

    await hass.services.async_call(
        DOMAIN,
        "set_custom_screen",
        {"device_id": device_id, "slot": 2, "text": "Hi"},
        blocking=True,
    )
    assert websocket.sent == [{"command": "set_custom_screen", "slot": 2, "text": "Hi"}]
    assert coordinator.custom_screens == {2: "Hi"}

    await websocket.push({"type": "status_update", "data": {"custom_screens": 0}})
    with pytest.raises(HomeAssistantError, match="does not support custom screens"):
        await hass.services.async_call(
            DOMAIN, "set_custom_screen", {"device_id": device_id, "slot": 1}, blocking=True
        )


async def test_custom_screen_templates(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test templated slots follow their template and errors blank the slot."""
    mock_device_http(aioclient_mock)
    hass.states.async_set("sensor.outdoor_temperature", "12")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        unique_id=UNIQUE_ID,
        version=3,
        minor_version=2,
        options={
            "custom_screen_1": "{{ states('sensor.outdoor_temperature') }}°C",
            "custom_screen_2": "  ",
            "custom_screen_3": "{{ states.sensor.outdoor_temperature.state | int / 0 }}",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    websocket = await mock_websocket.wait_listening()

    coordinator = _coordinator(hass, entry)
    assert coordinator.custom_screens == {1: "12°C", 3: ""}
    assert "Custom screen 3 template error" in caplog.text

    websocket.send.reset_mock()
    hass.states.async_set("sensor.outdoor_temperature", "13")
    await hass.async_block_till_done()
    assert coordinator.custom_screens[1] == "13°C"
    assert {"command": "set_custom_screen", "slot": 1, "text": "13°C"} in websocket.sent

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_options_update_reloads_entry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test changing the options reloads the entry with the new templates."""
    mock_websocket.connected.clear()
    hass.config_entries.async_update_entry(
        init_integration, options={"custom_screen_1": "static text"}
    )
    await hass.async_block_till_done()
    await mock_websocket.wait_listening()

    assert init_integration.state is ConfigEntryState.LOADED
    assert len(mock_websocket.sockets) == 2
    assert _coordinator(hass, init_integration).custom_screens == {1: "static text"}

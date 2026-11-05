"""Smoke tests for the HomeBrainz entity platforms."""
from __future__ import annotations

import aiohttp
import pytest
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaPlayerState,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.text import DOMAIN as TEXT_DOMAIN, SERVICE_SET_VALUE as TEXT_SET_VALUE
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_STOP,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.homebrainz.const import DOMAIN

from .conftest import BASE_URL, HOST, FakeWebSocketFactory


def _entity_id(hass: HomeAssistant, platform: str, entry: MockConfigEntry, suffix: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{suffix}"
    )
    assert entity_id is not None, suffix
    return entity_id


def _state(hass: HomeAssistant, platform: str, entry: MockConfigEntry, suffix: str):
    state = hass.states.get(_entity_id(hass, platform, entry, suffix))
    assert state is not None
    return state


async def test_entities_created(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test every platform registers its entities against one device."""
    entries = er.async_entries_for_config_entry(er.async_get(hass), init_integration.entry_id)
    by_domain: dict[str, int] = {}
    for entity in entries:
        by_domain[entity.domain] = by_domain.get(entity.domain, 0) + 1

    assert by_domain == {
        "sensor": 19,
        "number": 1,
        "select": 5,
        "switch": 6,
        "binary_sensor": 1,
        "button": 2,
        "event": 2,
        "media_player": 1,
        "text": 1,
    }
    assert len({entity.device_id for entity in entries}) == 1


async def test_sensor_states(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test sensor values from the polled payloads and the legacy fallbacks."""
    entry = init_integration
    expected = {
        "temperature": "21.5",
        "humidity": "45.2",
        "pressure": "1013.2",
        "wifi_signal": "-55",
        "gas_resistance": "120.5",
        "uptime": "1234",
        "brightness": "8",
        "ip_address": HOST,
        "firmware_version": "1.2.3",
        "firmware_id": "5",
        "latest_firmware_version": "1.2.3",
        "bsec_iaq": "50",
        "bsec_iaq_rating": "good",
    }
    for suffix, value in expected.items():
        assert _state(hass, "sensor", entry, suffix).state == value, suffix

    # Older firmware: AHT20 + BMP280 instead of a BME680.
    await mock_websocket.current.push(
        {
            "type": "sensor_update",
            "data": {
                "aht20": {"temperature": 19.0, "humidity": 40.0},
                "bmp280": {"pressure": 1000.0},
            },
        }
    )
    await hass.async_block_till_done()

    assert _state(hass, "sensor", entry, "temperature").state == "19.0"
    assert _state(hass, "sensor", entry, "humidity").state == "40.0"
    assert _state(hass, "sensor", entry, "pressure").state == "1000.0"
    assert _state(hass, "sensor", entry, "bsec_iaq").state == STATE_UNKNOWN

    await mock_websocket.current.push({"type": "sensor_update", "data": {}})
    await hass.async_block_till_done()
    assert _state(hass, "sensor", entry, "temperature").state == STATE_UNKNOWN


async def test_firmware_update_binary_sensor(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test the update-available binary sensor and its OTA attributes."""
    state = _state(hass, "binary_sensor", init_integration, "firmware_update_available")
    assert state.state == STATE_OFF
    assert state.attributes["current_version"] == "1.2.3"
    assert state.attributes["latest_firmware_id"] == 5


async def test_brightness_number(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test the brightness number sends a clamped value over the WebSocket."""
    entity_id = _entity_id(hass, "number", init_integration, "brightness_level")
    assert hass.states.get(entity_id).state == "8"

    websocket = mock_websocket.current
    websocket.send.reset_mock()
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 12},
        blocking=True,
    )
    assert websocket.sent == [{"command": "set_brightness", "value": 12}]
    assert hass.states.get(entity_id).state == "12"

    hass.data[DOMAIN][init_integration.entry_id]._websocket_connected = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 3},
            blocking=True,
        )


async def test_screen_switches(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test screen switches post the rotation in the fixed screen order."""
    clock = _entity_id(hass, "switch", init_integration, "screen_clock")
    temp = _entity_id(hass, "switch", init_integration, "screen_temp")
    gas = _entity_id(hass, "switch", init_integration, "screen_gas")
    assert hass.states.get(clock).state == STATE_ON
    assert hass.states.get(gas).state == STATE_OFF

    aioclient_mock.post(f"{BASE_URL}/display/screens", json={})
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: gas}, blocking=True
    )
    assert aioclient_mock.mock_calls[-1][2] == {"screens": ["clock", "temp", "gas"]}
    assert hass.states.get(gas).state == STATE_ON

    calls = aioclient_mock.call_count
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: gas}, blocking=True
    )
    assert aioclient_mock.call_count == calls

    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: temp}, blocking=True
    )
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: gas}, blocking=True
    )
    calls = aioclient_mock.call_count
    # The last enabled screen can't be turned off.
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: clock}, blocking=True
    )
    assert aioclient_mock.call_count == calls
    assert hass.states.get(clock).state == STATE_ON

    for mock_kwargs in ({"status": 500}, {"exc": aiohttp.ClientError()}):
        aioclient_mock.clear_requests()
        aioclient_mock.post(f"{BASE_URL}/display/screens", **mock_kwargs)
        await hass.services.async_call(
            "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: gas}, blocking=True
        )
        assert hass.states.get(gas).state == STATE_OFF


async def test_custom_message_text(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test the custom message text sends display_text and gates old firmware."""
    entity_id = _entity_id(hass, "text", init_integration, "custom_text")
    websocket = mock_websocket.current
    websocket.send.reset_mock()

    await hass.services.async_call(
        TEXT_DOMAIN, TEXT_SET_VALUE, {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: "Hi"}, blocking=True
    )
    assert websocket.sent == [{"command": "display_text", "text": "Hi"}]
    assert hass.states.get(entity_id).state == "Hi"

    await websocket.push({"type": "status_update", "data": {"firmware_id": 1}})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_timezone_select(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the timezone select reflects and writes the device config."""
    entity_id = _entity_id(hass, "select", init_integration, "timezone")
    assert hass.states.get(entity_id).state == "Europe/Berlin"

    aioclient_mock.post(f"{BASE_URL}/config", json={})
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "Etc/UTC"},
        blocking=True,
    )
    assert hass.states.get(entity_id).state == "Etc/UTC"

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE_URL}/config", status=500)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "Europe/London"},
            blocking=True,
        )


async def test_button_action_select(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test button gesture selects map HA options to firmware action names."""
    entity_id = _entity_id(hass, "select", init_integration, "button1_singleTap_action")
    assert hass.states.get(entity_id).state == "next_screen"
    assert (
        hass.states.get(
            _entity_id(hass, "select", init_integration, "button2_longPress_action")
        ).state
        == "brightness_down"
    )

    aioclient_mock.post(f"{BASE_URL}/buttons/actions", json={})
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "toggle_screen"},
        blocking=True,
    )
    assert aioclient_mock.mock_calls[-1][2] == {"button1": {"singleTap": "toggleScreen"}}
    assert hass.states.get(entity_id).state == "toggle_screen"

    for mock_kwargs in ({"status": 500}, {"exc": aiohttp.ClientError()}):
        aioclient_mock.clear_requests()
        aioclient_mock.post(f"{BASE_URL}/buttons/actions", **mock_kwargs)
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SELECT_DOMAIN,
                SERVICE_SELECT_OPTION,
                {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "none"},
                blocking=True,
            )


async def test_button_events(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test button_event pushes fire the matching event entity."""
    button1 = _entity_id(hass, "event", init_integration, "button_1_event")
    button2 = _entity_id(hass, "event", init_integration, "button_2_event")
    assert hass.states.get(button1).state == STATE_UNKNOWN

    websocket = mock_websocket.current
    await websocket.push(
        {"type": "button_event", "data": {"button": 1, "gesture": "long_press", "action": "none"}}
    )
    await websocket.push({"type": "button_event", "data": {"button": 2, "gesture": "triple"}})
    await hass.async_block_till_done()

    state = hass.states.get(button1)
    assert state.attributes["event_type"] == "long_press"
    assert state.attributes["action"] == "none"
    assert hass.states.get(button2).state == STATE_UNKNOWN


async def test_firmware_buttons(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the firmware check and install buttons."""
    check = _entity_id(hass, "button", init_integration, "firmware_check")
    install = _entity_id(hass, "button", init_integration, "firmware_update")
    available = _entity_id(hass, "binary_sensor", init_integration, "firmware_update_available")

    with pytest.raises(HomeAssistantError, match="already up to date"):
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: install}, blocking=True
        )

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{BASE_URL}/api/ota/check",
        json={"updateAvailable": True, "latestVersion": "1.3.0", "downloadUrl": "http://fw/bin"},
    )
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: check}, blocking=True
    )
    assert hass.states.get(available).state == STATE_ON

    aioclient_mock.post(f"{BASE_URL}/api/ota/update", json={})
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: install}, blocking=True
    )
    assert aioclient_mock.mock_calls[-1][2] == {"url": "http://fw/bin", "confirm": True}

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{BASE_URL}/api/ota/check",
        json={"updateAvailable": True, "downloadUrl": "http://fw/bin"},
    )
    aioclient_mock.post(f"{BASE_URL}/api/ota/update", status=409, text="busy")
    with pytest.raises(HomeAssistantError, match="rejected the firmware update"):
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: install}, blocking=True
        )


async def test_speaker(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test the speaker uses HTTP and falls back to WebSocket on old firmware."""
    entity_id = _entity_id(hass, "media_player", init_integration, "speaker")
    assert hass.states.get(entity_id).state == MediaPlayerState.IDLE

    aioclient_mock.post(f"{BASE_URL}/speaker/volume", json={"success": True})
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_VOLUME_SET,
        {ATTR_ENTITY_ID: entity_id, ATTR_MEDIA_VOLUME_LEVEL: 0.3},
        blocking=True,
    )
    assert aioclient_mock.mock_calls[-1][2] == {"value": 30}
    assert hass.states.get(entity_id).attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.3

    # Firmware without the HTTP speaker API: commands go over the WebSocket.
    websocket = mock_websocket.current
    websocket.send.reset_mock()
    aioclient_mock.post(f"{BASE_URL}/speaker/mute", status=404)
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_VOLUME_MUTE,
        {ATTR_ENTITY_ID: entity_id, ATTR_MEDIA_VOLUME_MUTED: True},
        blocking=True,
    )
    assert websocket.sent == [{"command": "speaker_mute", "muted": True}]

    aioclient_mock.post(f"{BASE_URL}/speaker/play", json={})
    aioclient_mock.post(f"{BASE_URL}/speaker/pause", json={})
    aioclient_mock.post(f"{BASE_URL}/speaker/stop", json={"success": False})
    sample = f"http://{HOST}/speaker/sample.wav"
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {ATTR_ENTITY_ID: entity_id, ATTR_MEDIA_CONTENT_TYPE: "music", ATTR_MEDIA_CONTENT_ID: sample},
        blocking=True,
    )
    # Device-local media is played natively, not through the audio proxy.
    assert aioclient_mock.mock_calls[-1][2] == {"media_type": "music", "media_id": sample}
    assert hass.states.get(entity_id).state == MediaPlayerState.PLAYING

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_MEDIA_PAUSE, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == MediaPlayerState.IDLE

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_MEDIA_PLAY, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert aioclient_mock.mock_calls[-1][2]["media_id"] == sample
    assert hass.states.get(entity_id).state == MediaPlayerState.PLAYING

    # The device rejecting a command leaves the optimistic state alone.
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_MEDIA_STOP, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == MediaPlayerState.PLAYING

    await websocket.push(
        {
            "type": "status_update",
            "data": {"speaker": {"state": "paused", "volume": 0.6, "muted": False, "title": "News"}},
        }
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == MediaPlayerState.PAUSED
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.6
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is False
    assert state.attributes["media_title"] == "News"


@pytest.mark.xfail(
    reason=(
        "Bug: media_player._speaker_data() uses status.get('speaker', {}), which is "
        "always a dict, so Atmos top-level speaker_* status keys are never read"
    ),
    strict=True,
)
async def test_speaker_atmos_top_level_status(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test Atmos firmware's top-level speaker_* status keys drive the speaker state."""
    entity_id = _entity_id(hass, "media_player", init_integration, "speaker")

    await mock_websocket.current.push(
        {
            "type": "status_update",
            "data": {"speaker_playing": True, "speaker_volume": 80, "speaker_muted": True},
        }
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == MediaPlayerState.PLAYING
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.8
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is True

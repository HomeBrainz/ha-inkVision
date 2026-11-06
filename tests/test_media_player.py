"""Tests for the inkVision speaker media player."""
from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.inkvision.audio_proxy import PROXY_URL_PREFIX
from custom_components.inkvision.const import DOMAIN
from custom_components.inkvision.media_player import _is_local_network_host
from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_TITLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    BrowseMedia,
    MediaClass,
    MediaPlayerState,
)
from homeassistant.components.media_source import PlayMedia, Unresolvable
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
)
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import NoURLAvailableError

from .conftest import BASE_URL, HOST, FakeWebSocketFactory

INTERNAL_URL = "http://192.168.1.10:8123"
STREAM_URL = "http://radio.example.com/stream.mp3"


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        MEDIA_PLAYER_DOMAIN, DOMAIN, f"{entry.unique_id}_speaker"
    )
    assert entity_id
    return entity_id


async def _push_status(mock_websocket: FakeWebSocketFactory, status: dict[str, Any]) -> None:
    await mock_websocket.current.push({"type": "status_update", "data": status})


async def _call(
    hass: HomeAssistant, entry: MockConfigEntry, service: str, **data: Any
) -> None:
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        service,
        {ATTR_ENTITY_ID: _entity_id(hass, entry), **data},
        blocking=True,
    )


async def _play(hass: HomeAssistant, entry: MockConfigEntry, media_id: str, **data: Any) -> None:
    await _call(
        hass,
        entry,
        SERVICE_PLAY_MEDIA,
        **{ATTR_MEDIA_CONTENT_TYPE: "music", ATTR_MEDIA_CONTENT_ID: media_id, **data},
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({}, MediaPlayerState.IDLE),
        ({"speaker": {"state": " Playing "}}, MediaPlayerState.PLAYING),
        ({"speaker_state": "paused"}, MediaPlayerState.PAUSED),
        ({"media_state": "stopped"}, MediaPlayerState.IDLE),
        ({"speaker_state": "unavailable"}, MediaPlayerState.OFF),
        ({"speaker_state": "buffering", "speaker_playing": True}, MediaPlayerState.PLAYING),
        ({"speaker_playing": False}, MediaPlayerState.IDLE),
        ({"speaker_available": False}, MediaPlayerState.OFF),
    ],
)
async def test_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
    status: dict[str, Any],
    expected: MediaPlayerState,
) -> None:
    """Test the state from the nested and the flat speaker status formats."""
    await _push_status(mock_websocket, status)

    assert hass.states.get(_entity_id(hass, init_integration)).state == expected


@pytest.mark.parametrize(
    ("status", "volume", "muted", "title"),
    [
        ({}, 0.5, False, None),
        ({"speaker": {"volume": 0.3, "muted": True, "title": "Radio"}}, 0.3, True, "Radio"),
        ({"speaker_volume": 40, "speaker_muted": False, "media_title": "News"}, 0.4, False, "News"),
        ({"speaker_volume": 250}, 1.0, False, None),
        ({"speaker_volume": -0.5, "media_title": 7}, 0.0, False, None),
    ],
)
async def test_attributes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_websocket: FakeWebSocketFactory,
    status: dict[str, Any],
    volume: float,
    muted: bool,
    title: str | None,
) -> None:
    """Test volume, mute and title, with 0-1 and percent volumes."""
    await _push_status(mock_websocket, status)

    state = hass.states.get(_entity_id(hass, init_integration))
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == volume
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is muted
    assert state.attributes.get(ATTR_MEDIA_TITLE) == title


async def test_controls(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the controls send commands and update the state optimistically."""
    for path in ("mute", "volume", "play", "pause", "stop"):
        aioclient_mock.post(f"{BASE_URL}/speaker/{path}", json={})
    entity_id = _entity_id(hass, init_integration)

    await _call(hass, init_integration, SERVICE_VOLUME_MUTE, **{ATTR_MEDIA_VOLUME_MUTED: True})
    await _call(hass, init_integration, SERVICE_VOLUME_SET, **{ATTR_MEDIA_VOLUME_LEVEL: 0.26})
    state = hass.states.get(entity_id)
    assert state.attributes[ATTR_MEDIA_VOLUME_MUTED] is True
    assert state.attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.26

    await _call(hass, init_integration, SERVICE_MEDIA_PLAY)
    assert hass.states.get(entity_id).state == MediaPlayerState.PLAYING
    await _call(hass, init_integration, SERVICE_MEDIA_PAUSE)
    assert hass.states.get(entity_id).state == MediaPlayerState.IDLE
    await _call(hass, init_integration, SERVICE_MEDIA_PLAY)
    await _call(hass, init_integration, SERVICE_MEDIA_STOP)
    assert hass.states.get(entity_id).state == MediaPlayerState.IDLE

    assert [
        (call[1].path, call[2]) for call in aioclient_mock.mock_calls if call[0].upper() == "POST"
    ] == [
        ("/speaker/mute", {"muted": True}),
        ("/speaker/volume", {"value": 26}),
        ("/speaker/play", {}),
        ("/speaker/pause", {}),
        ("/speaker/play", {}),
        ("/speaker/stop", {}),
    ]


@pytest.mark.parametrize(
    ("service", "data"),
    [
        (SERVICE_VOLUME_MUTE, {ATTR_MEDIA_VOLUME_MUTED: True}),
        (SERVICE_VOLUME_SET, {ATTR_MEDIA_VOLUME_LEVEL: 0.5}),
        (SERVICE_MEDIA_PLAY, {}),
        (SERVICE_MEDIA_PAUSE, {}),
        (SERVICE_MEDIA_STOP, {}),
        (
            SERVICE_PLAY_MEDIA,
            {ATTR_MEDIA_CONTENT_TYPE: "music", ATTR_MEDIA_CONTENT_ID: f"{BASE_URL}/speaker/a.wav"},
        ),
    ],
)
async def test_rejected_command(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    data: dict[str, Any],
) -> None:
    """Test a command the device rejects raises an error."""
    for path in ("mute", "volume", "play", "pause", "stop"):
        aioclient_mock.post(f"{BASE_URL}/speaker/{path}", status=500)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, init_integration, service, **data)
    assert err.value.translation_key == "request_failed"


async def test_play_media_options_and_resume(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test announce and enqueue are passed on, and play resumes the last media."""
    await async_process_ha_core_config(hass, {"internal_url": INTERNAL_URL})
    aioclient_mock.post(f"{BASE_URL}/speaker/play", json={})

    await _play(hass, init_integration, STREAM_URL, **{ATTR_MEDIA_ENQUEUE: "add"})
    assert aioclient_mock.mock_calls[-1][2]["enqueue"] == "add"

    await _play(hass, init_integration, STREAM_URL, **{ATTR_MEDIA_ANNOUNCE: True})
    payload = aioclient_mock.mock_calls[-1][2]
    assert payload["announce"] is True
    first_url = payload["media_id"]
    assert first_url.startswith(f"{INTERNAL_URL}{PROXY_URL_PREFIX}/")

    await _call(hass, init_integration, SERVICE_MEDIA_PLAY)
    payload = aioclient_mock.mock_calls[-1][2]
    # A fresh one-time proxy URL for the same media.
    assert payload["media_type"] == "music"
    assert payload["media_id"].startswith(f"{INTERNAL_URL}{PROXY_URL_PREFIX}/")
    assert payload["media_id"] != first_url


async def test_play_device_sample(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test the device's own files are played directly, not through the proxy."""
    aioclient_mock.post(f"{BASE_URL}/speaker/play", json={})

    await _play(hass, init_integration, f"http://{HOST}/speaker/sample.wav")

    assert aioclient_mock.mock_calls[-1][2]["media_id"] == f"http://{HOST}/speaker/sample.wav"


async def test_play_media_source(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test media sources are resolved, signed and sent through the proxy."""
    await async_process_ha_core_config(hass, {"internal_url": INTERNAL_URL})
    aioclient_mock.post(f"{BASE_URL}/speaker/play", json={})

    with (
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            return_value=PlayMedia(url="/media/local/song.mp3", mime_type="audio/mpeg"),
        ),
        patch("custom_components.inkvision.audio_proxy.AudioProxyData.create") as create,
    ):
        create.return_value = f"{PROXY_URL_PREFIX}/x/y.mp3"
        await _play(hass, init_integration, "media-source://media_source/local/song.mp3")

    source_url = create.call_args.args[1]
    assert source_url.startswith(f"{INTERNAL_URL}/media/local/song.mp3?authSig=")
    assert aioclient_mock.mock_calls[-1][2]["media_id"] == f"{INTERNAL_URL}{PROXY_URL_PREFIX}/x/y.mp3"


async def test_play_unresolvable_media_source(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a media source that can't be resolved fails instead of reaching the device."""
    with (
        patch(
            "homeassistant.components.media_source.async_resolve_media",
            side_effect=Unresolvable("gone"),
        ),
        pytest.raises(HomeAssistantError, match="gone"),
    ):
        await _play(hass, init_integration, "media-source://media_source/local/gone.mp3")

    assert not [call for call in aioclient_mock.mock_calls if call[0].upper() == "POST"]


async def test_https_url_downgrade(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a local HTTPS URL becomes HTTP, and other URLs are passed with a warning."""
    await async_process_ha_core_config(hass, {"internal_url": "https://homeassistant.local:8123"})
    aioclient_mock.post(f"{BASE_URL}/speaker/play", json={})

    await _play(hass, init_integration, "http://radio.example.com/stream.mp3")
    assert aioclient_mock.mock_calls[-1][2]["media_id"].startswith(
        f"http://homeassistant.local:8123{PROXY_URL_PREFIX}/"
    )
    assert "Downgraded local HTTPS media URL" in caplog.text

    with patch(
        "custom_components.inkvision.media_player.get_url", side_effect=NoURLAvailableError
    ):
        await _play(hass, init_integration, "https://radio.example.com/stream.mp3")
    assert aioclient_mock.mock_calls[-1][2]["media_id"] == "https://radio.example.com/stream.mp3"
    assert "requires http://" in caplog.text


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("", False),
        ("clock.local", True),
        ("10.0.0.2", True),
        ("127.0.0.1", True),
        ("169.254.1.1", True),
        ("8.8.8.8", False),
        ("example.com", False),
    ],
)
def test_is_local_network_host(host: str, local: bool) -> None:
    """Test which hosts count as local for the HTTPS downgrade."""
    assert _is_local_network_host(host) is local


async def test_browse_media(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Test browsing lists Home Assistant's media sources, filtered to audio."""
    root = BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id="media-source://",
        media_content_type="",
        title="Media",
        can_play=False,
        can_expand=True,
    )
    client = await hass_ws_client()

    with patch(
        "homeassistant.components.media_source.async_browse_media", return_value=root
    ) as browse:
        await client.send_json_auto_id(
            {"type": "media_player/browse_media", "entity_id": _entity_id(hass, init_integration)}
        )
        response = await client.receive_json()

    assert response["success"], response
    assert response["result"]["media_content_id"] == "media-source://"
    assert browse.call_args.args[1] is None
    content_filter = browse.call_args.kwargs["content_filter"]
    assert content_filter(Mock(media_content_type="audio/mpeg"))
    assert not content_filter(Mock(media_content_type="video/mp4"))

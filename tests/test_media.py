"""Tests for the HomeBrainz audio proxy and playlist resolver."""
from __future__ import annotations

import asyncio
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.homebrainz.audio_proxy import (
    DATA_AUDIO_PROXY,
    FORMAT_MP3,
    FORMAT_WAV,
    PROXY_URL_PREFIX,
    AudioProxyData,
    _redact,
)
from custom_components.homebrainz.const import DOMAIN
from custom_components.homebrainz.playlist import (
    async_resolve_playlist,
    looks_like_playlist_url,
    parse_playlist,
)

from .conftest import BASE_URL, FakeWebSocketFactory

STREAM_URL = "http://radio.example.com/stream.mp3"
PLAYLIST_URL = "http://radio.example.com/listen.pls"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (PLAYLIST_URL, True),
        ("http://radio.example.com/listen.M3U?x=1", True),
        ("http://radio.example.com/live.m3u8", False),
        (STREAM_URL, False),
    ],
)
def test_looks_like_playlist_url(url: str, expected: bool) -> None:
    """Test only .pls/.m3u links count as playlists."""
    assert looks_like_playlist_url(url) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "[playlist]\nTitle1=http://ignored.example.com/\nFile1=http://a.example.com/s\n",
            "http://a.example.com/s",
        ),
        ("#EXTM3U\n#EXTINF:-1,Radio\nhttp://b.example.com/s\n", "http://b.example.com/s"),
        ("# comment\nstream/relative.mp3\n", "http://radio.example.com/stream/relative.mp3"),
        ("#EXTM3U\n#EXT-X-VERSION:3\nseg.ts\n", None),
        ("ftp://c.example.com/s\n", None),
        ("", None),
    ],
    ids=["pls", "m3u", "relative", "hls", "non_http", "empty"],
)
def test_parse_playlist(text: str, expected: str | None) -> None:
    """Test PLS and M3U parsing."""
    assert parse_playlist(text, PLAYLIST_URL) == expected


async def test_resolve_playlist(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test playlist links are followed to the stream they contain."""
    session = async_get_clientsession(hass)
    aioclient_mock.get(PLAYLIST_URL, text=f"[playlist]\nFile1={STREAM_URL}\n")

    assert await async_resolve_playlist(session, PLAYLIST_URL) == STREAM_URL
    assert await async_resolve_playlist(session, STREAM_URL) == STREAM_URL


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        {"status": 404},
        {"exc": aiohttp.ClientError()},
        {"text": "#EXTM3U\n#EXT-X-VERSION:3\n"},
    ],
)
async def test_resolve_playlist_keeps_url_on_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_kwargs: dict
) -> None:
    """Test unreachable or unrecognised playlists are handed to ffmpeg unchanged."""
    aioclient_mock.get(PLAYLIST_URL, **mock_kwargs)

    assert (
        await async_resolve_playlist(async_get_clientsession(hass), PLAYLIST_URL)
        == PLAYLIST_URL
    )


async def test_resolve_playlist_depth_limit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test playlists pointing at playlists stop after a few hops."""
    for hop in range(4):
        aioclient_mock.get(
            f"http://radio.example.com/{hop}.m3u", text=f"http://radio.example.com/{hop + 1}.m3u\n"
        )

    result = await async_resolve_playlist(
        async_get_clientsession(hass), "http://radio.example.com/0.m3u"
    )

    assert result == "http://radio.example.com/3.m3u"


def test_audio_proxy_registry() -> None:
    """Test one-time URLs are per device and capped per device."""
    proxy = AudioProxyData()
    first = proxy.create("dev", STREAM_URL)
    second = proxy.create("dev", STREAM_URL, FORMAT_MP3)
    assert first.startswith(f"{PROXY_URL_PREFIX}/dev/") and first.endswith(".wav")
    assert second.endswith(".mp3")

    first_id = first.rsplit("/", 1)[1].split(".")[0]
    running = MagicMock(returncode=None)
    proxy.find("dev", first_id).proc = running

    proxy.create("dev", STREAM_URL)

    assert proxy.find("dev", first_id) is None
    running.kill.assert_called_once()
    assert proxy.find("other", first_id) is None


def test_redact() -> None:
    """Test credentials in URLs are hidden from logs."""
    assert (
        _redact("http://ha/api/x?authSig=abc&ok=1&token=t")
        == "http://ha/api/x?authSig=REDACTED&ok=1&token=REDACTED"
    )


class _FakeProcess:
    """Minimal asyncio subprocess stand-in for ffmpeg."""

    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode: int | None = None
        self._exit_code = returncode
        self.kill = MagicMock()

    async def wait(self) -> int:
        self.returncode = self._exit_code
        return self.returncode


async def _play_remote_stream(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entity_id = er.async_get(hass).async_get_entity_id(
        "media_player", DOMAIN, f"{entry.entry_id}_speaker"
    )
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_TYPE: "music",
            ATTR_MEDIA_CONTENT_ID: STREAM_URL,
        },
        blocking=True,
    )


async def test_play_media_through_proxy(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
    hass_client_no_auth: ClientSessionGenerator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test remote media is sent to the device as a proxy URL that ffmpeg serves."""
    await async_process_ha_core_config(hass, {"internal_url": "http://192.168.1.10:8123"})
    aioclient_mock.post(f"{BASE_URL}/speaker/play", json={})

    await _play_remote_stream(hass, init_integration)
    media_id = aioclient_mock.mock_calls[-1][2]["media_id"]
    assert media_id.startswith(f"http://192.168.1.10:8123{PROXY_URL_PREFIX}/")
    # Firmware without speaker_wav_stream gets MP3.
    assert media_id.endswith(".mp3")

    # Newer firmware gets WAV.
    await mock_websocket.current.push(
        {"type": "status_update", "data": {"speaker_wav_stream": True}}
    )
    await _play_remote_stream(hass, init_integration)
    media_id = aioclient_mock.mock_calls[-1][2]["media_id"]
    assert media_id.endswith(".wav")
    path = media_id.removeprefix("http://192.168.1.10:8123")

    client = await hass_client_no_auth()
    response = await client.get(f"{path}x")
    assert response.status == HTTPStatus.NOT_FOUND
    response = await client.get(path.replace(".wav", ".mp3"))
    assert response.status == HTTPStatus.NOT_FOUND

    process = _FakeProcess(b"RIFF....WAVEdata", b"Connection refused\n", 1)
    with patch(
        "custom_components.homebrainz.audio_proxy.asyncio.create_subprocess_exec",
        return_value=process,
    ) as mock_exec:
        response = await client.get(path)
        assert response.status == HTTPStatus.OK
        assert response.content_type == "audio/wav"
        assert await response.read() == b"RIFF....WAVEdata"

    args = mock_exec.call_args.args
    assert args[args.index("-i") + 1] == STREAM_URL
    assert "-reconnect" in args
    assert "pcm_s16le" in args
    assert "Audio proxy ffmpeg failed" in caplog.text
    assert "Connection refused" in caplog.text

    conversion = hass.data[DATA_AUDIO_PROXY].find(
        init_integration.entry_id, path.rsplit("/", 1)[1].split(".")[0]
    )
    assert conversion.is_finished
    assert conversion.media_format == FORMAT_WAV


async def test_play_media_without_internal_url(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test media is played directly when Home Assistant has no reachable URL."""
    aioclient_mock.post(f"{BASE_URL}/speaker/play", json={})

    with patch(
        "custom_components.homebrainz.media_player.get_url", side_effect=Exception("no url")
    ):
        await _play_remote_stream(hass, init_integration)

    assert aioclient_mock.mock_calls[-1][2]["media_id"] == STREAM_URL
    assert "No internal Home Assistant URL" in caplog.text

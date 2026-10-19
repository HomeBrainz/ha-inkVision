"""HTTP view that transcodes any media URL into a format Atmos speakers play.

The Atmos firmware can only fetch plain-HTTP streams and has very little RAM
and CPU for decoding. Most real-world sources (internet radio over HTTPS, AAC,
HLS playlists, TTS MP3) are therefore unplayable or play choppily when handed
to the device directly. Instead, Home Assistant runs ffmpeg and serves the
result over plain HTTP:

- WAV (16-bit mono PCM) for firmware that advertises ``speaker_wav_stream``;
  the device plays it without any decoding.
- MP3 for older firmware, whose WAV path plays far quieter than its MP3 path.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
import logging
import re
import secrets
from typing import Final

from aiohttp import web

from homeassistant.components import ffmpeg
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PROXY_URL_PREFIX: Final = "/api/homebrainz/audio_proxy"

# 22.05 kHz mono matches Piper's native rate and keeps the device-side stream
# at ~44 KB/s, while the firmware's I2S DMA still holds ~370 ms of audio.
PROXY_SAMPLE_RATE: Final = 22050
PROXY_CHANNELS: Final = 1

FORMAT_WAV: Final = "wav"
FORMAT_MP3: Final = "mp3"

# Output codec arguments per format. MP3 stays at 22.05 kHz mono (MPEG-2
# Layer III) to keep decoding cheap on the device.
_FORMAT_ARGS: Final[dict[str, list[str]]] = {
    # +bitexact skips the LIST/INFO chunk so the header is just fmt + data.
    FORMAT_WAV: ["-c:a", "pcm_s16le", "-fflags", "+bitexact", "-f", "wav"],
    FORMAT_MP3: ["-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"],
}
_CONTENT_TYPES: Final[dict[str, str]] = {FORMAT_WAV: "audio/wav", FORMAT_MP3: "audio/mpeg"}

_MAX_CONVERSIONS_PER_DEVICE: Final = 2
_MAX_STDERR_LINES: Final = 32
_PROC_WAIT_TIMEOUT: Final = 5
_CHUNK_SIZE: Final = 4096
_SENSITIVE_QUERY_PARAMS: Final = re.compile(
    r"(?<=[?&])(authSig|token|key|password|secret)=[^&\s]+", re.IGNORECASE
)

DATA_AUDIO_PROXY: Final = f"{DOMAIN}_audio_proxy"


def _redact(text: str) -> str:
    return _SENSITIVE_QUERY_PARAMS.sub(r"\1=REDACTED", text)


@dataclass
class _Conversion:
    convert_id: str
    media_url: str
    media_format: str
    proc: asyncio.subprocess.Process | None = None
    is_finished: bool = False


class AudioProxyData:
    """Per-device registry of one-time conversion URLs."""

    def __init__(self) -> None:
        self.conversions: dict[str, list[_Conversion]] = {}

    def create(self, device_id: str, media_url: str, media_format: str = FORMAT_WAV) -> str:
        """Register media_url and return the proxy path that serves it."""
        conversions = [
            c for c in self.conversions.get(device_id, []) if not c.is_finished
        ]
        while len(conversions) >= _MAX_CONVERSIONS_PER_DEVICE:
            oldest = conversions.pop(0)
            if oldest.proc is not None and oldest.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    oldest.proc.kill()

        convert_id = secrets.token_urlsafe(16)
        conversions.append(_Conversion(convert_id, media_url, media_format))
        self.conversions[device_id] = conversions
        return f"{PROXY_URL_PREFIX}/{device_id}/{convert_id}.{media_format}"

    def find(self, device_id: str, convert_id: str) -> _Conversion | None:
        for conversion in self.conversions.get(device_id, []):
            if conversion.convert_id == convert_id:
                return conversion
        return None


class AudioProxyView(HomeAssistantView):
    """Stream a registered media URL through ffmpeg in its target format."""

    # The device cannot authenticate; access is gated by the unguessable id.
    requires_auth = False
    url = PROXY_URL_PREFIX + "/{device_id}/{filename}"
    name = "api:homebrainz:audio_proxy"

    def __init__(self, hass: HomeAssistant, proxy_data: AudioProxyData) -> None:
        self.hass = hass
        self.proxy_data = proxy_data

    async def get(
        self, request: web.Request, device_id: str, filename: str
    ) -> web.StreamResponse:
        convert_id, _, ext = filename.rpartition(".")
        conversion = self.proxy_data.find(device_id, convert_id)
        if conversion is None or conversion.media_format != ext:
            return web.Response(body="Invalid proxy URL", status=HTTPStatus.NOT_FOUND)

        # A re-request restarts the conversion; a new WAV response needs its own
        # header anyway.
        if conversion.proc is not None and conversion.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                conversion.proc.kill()
            conversion.proc = None

        manager = ffmpeg.get_ffmpeg_manager(self.hass)
        args = ["-hide_banner", "-nostats", "-loglevel", "error"]
        if conversion.media_url.lower().startswith(("http://", "https://")):
            # Live radio servers occasionally drop the connection; let ffmpeg
            # reconnect instead of ending playback.
            args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        args += [
            "-i",
            conversion.media_url,
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            str(PROXY_CHANNELS),
            "-ar",
            str(PROXY_SAMPLE_RATE),
            *_FORMAT_ARGS[conversion.media_format],
            "pipe:",
        ]

        _LOGGER.debug("Audio proxy: %s %s", manager.binary, _redact(" ".join(args)))
        proc = await asyncio.create_subprocess_exec(
            manager.binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=False,
        )
        conversion.proc = proc

        response = web.StreamResponse(status=HTTPStatus.OK)
        response.content_type = _CONTENT_TYPES[conversion.media_format]
        await response.prepare(request)

        stderr_lines: deque[str] = deque(maxlen=_MAX_STDERR_LINES)
        stderr_task = self.hass.async_create_background_task(
            _collect_stderr(proc, stderr_lines), "homebrainz audio proxy stderr"
        )

        assert proc.stdout is not None
        try:
            while (
                self.hass.is_running
                and request.transport is not None
                and not request.transport.is_closing()
                and (chunk := await proc.stdout.read(_CHUNK_SIZE))
            ):
                await response.write(chunk)
        except (ConnectionResetError, RuntimeError, OSError):
            # The device stopped playback or started another stream.
            pass
        except asyncio.CancelledError:
            if request.transport:
                request.transport.abort()
            raise
        finally:
            conversion.is_finished = True
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=_PROC_WAIT_TIMEOUT)
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

            # -9 is our own kill when the client goes away; anything else
            # positive is a real ffmpeg failure worth surfacing.
            if proc.returncode is not None and proc.returncode > 0:
                _LOGGER.error(
                    "Audio proxy ffmpeg failed for %s (code %s):\n%s",
                    _redact(conversion.media_url),
                    proc.returncode,
                    _redact("\n".join(stderr_lines)),
                )

        if request.transport and not request.transport.is_closing():
            with contextlib.suppress(ConnectionResetError, RuntimeError, OSError):
                await response.write_eof()
        return response


async def _collect_stderr(proc: asyncio.subprocess.Process, lines: deque[str]) -> None:
    assert proc.stderr is not None
    while chunk := await proc.stderr.readline():
        lines.append(chunk.decode(errors="replace").rstrip())


@callback
def async_setup_audio_proxy(hass: HomeAssistant) -> AudioProxyData:
    """Register the proxy view once and return its registry."""
    if (proxy_data := hass.data.get(DATA_AUDIO_PROXY)) is None:
        proxy_data = AudioProxyData()
        hass.data[DATA_AUDIO_PROXY] = proxy_data
        hass.http.register_view(AudioProxyView(hass, proxy_data))
    return proxy_data

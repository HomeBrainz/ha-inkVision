"""Resolve internet-radio playlist links (.pls / .m3u) to their stream URL.

ffmpeg plays HLS (.m3u8) natively but treats SHOUTcast/Icecast style
playlists as plain text, so a station added by its playlist link would fail
in the audio proxy. These are fetched and replaced by the first stream entry.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Final
from urllib.parse import urljoin, urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

_PLAYLIST_EXTENSIONS: Final = (".pls", ".m3u")
_MAX_PLAYLIST_BYTES: Final = 64 * 1024
_MAX_DEPTH: Final = 3
_TIMEOUT: Final = 10


def looks_like_playlist_url(url: str) -> bool:
    """Return True for .pls/.m3u links (but not HLS .m3u8)."""
    return urlparse(url).path.lower().endswith(_PLAYLIST_EXTENSIONS)


def parse_playlist(text: str, base_url: str) -> str | None:
    """Return the first stream URL in a PLS or M3U playlist, or None.

    HLS playlists (#EXT-X- tags) return None: ffmpeg plays those itself.
    """
    if "#EXT-X-" in text:
        return None

    entries: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, sep, value = line.partition("=")
        if sep and key.lower().startswith("file"):
            # PLS: File1=http://...
            entries.append(value.strip())
        elif line.lower().startswith(("http://", "https://")) or not sep:
            # M3U: one URL (or relative path) per line; PLS metadata such as
            # Title1=... is skipped even when it contains a URL.
            entries.append(line)

    for entry in entries:
        url = urljoin(base_url, entry)
        if urlparse(url).scheme in ("http", "https"):
            return url
    return None


async def async_resolve_playlist(session: aiohttp.ClientSession, url: str) -> str:
    """Follow .pls/.m3u links to the actual stream; return url unchanged otherwise."""
    for _ in range(_MAX_DEPTH):
        if not looks_like_playlist_url(url):
            return url
        try:
            async with asyncio.timeout(_TIMEOUT):
                async with session.get(url) as response:
                    response.raise_for_status()
                    body = await response.content.read(_MAX_PLAYLIST_BYTES)
                    final_url = str(response.url)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Could not fetch playlist %s: %s", url, err)
            return url

        stream_url = parse_playlist(body.decode(errors="replace"), final_url)
        if stream_url is None:
            # HLS or unrecognised: let ffmpeg try the original link.
            return url
        _LOGGER.debug("Playlist %s resolved to %s", url, stream_url)
        url = stream_url
    return url

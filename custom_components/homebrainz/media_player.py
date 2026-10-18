"""Media player platform for HomeBrainz integration."""
from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HomeBrainzDataUpdateCoordinator
from .audio_proxy import async_setup_audio_proxy
from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)


def _is_local_network_host(hostname: str) -> bool:
    """Return True if hostname refers to a local-network address."""
    if not hostname:
        return False
    if hostname.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


SPEAKER_FEATURES = (
    MediaPlayerEntityFeature.BROWSE_MEDIA
    |
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PLAY_MEDIA
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the media_player platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([HomeBrainzSpeakerEntity(coordinator, config_entry)])


class HomeBrainzSpeakerEntity(CoordinatorEntity, MediaPlayerEntity):
    """HomeBrainz speaker exposed as a Home Assistant media player."""

    _attr_name = "Speaker"
    _attr_supported_features = SPEAKER_FEATURES

    def __init__(
        self,
        coordinator: HomeBrainzDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the media player entity."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._attr_unique_id = f"{config_entry.entry_id}_speaker"

        # Optimistic fallback state for older firmware responses.
        self._optimistic_state = MediaPlayerState.IDLE
        self._optimistic_volume_level = 0.5
        self._optimistic_is_muted = False
        self._last_media_id: str | None = None
        self._last_media_type: str = str(MediaType.MUSIC)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_name = "HomeBrainz Clock"
        mac_address = ""

        status = {}
        if self.coordinator.data:
            status = self.coordinator.data.get("status", {}) or {}
            if isinstance(status, dict):
                device_name = status.get("device", device_name)
                mac_address = status.get("mac_address", mac_address)

        return DeviceInfo(
            identifiers={(DOMAIN, mac_address or self._host)},
            name=device_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=status.get("version", "Unknown") if isinstance(status, dict) else "Unknown",
            configuration_url=f"http://{self._host}",
        )

    def _status(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        status = self.coordinator.data.get("status", {})
        return status if isinstance(status, dict) else {}

    def _speaker_data(self) -> dict[str, Any]:
        status = self._status()
        speaker = status.get("speaker", {})
        if isinstance(speaker, dict):
            return speaker

        # Atmos firmware reports speaker values as top-level status keys.
        merged: dict[str, Any] = {}
        if "speaker_playing" in status:
            merged["playing"] = status.get("speaker_playing")
        if "speaker_muted" in status:
            merged["muted"] = status.get("speaker_muted")
        if "speaker_volume" in status:
            merged["volume"] = status.get("speaker_volume")
        if "speaker_available" in status:
            merged["available"] = status.get("speaker_available")
        return merged

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success

    @property
    def state(self) -> MediaPlayerState:
        """Return the current playback state."""
        status = self._status()
        speaker = self._speaker_data()

        raw_state = (
            speaker.get("state")
            or status.get("speaker_state")
            or status.get("media_state")
        )

        if isinstance(raw_state, str):
            normalized = raw_state.strip().lower()
            if normalized == "playing":
                return MediaPlayerState.PLAYING
            if normalized == "paused":
                return MediaPlayerState.PAUSED
            if normalized in ("off", "idle", "stopped", "stop"):
                return MediaPlayerState.IDLE
            if normalized == "unavailable":
                return MediaPlayerState.OFF

        raw_playing = speaker.get("playing")
        if isinstance(raw_playing, bool):
            return MediaPlayerState.PLAYING if raw_playing else MediaPlayerState.IDLE

        raw_available = speaker.get("available")
        if isinstance(raw_available, bool) and not raw_available:
            return MediaPlayerState.OFF

        return self._optimistic_state

    @property
    def volume_level(self) -> float | None:
        """Volume level in range 0.0..1.0."""
        status = self._status()
        speaker = self._speaker_data()

        raw_volume = speaker.get("volume")
        if raw_volume is None:
            raw_volume = status.get("speaker_volume")

        if isinstance(raw_volume, (int, float)):
            if raw_volume > 1:
                return max(0.0, min(1.0, float(raw_volume) / 100.0))
            return max(0.0, min(1.0, float(raw_volume)))

        return self._optimistic_volume_level

    @property
    def is_volume_muted(self) -> bool | None:
        """Return muted state."""
        status = self._status()
        speaker = self._speaker_data()

        raw_muted = speaker.get("muted")
        if raw_muted is None:
            raw_muted = status.get("speaker_muted")

        if isinstance(raw_muted, bool):
            return raw_muted

        return self._optimistic_is_muted

    @property
    def media_title(self) -> str | None:
        """Return current media title when available."""
        status = self._status()
        speaker = self._speaker_data()
        title = speaker.get("title") or status.get("media_title")
        return title if isinstance(title, str) else None

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute/unmute playback."""
        success = await self.coordinator.async_speaker_command("mute", muted=mute)
        if success:
            self._optimistic_is_muted = mute
            self.async_write_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level."""
        volume = max(0.0, min(1.0, volume))
        percent = int(round(volume * 100))
        success = await self.coordinator.async_speaker_command("set_volume", value=percent)
        if success:
            self._optimistic_volume_level = volume
            self.async_write_ha_state()

    async def async_media_play(self) -> None:
        """Resume playback."""
        if self._last_media_id:
            # Re-resolve: proxy URLs are one-time and may have expired.
            success = await self.coordinator.async_speaker_command(
                "play_media",
                media_type=self._last_media_type,
                media_id=await self._resolve_media_id(self._last_media_id),
            )
        else:
            success = await self.coordinator.async_speaker_command("play")
        if success:
            self._optimistic_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        """Pause playback."""
        success = await self.coordinator.async_speaker_command("pause")
        if success:
            # Current firmware maps pause to stop.
            self._optimistic_state = MediaPlayerState.IDLE
            self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Stop playback."""
        success = await self.coordinator.async_speaker_command("stop")
        if success:
            self._optimistic_state = MediaPlayerState.IDLE
            self.async_write_ha_state()

    async def async_play_media(
        self,
        media_type: MediaType | str,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        """Play media by id/url."""
        resolved_media_id = await self._resolve_media_id(media_id)

        payload: dict[str, Any] = {
            "media_type": str(media_type),
            "media_id": resolved_media_id,
        }

        announce = kwargs.get("announce")
        if isinstance(announce, bool):
            payload["announce"] = announce

        enqueue = kwargs.get("enqueue")
        if enqueue is not None:
            payload["enqueue"] = str(enqueue)

        success = await self.coordinator.async_speaker_command("play_media", **payload)
        if success:
            self._last_media_id = media_id
            self._last_media_type = str(media_type)
            self._optimistic_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()

    async def _resolve_media_id(self, media_id: str) -> str:
        """Resolve a media ID to a URL the device can fetch and play.

        Everything except the device's own built-in sample is routed through
        the ffmpeg audio proxy, so the firmware always receives plain-HTTP PCM
        WAV regardless of the source's scheme, codec or container.
        """
        source_url = await self._resolve_source_url(media_id)
        if source_url is None:
            return media_id

        if (urlparse(source_url).hostname or "") == self._host:
            # Device-local URLs (e.g. /speaker/sample.wav) are played natively.
            return source_url

        proxy_path = async_setup_audio_proxy(self.hass).create(
            self.config_entry.entry_id, source_url
        )
        try:
            base = get_url(self.hass, prefer_external=False)
        except Exception:
            _LOGGER.warning("No internal Home Assistant URL; playing media directly")
            return self._to_device_url(source_url)

        return self._to_device_url(f"{base}{proxy_path}")

    async def _resolve_source_url(self, media_id: str) -> str | None:
        """Resolve media-source IDs and relative paths to an absolute URL."""
        resolved = media_id

        if media_id.startswith("media-source://"):
            try:
                from homeassistant.components.media_source import async_resolve_media

                media = await async_resolve_media(self.hass, media_id, self.entity_id)
                resolved = media.url
            except Exception as err:
                _LOGGER.error("Failed to resolve %s: %s", media_id, err)
                return None

        try:
            from homeassistant.components.media_player.browse_media import async_process_play_media_url

            # Signs HA-local paths (authSig) and makes them absolute.
            resolved = async_process_play_media_url(self.hass, resolved)
        except Exception:
            if resolved.startswith("/"):
                try:
                    resolved = f"{get_url(self.hass, prefer_external=False)}{resolved}"
                except Exception:
                    return None

        return resolved

    def _to_device_url(self, url: str) -> str:
        """Downgrade local HTTPS to HTTP; the firmware supports plain HTTP only."""
        if url.startswith("https://"):
            parsed = urlparse(url)
            if _is_local_network_host(parsed.hostname or ""):
                url = urlunparse(parsed._replace(scheme="http"))
                _LOGGER.warning(
                    "Downgraded local HTTPS media URL to HTTP for Atmos device compatibility: %s",
                    url,
                )

        if not url.startswith("http://"):
            _LOGGER.warning(
                "Unsupported media URL for Atmos speaker (requires http://): %s",
                url,
            )

        return url

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Browse Home Assistant media sources for this player."""
        try:
            from homeassistant.components import media_source

            if not media_content_id:
                media_content_id = "media-source://"

            return await media_source.async_browse_media(self.hass, media_content_id)
        except Exception as err:
            _LOGGER.error("Failed to browse media for HomeBrainz speaker: %s", err)
            raise

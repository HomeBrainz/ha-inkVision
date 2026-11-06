"""Data update coordinator for inkVision devices."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import logging
import re

from pyinkvision import (
    InkVisionClient,
    InkVisionConnectionError,
    InkVisionError,
    InkVisionResponseError,
    InkVisionUnsupportedError,
    InkVisionWebSocket,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CUSTOM_SCREEN_SLOTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

type InkVisionConfigEntry = ConfigEntry[InkVisionDataUpdateCoordinator]

UPDATE_INTERVAL = timedelta(seconds=300)  # 5 minute fallback polling (WebSocket is primary)
WEBSOCKET_RETRY_DELAY = 10  # seconds
WEBSOCKET_MAX_RETRY_DELAY = 30  # seconds

_MAC = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")


def normalize_mac(value: str) -> str | None:
    """Return a MAC the way format_mac writes it, or None if value is no MAC."""
    mac = format_mac(value)
    return mac if _MAC.fullmatch(mac) else None


def device_key(entry: ConfigEntry) -> str:
    """Return the device identifier, which also prefixes the entry's entity unique IDs.

    It is the device's MAC, or the entry ID for devices that were added without one.
    """
    if entry.unique_id and _MAC.fullmatch(entry.unique_id):
        return entry.unique_id
    return entry.entry_id


class InkVisionDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the inkVision device via WebSocket."""

    config_entry: InkVisionConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: InkVisionConfigEntry) -> None:
        """Initialize."""
        host = config_entry.data["host"]
        self.host = host
        self.device_key = device_key(config_entry)
        self.client = InkVisionClient(host, async_get_clientsession(hass))
        self._websocket: InkVisionWebSocket | None = None
        self._retry_count = 0
        self._websocket_task = None
        self._websocket_connected = False
        # monotonic time of the last successful HTTP poll; see async_poll_if_stale
        self._last_poll: float | None = None
        # Callbacks for "button_event" pushes (the event platform registers here)
        self._button_listeners: list = []
        # Custom screen text per slot (1-based). The device keeps it in RAM only,
        # so every slot is re-sent whenever the WebSocket (re)connects.
        self.custom_screens: dict[int, str] = {}
        # GET /buttons/actions: {"button1": {"singleTap": ..., "longPress": ...}, "button2": {...}}.
        # Kept out of self.data, which every WebSocket push rebuilds from a fixed set of keys.
        self.button_actions: dict = {}
        
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,  # Fallback polling
        )

    async def async_poll_if_stale(self, _now=None) -> None:
        """Run the HTTP poll if it has not succeeded within UPDATE_INTERVAL.

        Every WebSocket push goes through async_set_updated_data(), which resets
        the coordinator's own refresh timer. With a live WebSocket the scheduled
        poll therefore never fires, and the status and OTA data it alone fetches
        (firmware availability, version, uptime) would stay frozen at startup.
        """
        loop_time = self.hass.loop.time()
        if self._last_poll is not None and loop_time - self._last_poll < UPDATE_INTERVAL.total_seconds():
            return
        await self.async_refresh()

    async def async_start_websocket(self):
        """Start the WebSocket connection."""
        if self._websocket_task is None or self._websocket_task.done():
            self._websocket_task = asyncio.create_task(self._websocket_handler())

    async def async_stop_websocket(self):
        """Stop the WebSocket connection."""
        if self._websocket_task:
            self._websocket_task.cancel()
            try:
                await self._websocket_task
            except asyncio.CancelledError:
                pass
        
        if self._websocket:
            await self._websocket.close()
            self._websocket = None

    async def _websocket_handler(self):
        """Handle WebSocket connection and messages."""
        while True:
            try:
                _LOGGER.debug("Attempting to connect to the WebSocket of %s", self.host)

                async with self.client.websocket() as websocket:
                    self._websocket = websocket
                    self._websocket_connected = True
                    if self._retry_count:
                        _LOGGER.info("WebSocket reconnected to inkVision device at %s", self.host)
                    else:
                        _LOGGER.debug("WebSocket connected to inkVision device at %s", self.host)
                    self._retry_count = 0

                    # Request initial data
                    await self._send_websocket_command({"command": "get_status"})
                    await self._send_websocket_command({"command": "get_sensors"})
                    await self._async_push_custom_screens()

                    # Listen for messages; ends with InkVisionConnectionError.
                    async for data in websocket:
                        try:
                            await self._handle_websocket_message(data)
                        except Exception as err:
                            _LOGGER.error("Error processing WebSocket message: %s", err)

            except InkVisionConnectionError as err:
                was_connected = self._websocket_connected
                self._websocket_connected = False
                self._websocket = None
                self._retry_count += 1

                if was_connected:
                    # Poll over HTTP right away so entities go unavailable now
                    # if the device is gone, rather than at the next 5 minute poll.
                    await self.async_request_refresh()

                delay = min(WEBSOCKET_RETRY_DELAY * self._retry_count, WEBSOCKET_MAX_RETRY_DELAY)
                # Log once per outage; repeated attempts while the device is off stay at debug.
                log = _LOGGER.warning if self._retry_count == 1 else _LOGGER.debug
                log(
                    "WebSocket connection to %s lost (%s), retrying every %d-%d seconds",
                    self.host, err, WEBSOCKET_RETRY_DELAY, WEBSOCKET_MAX_RETRY_DELAY,
                )
                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                _LOGGER.debug("WebSocket handler cancelled")
                break
            except Exception:
                _LOGGER.exception("Unexpected error in WebSocket handler")
                self._websocket_connected = False
                self._websocket = None
                await asyncio.sleep(WEBSOCKET_RETRY_DELAY)

    async def _send_websocket_command(self, command: dict):
        """Send a command to the WebSocket."""
        if not self._websocket or self._websocket.closed:
            _LOGGER.debug("Skipping send; WebSocket not connected: %s", command)
            return

        try:
            await self._websocket.send(command)
            _LOGGER.debug("Sent WebSocket command: %s", command)
        except InkVisionConnectionError as err:
            _LOGGER.warning("WebSocket send failed (%s); marking connection closed", err)
            self._websocket_connected = False
            self._websocket = None

    async def _handle_websocket_message(self, data: dict):
        """Handle incoming WebSocket message."""
        message_type = data.get("type")
        _LOGGER.debug("Received WebSocket message type: %s", message_type)
        
        if message_type == "sensor_update":
            # Real-time sensor data update
            raw_sensor_data = data.get("data", {}) or {}
            if not isinstance(raw_sensor_data, dict):
                raw_sensor_data = {}

            sensors_available = data.get("sensors_available")
            if sensors_available is not None:
                raw_sensor_data.setdefault("sensors_available", sensors_available)

            existing = self.data or {"sensors": {}, "status": {}, "screens": [], "ota": {}, "config": {}}
            current_status = deepcopy(existing.get("status", {}))
            screens_snapshot = deepcopy(existing.get("screens", []))
            ota_snapshot = deepcopy(existing.get("ota", {}))
            config_snapshot = deepcopy(existing.get("config", {}))

            timestamp = data.get("timestamp")
            if timestamp is not None:
                current_status["last_sensor_update"] = timestamp

            _LOGGER.debug("Updating sensor data via WebSocket: %s", raw_sensor_data)
            self.async_set_updated_data({
                "sensors": raw_sensor_data,
                "status": current_status,
                "screens": screens_snapshot,
                "ota": ota_snapshot,
                "config": config_snapshot,
            })
            
        elif message_type == "status_update":
            # Status update from device
            status_payload = data.get("data", {}) or {}
            if not isinstance(status_payload, dict):
                status_payload = {}

            timestamp = data.get("timestamp")
            if timestamp is not None:
                status_payload.setdefault("last_status_update", timestamp)

            _LOGGER.debug("Received status update: %s", status_payload)

            existing = self.data or {"sensors": {}, "status": {}, "screens": [], "ota": {}, "config": {}}
            sensors_snapshot = deepcopy(existing.get("sensors", {}))
            screens_snapshot = deepcopy(existing.get("screens", []))
            ota_snapshot = deepcopy(existing.get("ota", {}))
            config_snapshot = deepcopy(existing.get("config", {}))
            # Merge: pushes carry a subset of /status (e.g. no firmware_id on some builds).
            status_snapshot = deepcopy(existing.get("status", {}))
            status_snapshot.update(status_payload)

            self.async_set_updated_data({
                "sensors": sensors_snapshot,
                "status": status_snapshot,
                "screens": screens_snapshot,
                "ota": ota_snapshot,
                "config": config_snapshot,
            })

            if not sensors_snapshot:
                await self._send_websocket_command({"command": "get_sensors"})
                
        elif message_type == "button_event":
            event_data = data.get("data", {}) or {}
            if isinstance(event_data, dict):
                for listener in list(self._button_listeners):
                    listener(event_data)

        elif message_type == "ping":
            # Respond to ping with pong
            await self._send_websocket_command({"type": "pong", "timestamp": data.get("timestamp")})
            
        elif data.get("command"):
            # Response to a command we sent
            command = data.get("command")
            success = data.get("success", False)
            
            if success and command == "get_sensors":
                sensor_data = data.get("data", {}) or {}
                if not isinstance(sensor_data, dict):
                    sensor_data = {}

                existing = self.data or {"sensors": {}, "status": {}, "screens": [], "ota": {}, "config": {}}
                current_status = deepcopy(existing.get("status", {}))
                screens_snapshot = deepcopy(existing.get("screens", []))
                ota_snapshot = deepcopy(existing.get("ota", {}))
                config_snapshot = deepcopy(existing.get("config", {}))

                self.async_set_updated_data({
                    "sensors": sensor_data,
                    "status": current_status,
                    "screens": screens_snapshot,
                    "ota": ota_snapshot,
                    "config": config_snapshot,
                })

            elif success and command == "get_status":
                status_data = data.get("data", {}) or {}
                if not isinstance(status_data, dict):
                    status_data = {}

                existing = self.data or {"sensors": {}, "status": {}, "screens": [], "ota": {}, "config": {}}
                sensors_snapshot = deepcopy(existing.get("sensors", {}))
                screens_snapshot = deepcopy(existing.get("screens", []))
                ota_snapshot = deepcopy(existing.get("ota", {}))
                config_snapshot = deepcopy(existing.get("config", {}))
                # Merge: the WebSocket get_status reply omits fields that /status has.
                status_snapshot = deepcopy(existing.get("status", {}))
                status_snapshot.update(status_data)

                self.async_set_updated_data({
                    "sensors": sensors_snapshot,
                    "status": status_snapshot,
                    "screens": screens_snapshot,
                    "ota": ota_snapshot,
                    "config": config_snapshot,
                })
            elif command == "set_custom_screen":
                # Echoes slot/text; nothing to merge into status.
                pass
            elif success:
                response_data = data.get("data", {}) or {}
                if isinstance(response_data, dict) and response_data:
                    existing = self.data or {"sensors": {}, "status": {}, "screens": [], "ota": {}, "config": {}}
                    sensors_snapshot = deepcopy(existing.get("sensors", {}))
                    status_snapshot = deepcopy(existing.get("status", {}))
                    screens_snapshot = deepcopy(existing.get("screens", []))
                    ota_snapshot = deepcopy(existing.get("ota", {}))
                    config_snapshot = deepcopy(existing.get("config", {}))
                    status_snapshot.update(response_data)
                    self.async_set_updated_data({
                        "sensors": sensors_snapshot,
                        "status": status_snapshot,
                        "screens": screens_snapshot,
                        "ota": ota_snapshot,
                        "config": config_snapshot,
                    })

    def error(self, translation_key: str, **placeholders: str) -> HomeAssistantError:
        """Return a translated error whose message names this device."""
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={"device": self.config_entry.title, **placeholders},
        )

    async def send_device_command(self, command: str, **kwargs):
        """Send a command to the device."""
        if self._websocket_connected:
            cmd_data = {"command": command, **kwargs}
            await self._send_websocket_command(cmd_data)
            return True
        else:
            _LOGGER.warning("Cannot send command %s: WebSocket not connected", command)
            return False

    @callback
    def async_add_button_listener(self, listener) -> callable:
        """Register a callback for button gestures; returns the unsubscribe function."""
        self._button_listeners.append(listener)

        @callback
        def remove() -> None:
            self._button_listeners.remove(listener)

        return remove

    async def async_set_button_action(self, button: str, gesture: str, action: str) -> bool:
        """Set what one gesture does on the device, e.g. ("button1", "longPress", "nextScreen")."""
        try:
            await self.client.set_button_action(button, gesture, action)
        except InkVisionError as err:
            _LOGGER.error("Failed to set %s %s action: %s", button, gesture, err)
            return False

        self.button_actions.setdefault(button, {})[gesture] = action
        self.async_update_listeners()
        return True

    @property
    def websocket_connected(self) -> bool:
        """True while the WebSocket to the device is open."""
        return self._websocket_connected

    @property
    def custom_screens_supported(self) -> bool:
        """True when the firmware reports custom rotation screen slots."""
        status = (self.data or {}).get("status", {}) or {}
        slots = status.get("custom_screens")
        return isinstance(slots, int) and slots > 0

    async def async_set_custom_screen(self, slot: int, text: str) -> None:
        """Remember a slot's text and send it now if the device is reachable."""
        self.custom_screens[slot] = text
        if self._websocket_connected and self.custom_screens_supported:
            await self.send_device_command("set_custom_screen", slot=slot, text=text)

    async def _async_push_custom_screens(self) -> None:
        """Send every slot, empty ones included, so stale text from before a reload is cleared."""
        if not self.custom_screens_supported:
            return
        for slot in range(1, CUSTOM_SCREEN_SLOTS + 1):
            await self._send_websocket_command(
                {"command": "set_custom_screen", "slot": slot, "text": self.custom_screens.get(slot, "")}
            )

    async def async_clear_custom_screens(self) -> None:
        """Clear the device's slots so it doesn't show stale values while unloaded."""
        if not self._websocket_connected or not self.custom_screens_supported:
            return
        for slot in range(1, CUSTOM_SCREEN_SLOTS + 1):
            await self._send_websocket_command({"command": "set_custom_screen", "slot": slot, "text": ""})

    async def async_speaker_command(self, action: str, **kwargs) -> bool:
        """Send a speaker command via HTTP first, then WebSocket fallback."""
        payload = {key: value for key, value in kwargs.items() if value is not None}
        ws_command_by_action = {
            "play": "speaker_play",
            "pause": "speaker_pause",
            "stop": "speaker_stop",
            "set_volume": "speaker_set_volume",
            "mute": "speaker_mute",
            "play_media": "speaker_play_media",
        }

        ws_command = ws_command_by_action.get(action)
        if ws_command is None:
            _LOGGER.error("Unknown speaker action requested: %s", action)
            return False

        try:
            await self.client.speaker_command(action, **payload)
        except (InkVisionUnsupportedError, InkVisionConnectionError) as err:
            # Older firmware takes speaker commands only over the WebSocket.
            _LOGGER.debug("Speaker HTTP command %s failed (%s); trying the WebSocket", action, err)
        except InkVisionResponseError as err:
            _LOGGER.error("Speaker command %s failed: %s", action, err)
            return False
        else:
            return True

        return await self.send_device_command(ws_command, **payload)

    async def async_fetch_ota_status(self) -> dict:
        """Fetch OTA status via HTTP."""
        try:
            return await self.client.get_ota_status()
        except InkVisionError as err:
            _LOGGER.debug("OTA check failed: %s", err)
        return {}

    async def async_fetch_config(self) -> dict:
        """Fetch device config via HTTP."""
        try:
            return await self.client.get_config()
        except InkVisionError as err:
            _LOGGER.debug("Config fetch failed: %s", err)
        return {}

    async def async_set_timezone(self, timezone: str) -> bool:
        """Set timezone on the device via HTTP POST /config."""
        try:
            await self.client.set_timezone(timezone)
        except InkVisionError as err:
            _LOGGER.error("Failed to set timezone: %s", err)
            return False
        # Optimistically update coordinator data
        new_data = deepcopy(self.data or {})
        new_data.setdefault("config", {})["timeZone"] = timezone
        self.async_set_updated_data(new_data)
        return True

    async def async_update_ota_status(self) -> None:
        """Refresh OTA status and merge into coordinator data."""
        ota_data = await self.async_fetch_ota_status()
        if not ota_data:
            return

        existing = self.data or {"sensors": {}, "status": {}, "screens": [], "ota": {}, "config": {}}
        sensors_snapshot = deepcopy(existing.get("sensors", {}))
        status_snapshot = deepcopy(existing.get("status", {}))
        screens_snapshot = deepcopy(existing.get("screens", []))
        config_snapshot = deepcopy(existing.get("config", {}))

        self.async_set_updated_data({
            "sensors": sensors_snapshot,
            "status": status_snapshot,
            "screens": screens_snapshot,
            "ota": ota_data,
            "config": config_snapshot,
        })

    async def async_install_firmware(self) -> str:
        """Re-check for an update, start installing it, and return its version.

        The device downloads and flashes the image itself, then reboots.
        """
        # The cached OTA data can be hours old; always act on a fresh check.
        await self.async_update_ota_status()
        ota = (self.data or {}).get("ota", {})
        if not isinstance(ota, dict) or not ota:
            raise self.error("update_check_failed")
        if ota.get("error"):
            raise self.error("update_check_error", error=str(ota["error"]))
        if not ota.get("updateAvailable"):
            raise self.error("firmware_up_to_date", version=str(ota.get("currentVersion", "unknown")))

        download_url = ota.get("downloadUrl")
        if not download_url:
            raise self.error("no_download_url")

        try:
            await self.client.start_ota_update(download_url)
        except InkVisionResponseError as err:
            raise self.error(
                "firmware_update_rejected", status=str(err.status), details=err.body
            ) from err
        except InkVisionConnectionError as err:
            raise self.error("communication_error", error=str(err)) from err

        _LOGGER.info(
            "Firmware update to %s started on %s; the device reboots when done",
            ota.get("latestVersion"),
            self.host,
        )
        return str(ota.get("latestVersion"))

    async def _async_update_data(self):
        """Update data via HTTP (fallback when WebSocket is not available)."""
        if self._websocket_connected:
            _LOGGER.debug("WebSocket active, augmenting data via HTTP polling")
        else:
            _LOGGER.debug("WebSocket not connected, using HTTP polling")
        try:
            sensor_data = await self.client.get_sensors()
            status_data = await self.client.get_status()
        except InkVisionResponseError as err:
            if err.status not in (None, 200):
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="update_failed_status",
                    translation_placeholders={"status": str(err.status)},
                ) from err
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except InkVisionConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        existing = self.data or {"screens": [], "ota": {}}
        try:
            screens = await self.client.get_screens()
        except InkVisionError:
            _LOGGER.debug("Unable to fetch screen rotation; keeping cached values")
            screens = existing.get("screens", [])

        try:
            self.button_actions = await self.client.get_button_actions()
        except InkVisionError:
            _LOGGER.debug("Unable to fetch button actions; keeping cached values")

        ota_data = await self.async_fetch_ota_status()
        if not ota_data:
            ota_data = existing.get("ota", {}) if isinstance(existing, dict) else {}

        config_data = await self.async_fetch_config()
        if not config_data:
            config_data = existing.get("config", {}) if isinstance(existing, dict) else {}

        self._last_poll = self.hass.loop.time()
        return {
            "sensors": sensor_data,
            "status": status_data,
            "screens": screens,
            "ota": ota_data if isinstance(ota_data, dict) else {},
            "config": config_data if isinstance(config_data, dict) else {},
        }

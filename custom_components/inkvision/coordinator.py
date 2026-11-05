"""Data update coordinator for inkVision devices."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import logging

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosedError, WebSocketException

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CUSTOM_SCREEN_SLOTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

type InkVisionConfigEntry = ConfigEntry[InkVisionDataUpdateCoordinator]

UPDATE_INTERVAL = timedelta(seconds=300)  # 5 minute fallback polling (WebSocket is primary)
WEBSOCKET_RETRY_DELAY = 10  # seconds
WEBSOCKET_MAX_RETRY_DELAY = 30  # seconds


class InkVisionDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the inkVision device via WebSocket."""

    config_entry: InkVisionConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: InkVisionConfigEntry) -> None:
        """Initialize."""
        host = config_entry.data["host"]
        self.host = host
        self.session = async_get_clientsession(hass)
        self._websocket = None
        self._websocket_url = f"ws://{host}/ws"
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
                _LOGGER.debug("Attempting to connect to WebSocket at %s", self._websocket_url)
                
                async with websockets.connect(
                    self._websocket_url,
                    ping_interval=15,
                    ping_timeout=10,
                    close_timeout=5
                ) as websocket:
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
                    
                    # Listen for messages
                    async for message in websocket:
                        try:
                            data = json.loads(message)
                            await self._handle_websocket_message(data)
                        except json.JSONDecodeError:
                            _LOGGER.warning("Received invalid JSON from WebSocket: %s", message)
                        except Exception as err:
                            _LOGGER.error("Error processing WebSocket message: %s", err)
                            
            except (WebSocketException, ConnectionClosedError, OSError) as err:
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
        if not self._websocket:
            _LOGGER.debug("Skipping send; WebSocket not connected: %s", command)
            return

        websocket = self._websocket
        is_closed = getattr(websocket, "closed", False)
        # Some versions of the websockets library expose ``closed`` as an awaitable or callable
        if callable(is_closed):
            try:
                is_closed = is_closed()
            except Exception:  # pragma: no cover - defensive guard
                is_closed = False

        if is_closed:
            _LOGGER.debug("Skipping send; WebSocket already closed: %s", command)
            return

        try:
            await websocket.send(json.dumps(command))
            _LOGGER.debug("Sent WebSocket command: %s", command)
        except (WebSocketException, ConnectionClosedError, OSError) as err:
            _LOGGER.warning("WebSocket send failed (%s); marking connection closed", err)
            self._websocket_connected = False
            self._websocket = None
        except Exception as err:  # pragma: no cover - unexpected errors
            _LOGGER.error("Error sending WebSocket command: %s", err)

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
            async with asyncio.timeout(10):
                async with self.session.post(
                    f"http://{self.host}/buttons/actions",
                    json={button: {gesture: action}},
                ) as response:
                    if response.status != 200:
                        _LOGGER.error("Failed to set %s %s action: HTTP %s", button, gesture, response.status)
                        return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("HTTP error setting %s %s action: %s", button, gesture, err)
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
        endpoint_by_action = {
            "play": "/speaker/play",
            "pause": "/speaker/pause",
            "stop": "/speaker/stop",
            "set_volume": "/speaker/volume",
            "mute": "/speaker/mute",
            "play_media": "/speaker/play",
        }
        ws_command_by_action = {
            "play": "speaker_play",
            "pause": "speaker_pause",
            "stop": "speaker_stop",
            "set_volume": "speaker_set_volume",
            "mute": "speaker_mute",
            "play_media": "speaker_play_media",
        }

        endpoint = endpoint_by_action.get(action)
        if endpoint:
            try:
                async with asyncio.timeout(10):
                    async with self.session.post(
                        f"http://{self.host}{endpoint}",
                        json=payload,
                    ) as response:
                        if response.status == 200:
                            try:
                                data = await response.json()
                            except Exception:
                                return True

                            if isinstance(data, dict) and data.get("success") is False:
                                _LOGGER.error("Speaker command %s rejected: %s", action, data)
                                return False
                            return True

                        if response.status not in (404, 405):
                            _LOGGER.error(
                                "Speaker command %s failed via HTTP: status %s",
                                action,
                                response.status,
                            )
                            return False
            except (aiohttp.ClientError, asyncio.TimeoutError):
                _LOGGER.debug("Speaker HTTP command failed for action %s", action, exc_info=True)

        ws_command = ws_command_by_action.get(action)
        if ws_command is None:
            _LOGGER.error("Unknown speaker action requested: %s", action)
            return False

        return await self.send_device_command(ws_command, **payload)

    async def async_fetch_ota_status(self) -> dict:
        """Fetch OTA status via HTTP."""
        try:
            async with asyncio.timeout(10):
                async with self.session.get(f"http://{self.host}/api/ota/check") as response:
                    if response.status == 200:
                        ota_data = await response.json()
                        return ota_data if isinstance(ota_data, dict) else {}
                    _LOGGER.debug("OTA check failed with status %s", response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            _LOGGER.debug("OTA check HTTP error", exc_info=True)
        return {}

    async def async_fetch_config(self) -> dict:
        """Fetch device config via HTTP."""
        try:
            async with asyncio.timeout(10):
                async with self.session.get(f"http://{self.host}/config.json") as response:
                    if response.status == 200:
                        config_data = await response.json()
                        return config_data if isinstance(config_data, dict) else {}
                    _LOGGER.debug("Config fetch failed with status %s", response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            _LOGGER.debug("Config fetch HTTP error", exc_info=True)
        return {}

    async def async_set_timezone(self, timezone: str) -> bool:
        """Set timezone on the device via HTTP POST /config."""
        try:
            async with asyncio.timeout(10):
                async with self.session.post(
                    f"http://{self.host}/config",
                    data={"data": json.dumps({"timeZone": timezone})},
                ) as response:
                    if response.status == 200:
                        # Optimistically update coordinator data
                        existing = self.data or {}
                        new_data = deepcopy(existing)
                        config = new_data.setdefault("config", {})
                        config["timeZone"] = timezone
                        self.async_set_updated_data(new_data)
                        return True
                    _LOGGER.error("Failed to set timezone: HTTP %s", response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("HTTP error setting timezone: %s", err)
        return False

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

    async def _async_update_data(self):
        """Update data via HTTP (fallback when WebSocket is not available)."""
        if self._websocket_connected:
            _LOGGER.debug("WebSocket active, augmenting data via HTTP polling")
        else:
            _LOGGER.debug("WebSocket not connected, using HTTP polling")
        try:
            async with asyncio.timeout(10):
                # Fetch sensor data from the device
                async with self.session.get(f"http://{self.host}/sensors") as response:
                    if response.status == 200:
                        sensor_data = await response.json()
                    else:
                        raise UpdateFailed(
                            translation_domain=DOMAIN,
                            translation_key="update_failed_status",
                            translation_placeholders={"status": str(response.status)},
                        )
                
                # Fetch status data
                async with self.session.get(f"http://{self.host}/status") as response:
                    if response.status == 200:
                        status_data = await response.json()
                    else:
                        raise UpdateFailed(
                            translation_domain=DOMAIN,
                            translation_key="update_failed_status",
                            translation_placeholders={"status": str(response.status)},
                        )

                screens_data = None
                try:
                    async with self.session.get(f"http://{self.host}/display/screens") as response:
                        if response.status == 200:
                            screens_data = await response.json()
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    _LOGGER.debug("Unable to fetch screen rotation; keeping cached values")

                try:
                    async with self.session.get(f"http://{self.host}/buttons/actions") as response:
                        if response.status == 200:
                            button_actions = await response.json()
                            if isinstance(button_actions, dict):
                                self.button_actions = button_actions
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    _LOGGER.debug("Unable to fetch button actions; keeping cached values")

                existing = self.data or {"screens": [], "ota": {}}
                screens = existing.get("screens", [])
                if isinstance(screens_data, dict):
                    screens_value = screens_data.get("screens")
                    if isinstance(screens_value, list):
                        screens = screens_value

                ota_data = await self.async_fetch_ota_status()
                if not ota_data:
                    ota_data = existing.get("ota", {}) if isinstance(existing, dict) else {}

                config_data = await self.async_fetch_config()
                if not config_data:
                    config_data = existing.get("config", {}) if isinstance(existing, dict) else {}

                self._last_poll = self.hass.loop.time()
                return {
                    "sensors": sensor_data if isinstance(sensor_data, dict) else {},
                    "status": status_data if isinstance(status_data, dict) else {},
                    "screens": screens,
                    "ota": ota_data if isinstance(ota_data, dict) else {},
                    "config": config_data if isinstance(config_data, dict) else {},
                }
                
        except aiohttp.ClientError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except asyncio.TimeoutError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": "timeout"},
            ) from err

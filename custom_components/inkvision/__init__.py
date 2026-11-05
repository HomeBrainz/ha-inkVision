"""The inkVision integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
import json
from copy import deepcopy
import voluptuous as vol

import aiohttp
import websockets
from websockets.exceptions import WebSocketException, ConnectionClosedError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.template import Template
from homeassistant.exceptions import HomeAssistantError, TemplateError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import legacy_migration
from .const import CONF_CUSTOM_SCREEN_PREFIX, CUSTOM_SCREEN_SLOTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.MEDIA_PLAYER,
    Platform.TEXT,
]

UPDATE_INTERVAL = timedelta(seconds=300)  # 5 minute fallback polling (WebSocket is primary)
WEBSOCKET_RETRY_DELAY = 10  # seconds
WEBSOCKET_MAX_RETRY_DELAY = 30  # seconds

# Service schemas
SERVICE_SET_BRIGHTNESS = "set_brightness"
SERVICE_DISPLAY_TEXT = "display_text"
SERVICE_RESTART_DEVICE = "restart_device"
SERVICE_SET_SCREEN_ROTATION = "set_screen_rotation"
SERVICE_SET_TIMEZONE = "set_timezone"
SERVICE_SET_CUSTOM_SCREEN = "set_custom_screen"

BRIGHTNESS_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("brightness"): vol.Coerce(int),
})

DISPLAY_TEXT_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("text"): str,
})

RESTART_DEVICE_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
})

SCREEN_ROTATION_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("screens"): vol.All(cv.ensure_list, [cv.string]),
})

TIMEZONE_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("timezone"): str,
})

CUSTOM_SCREEN_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=CUSTOM_SCREEN_SLOTS)),
    vol.Optional("text", default=""): cv.string,
})


class InkVisionDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the inkVision device via WebSocket."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        """Initialize."""
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
                        raise UpdateFailed(f"Error communicating with device: {response.status}")
                
                # Fetch status data
                async with self.session.get(f"http://{self.host}/status") as response:
                    if response.status == 200:
                        status_data = await response.json()
                    else:
                        raise UpdateFailed(f"Error communicating with device: {response.status}")

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
            raise UpdateFailed(f"Error communicating with API: {err}")
        except asyncio.TimeoutError:
            raise UpdateFailed("Timeout communicating with API")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration."""
    await legacy_migration.async_setup_legacy(hass)  # legacy: HACS-only
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up inkVision from a config entry."""
    if legacy_migration.CONF_MIGRATE_FROM in entry.data:  # legacy: HACS-only
        await legacy_migration.async_take_over_legacy_entry(hass, entry)

    coordinator = InkVisionDataUpdateCoordinator(
        hass, entry.data["host"]
    )

    await coordinator.async_config_entry_first_refresh()

    # Start WebSocket connection
    await coordinator.async_start_websocket()

    # Independent of the coordinator's own timer, which WebSocket pushes keep
    # resetting; check once a minute and poll when the last poll is stale.
    entry.async_on_unload(
        async_track_time_interval(hass, coordinator.async_poll_if_stale, timedelta(minutes=1))
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_track_custom_screen_templates(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    
    # Register services if this is the first entry
    if len(hass.data[DOMAIN]) == 1:
        await async_setup_services(hass)

    return True


@callback
def _async_track_custom_screen_templates(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: InkVisionDataUpdateCoordinator
) -> None:
    """Keep each templated custom screen slot in sync with its template's result."""
    for slot in range(1, CUSTOM_SCREEN_SLOTS + 1):
        template_str = (entry.options.get(f"{CONF_CUSTOM_SCREEN_PREFIX}{slot}") or "").strip()
        if not template_str:
            continue

        template = Template(template_str, hass)

        @callback
        def _on_result(event, updates: list[TrackTemplateResult], slot=slot) -> None:
            result = updates.pop().result
            if isinstance(result, TemplateError):
                _LOGGER.warning("Custom screen %d template error: %s", slot, result)
                text = ""
            else:
                text = "" if result is None else str(result)
            if coordinator.custom_screens.get(slot) != text:
                hass.async_create_task(coordinator.async_set_custom_screen(slot, text))

        info = async_track_template_result(hass, [TrackTemplate(template, None)], _on_result)
        entry.async_on_unload(info.async_remove)
        info.async_refresh()


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so changed custom screen templates take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version < 2:
        entity_registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        removed = 0

        for entity in entries:
            should_remove = entity.domain == "weather"
            if not should_remove:
                haystack = " ".join(
                    part for part in (entity.unique_id, entity.original_name, entity.entity_id) if part
                ).lower()
                should_remove = "weather" in haystack or "forecast" in haystack

            if should_remove:
                _LOGGER.info("Removing deprecated weather entity: %s", entity.entity_id)
                entity_registry.async_remove(entity.entity_id)
                removed += 1

        if removed:
            _LOGGER.info("Removed %d deprecated weather entities", removed)

        hass.config_entries.async_update_entry(entry, version=2)

    if entry.version < 3:
        entity_registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        removed = 0

        deprecated_tokens = [
            "tvoc",
            "co2",
            "aqi",
            "air quality",
            "air_quality",
            "humidity offset",
            "humidity_offset",
            "temperature offset",
            "temperature_offset",
            "display mode",
            "display_mode",
            "free heap",
            "free_heap",
            "sensors available",
            "sensors_available",
            "websocket",
            "websocket client",
            "websocket_clients",
            "wifi connected",
            "wifi_connected",
        ]

        for entity in entries:
            haystack = " ".join(
                part for part in (entity.unique_id, entity.original_name, entity.entity_id) if part
            ).lower()
            if any(token in haystack for token in deprecated_tokens):
                _LOGGER.info("Removing deprecated entity: %s", entity.entity_id)
                entity_registry.async_remove(entity.entity_id)
                removed += 1

        if removed:
            _LOGGER.info("Removed %d deprecated entities", removed)

        hass.config_entries.async_update_entry(entry, version=3)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await coordinator.async_clear_custom_screens()

    # Stop WebSocket connection
    await coordinator.async_stop_websocket()
    
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    # Unregister services if this was the last entry
    if not hass.data[DOMAIN]:
        await async_unload_services(hass)

    return unload_ok


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for inkVision."""
    
    async def set_brightness_service(call: ServiceCall) -> None:
        """Handle set brightness service call."""
        device_id = call.data["device_id"]
        brightness = call.data["brightness"]
        
        coordinator = await _get_coordinator_for_device(hass, device_id)
        if coordinator:
            success = await coordinator.send_device_command("set_brightness", value=brightness)
            if success:
                _LOGGER.info("Set brightness to %d for device %s", brightness, device_id)
            else:
                _LOGGER.error("Failed to set brightness for device %s", device_id)
    
    async def display_text_service(call: ServiceCall) -> None:
        """Handle display text service call."""
        device_id = call.data["device_id"]
        text = call.data["text"]
        
        coordinator = await _get_coordinator_for_device(hass, device_id)
        if coordinator:
            success = await coordinator.send_device_command("display_text", text=text)
            if success:
                _LOGGER.debug("Displayed text '%s' on device %s", text, device_id)
            else:
                _LOGGER.error("Failed to display text on device %s", device_id)
    
    async def restart_device_service(call: ServiceCall) -> None:
        """Handle restart device service call."""
        device_id = call.data["device_id"]
        
        coordinator = await _get_coordinator_for_device(hass, device_id)
        if coordinator:
            success = await coordinator.send_device_command("restart")
            if success:
                _LOGGER.info("Restart command sent to device %s", device_id)
            else:
                _LOGGER.error("Failed to restart device %s", device_id)

    async def set_screen_rotation_service(call: ServiceCall) -> None:
        """Handle screen rotation service call."""
        device_id = call.data["device_id"]
        screens = call.data["screens"]

        coordinator = await _get_coordinator_for_device(hass, device_id)
        if not coordinator:
            return

        if isinstance(screens, str):
            screens = [screen.strip() for screen in screens.split(",") if screen.strip()]

        if not screens:
            _LOGGER.error("No screens provided for device %s", device_id)
            return

        try:
            async with asyncio.timeout(10):
                async with coordinator.session.post(
                    f"http://{coordinator.host}/display/screens",
                    json={"screens": screens},
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Updated screen rotation for device %s", device_id)
                    else:
                        _LOGGER.error(
                            "Failed to update screen rotation for device %s: %s",
                            device_id,
                            response.status,
                        )
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP error updating screen rotation for device %s: %s", device_id, err)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout updating screen rotation for device %s", device_id)

    async def set_timezone_service(call: ServiceCall) -> None:
        """Handle set timezone service call."""
        device_id = call.data["device_id"]
        timezone = call.data["timezone"]

        coordinator = await _get_coordinator_for_device(hass, device_id)
        if not coordinator:
            return

        success = await coordinator.async_set_timezone(timezone)
        if success:
            _LOGGER.info("Set timezone to %s for device %s", timezone, device_id)
        else:
            _LOGGER.error("Failed to set timezone for device %s", device_id)
    
    async def set_custom_screen_service(call: ServiceCall) -> None:
        """Handle set custom screen service call."""
        device_id = call.data["device_id"]
        slot = call.data["slot"]

        coordinator = await _get_coordinator_for_device(hass, device_id)
        if not coordinator:
            return

        if not coordinator.custom_screens_supported:
            raise HomeAssistantError("This device's firmware does not support custom screens.")

        await coordinator.async_set_custom_screen(slot, call.data["text"])

    services = {
        SERVICE_SET_BRIGHTNESS: (set_brightness_service, BRIGHTNESS_SCHEMA),
        SERVICE_DISPLAY_TEXT: (display_text_service, DISPLAY_TEXT_SCHEMA),
        SERVICE_RESTART_DEVICE: (restart_device_service, RESTART_DEVICE_SCHEMA),
        SERVICE_SET_SCREEN_ROTATION: (set_screen_rotation_service, SCREEN_ROTATION_SCHEMA),
        SERVICE_SET_TIMEZONE: (set_timezone_service, TIMEZONE_SCHEMA),
        SERVICE_SET_CUSTOM_SCREEN: (set_custom_screen_service, CUSTOM_SCREEN_SCHEMA),
    }
    for name, (handler, schema) in services.items():
        hass.services.async_register(DOMAIN, name, handler, schema=schema)
    legacy_migration.async_register_service_aliases(hass, services)  # legacy: HACS-only


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload services for inkVision."""
    hass.services.async_remove(DOMAIN, SERVICE_SET_BRIGHTNESS)
    hass.services.async_remove(DOMAIN, SERVICE_DISPLAY_TEXT)
    hass.services.async_remove(DOMAIN, SERVICE_RESTART_DEVICE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SCREEN_ROTATION)
    hass.services.async_remove(DOMAIN, SERVICE_SET_TIMEZONE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_CUSTOM_SCREEN)
    legacy_migration.async_remove_service_aliases(hass)  # legacy: HACS-only


async def _get_coordinator_for_device(hass: HomeAssistant, device_id: str) -> InkVisionDataUpdateCoordinator:
    """Get coordinator for a specific device."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if not device:
        _LOGGER.error("Device %s not found", device_id)
        return None

    for entry_id in device.config_entries:
        coordinator = hass.data[DOMAIN].get(entry_id)
        if coordinator:
            return coordinator

    _LOGGER.error("No coordinator found for device %s", device_id)
    return None
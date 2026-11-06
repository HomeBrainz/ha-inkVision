"""Fixtures for inkVision integration tests."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.inkvision.const import DOMAIN

HOST = "192.168.1.50"
BASE_URL = f"http://{HOST}"
MAC = "AA:BB:CC:DD:EE:FF"
# The config entry unique ID and device identifier: the MAC as format_mac() gives it.
UNIQUE_ID = "aa:bb:cc:dd:ee:ff"

INFO_PAYLOAD: dict[str, Any] = {
    "name": "Atmos Smart Clock",
    "device": "Atmos Smart Clock",
    "mac_address": MAC,
    "type": "HOMEBRAINZ_DEVICE",
}

STATUS_PAYLOAD: dict[str, Any] = {
    "device": "Atmos Smart Clock",
    "model": "ATMOS-SMART-CLOCK",
    "mac_address": MAC,
    "ip_address": HOST,
    "version": "1.2.3",
    "firmware_id": 5,
    "rssi": -55,
    "uptime": 1234,
    "brightness": 8,
    "button_events": True,
    "custom_screens": 4,
}

SENSORS_PAYLOAD: dict[str, Any] = {
    "bme680": {
        "temperature": 21.5,
        "humidity": 45.2,
        "pressure": 1013.2,
        "gas_resistance_kohm": 120.5,
        "iaq": 50,
        "iaq_accuracy": 3,
        "static_iaq": 48,
        "co2_equivalent": 600,
        "breath_voc": 0.5,
        "iaq_rating": "good",
    },
}

SCREENS_PAYLOAD: dict[str, Any] = {"screens": ["clock", "temp"]}

BUTTON_ACTIONS_PAYLOAD: dict[str, Any] = {
    "button1": {"singleTap": "nextScreen", "longPress": "none"},
    "button2": {"singleTap": "brightnessUp", "longPress": "brightnessDown"},
}

OTA_PAYLOAD: dict[str, Any] = {
    "updateAvailable": False,
    "currentVersion": "1.2.3",
    "latestVersion": "1.2.3",
    "currentFirmwareId": 5,
    "latestFirmwareId": 5,
}

CONFIG_PAYLOAD: dict[str, Any] = {"timeZone": "Europe/Berlin"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip the real entry setup when only the config flow is under test."""
    with patch(
        "custom_components.inkvision.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Create entities that are disabled by default as enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return an inkVision config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Atmos Smart Clock",
        data={CONF_HOST: HOST},
        unique_id=UNIQUE_ID,
        version=3,
        minor_version=2,
    )


def mock_device_http(
    aioclient_mock: AiohttpClientMocker,
    *,
    status: dict[str, Any] | None = None,
    sensors: dict[str, Any] | None = None,
) -> None:
    """Register the device's HTTP API on the aiohttp mocker."""
    aioclient_mock.get(f"{BASE_URL}/sensors", json=deepcopy(sensors or SENSORS_PAYLOAD))
    aioclient_mock.get(f"{BASE_URL}/status", json=deepcopy(status or STATUS_PAYLOAD))
    aioclient_mock.get(f"{BASE_URL}/display/screens", json=deepcopy(SCREENS_PAYLOAD))
    aioclient_mock.get(f"{BASE_URL}/buttons/actions", json=deepcopy(BUTTON_ACTIONS_PAYLOAD))
    aioclient_mock.get(f"{BASE_URL}/api/ota/check", json=deepcopy(OTA_PAYLOAD))
    aioclient_mock.get(f"{BASE_URL}/config.json", json=deepcopy(CONFIG_PAYLOAD))


class FakeWebSocket:
    """Stand-in for a pyinkvision InkVisionWebSocket.

    Messages pushed with ``push`` are handed to the coordinator's ``async for``
    loop; ``push`` returns once the coordinator has processed the message.
    An exception pushed instead of a message is raised from the iterator.
    """

    def __init__(self) -> None:
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        self.listening = asyncio.Event()
        self.send = AsyncMock()
        self.close = AsyncMock()
        self.closed = False
        self._pending = False

    def __aiter__(self) -> FakeWebSocket:
        self.listening.set()
        return self

    async def __anext__(self) -> dict[str, Any]:
        while True:
            if self._pending:
                # The coordinator is back for the next message, so the last one is handled.
                self._pending = False
                self.messages.task_done()
            item = await self.messages.get()
            self._pending = True
            if isinstance(item, BaseException):
                self._pending = False
                self.messages.task_done()
                raise item
            if isinstance(item, str):
                # Like the library: decode text frames and skip invalid JSON.
                try:
                    item = json.loads(item)
                except ValueError:
                    continue
            if not isinstance(item, dict):
                # The library only yields JSON objects.
                continue
            return item

    async def push(self, message: Any) -> None:
        """Deliver a message (dict, raw string or exception) and wait until handled."""
        await self.messages.put(message)
        await self.messages.join()

    @property
    def sent(self) -> list[dict[str, Any]]:
        """Return the decoded commands the coordinator sent."""
        return [call.args[0] for call in self.send.await_args_list]


class FakeWebSocketFactory:
    """Replacement for ``InkVisionClient.websocket`` that hands out FakeWebSockets."""

    def __init__(self) -> None:
        self.sockets: list[FakeWebSocket] = []
        self.side_effects: list[BaseException] = []
        self.urls: list[str] = []
        self.connected = asyncio.Event()

    @property
    def current(self) -> FakeWebSocket:
        """Return the most recent connection."""
        return self.sockets[-1]

    def __call__(self, client: Any):
        self.urls.append(f"ws://{client.host}/ws")
        return self._connect()

    @asynccontextmanager
    async def _connect(self) -> AsyncGenerator[FakeWebSocket]:
        if self.side_effects:
            raise self.side_effects.pop(0)
        websocket = FakeWebSocket()
        self.sockets.append(websocket)
        self.connected.set()
        yield websocket

    async def wait_listening(self) -> FakeWebSocket:
        """Wait until the coordinator is reading from the current connection."""
        async with asyncio.timeout(5):
            await self.connected.wait()
            await self.current.listening.wait()
        return self.current


@pytest.fixture
def mock_websocket() -> Generator[FakeWebSocketFactory]:
    """Patch the integration's WebSocket client."""
    factory = FakeWebSocketFactory()
    # A function, so the patched method gets the client like the real one.
    with patch("pyinkvision.InkVisionClient.websocket", lambda client: factory(client)):
        yield factory


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration with a mocked device and unload it afterwards."""
    mock_device_http(aioclient_mock)
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await mock_websocket.wait_listening()

    yield mock_config_entry

    if mock_config_entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

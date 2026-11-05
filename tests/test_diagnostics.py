"""Tests for inkVision diagnostics."""
from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import HOST, MAC


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Test diagnostics include the device data with addresses redacted."""
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    assert result["entry"]["data"] == {"host": "**REDACTED**"}
    assert result["entry"]["unique_id"] == "**REDACTED**"
    assert result["websocket_connected"] is True
    assert result["last_update_success"] is True
    assert result["data"]["status"]["mac_address"] == "**REDACTED**"
    assert result["data"]["status"]["version"] == "1.2.3"
    assert result["custom_screens"] == {}

    dumped = json.dumps(result)
    assert HOST not in dumped
    assert MAC not in dumped

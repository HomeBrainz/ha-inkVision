"""Diagnostics support for inkVision."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import InkVisionConfigEntry

TO_REDACT = {
    CONF_HOST,
    "mac_address",
    "ip_address",
    "ssid",
    # The OTA download link can carry a signed token.
    "downloadUrl",
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: InkVisionConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options), "unique_id": entry.unique_id},
            TO_REDACT,
        ),
        "websocket_connected": coordinator.websocket_connected,
        "last_update_success": coordinator.last_update_success,
        "data": async_redact_data(coordinator.data, TO_REDACT),
        "button_actions": coordinator.button_actions,
        "custom_screens": coordinator.custom_screens,
    }

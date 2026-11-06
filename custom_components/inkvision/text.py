"""Text platform for inkVision integration."""
from __future__ import annotations

import logging
from typing import override

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Maximum custom text length the firmware accepts. The device truncates to
# this length anyway, but capping in the UI avoids surprises.
CUSTOM_TEXT_MAX_LEN = 96

# First firmware_id that understands the "display_text" WebSocket command without silently ignoring it.
MIN_FIRMWARE_ID_CUSTOM_TEXT = 2


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the text platform."""
    coordinator = config_entry.runtime_data
    async_add_entities([InkVisionCustomTextEntity(coordinator, config_entry)])


class InkVisionCustomTextEntity(InkVisionEntity, TextEntity):
    """Text entity that shows a custom message on the device's display."""

    _attr_translation_key = "custom_message"
    _attr_mode = TextMode.TEXT
    _attr_native_max = CUSTOM_TEXT_MAX_LEN

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._attr_unique_id = f"{coordinator.device_key}_custom_text"
        self._attr_native_value = ""

    @property
    @override
    def available(self) -> bool:
        """Unavailable on firmware that predates the display_text command.

        Older devices silently ignore the command (unknown WS commands are a
        no-op), so without this the entity would look normal but do nothing.
        Devices that haven't reported a firmware_id at all are old builds
        from before this field existed - also gated off.
        """
        if not super().available:
            return False
        firmware_id = self.coordinator.data.status.get("firmware_id")
        return isinstance(firmware_id, int) and firmware_id >= MIN_FIRMWARE_ID_CUSTOM_TEXT

    @override
    async def async_set_value(self, value: str) -> None:
        """Show the message on the device's display (auto-reverts on its own after a timeout)."""
        success = await self.coordinator.send_device_command("display_text", text=value)
        if not success:
            raise self.coordinator.error("not_connected")

        self._attr_native_value = value[:CUSTOM_TEXT_MAX_LEN]
        self.async_write_ha_state()

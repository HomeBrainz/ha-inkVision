"""Text platform for HomeBrainz integration."""
from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HomeBrainzDataUpdateCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)

# Maximum custom text length the firmware accepts. The device truncates to
# this length anyway, but capping in the UI avoids surprises.
CUSTOM_TEXT_MAX_LEN = 96

# First firmware_id that understands the "display_text" WebSocket command without silently ignoring it.
MIN_FIRMWARE_ID_CUSTOM_TEXT = 2


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the text platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([HomeBrainzCustomTextEntity(coordinator, config_entry)])


class HomeBrainzCustomTextEntity(CoordinatorEntity, TextEntity):
    """Text entity that shows a custom message on the device's display."""

    _attr_name = "Custom Message"
    _attr_icon = "mdi:message-text-outline"
    _attr_mode = TextMode.TEXT
    _attr_native_max = CUSTOM_TEXT_MAX_LEN

    def __init__(
        self,
        coordinator: HomeBrainzDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._attr_unique_id = f"{config_entry.entry_id}_custom_text"
        self._attr_native_value = ""

    @property
    def available(self) -> bool:
        """Unavailable on firmware that predates the display_text command.

        Older devices silently ignore the command (unknown WS commands are a
        no-op), so without this the entity would look normal but do nothing.
        Devices that haven't reported a firmware_id at all are old builds
        from before this field existed - also gated off.
        """
        if not super().available or not self.coordinator.data:
            return False
        status = self.coordinator.data.get("status", {}) or {}
        firmware_id = status.get("firmware_id")
        return isinstance(firmware_id, int) and firmware_id >= MIN_FIRMWARE_ID_CUSTOM_TEXT

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

    async def async_set_value(self, value: str) -> None:
        """Show the message on the device's display (auto-reverts on its own after a timeout)."""
        success = await self.coordinator.send_device_command("display_text", text=value)
        if not success:
            raise HomeAssistantError("Unable to display text on HomeBrainz device.")

        self._attr_native_value = value[:CUSTOM_TEXT_MAX_LEN]
        self.async_write_ha_state()

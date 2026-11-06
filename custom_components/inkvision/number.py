"""Number platform for inkVision integration."""
from __future__ import annotations

import logging
from typing import override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator = config_entry.runtime_data
    async_add_entities([InkVisionBrightnessNumber(coordinator, config_entry)])


class InkVisionBrightnessNumber(InkVisionEntity, NumberEntity):
    """Number entity to control display brightness."""

    _attr_translation_key = "brightness"
    _attr_native_min_value = 0
    _attr_native_max_value = 15
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._attr_unique_id = f"{coordinator.device_key}_brightness_level"

    @property
    @override
    def native_value(self) -> int | None:
        """Return the current brightness level."""
        value = self.coordinator.data.status.get("brightness")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            _LOGGER.debug("Received non-integer brightness value: %s", value)
            return None

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set a new brightness level."""
        brightness = max(self._attr_native_min_value, min(self._attr_native_max_value, round(value)))

        if not await self.coordinator.send_device_command("set_brightness", value=brightness):
            raise self.coordinator.error("not_connected")

        self.coordinator.async_set_updated_fields(
            status={**self.coordinator.data.status, "brightness": brightness}
        )

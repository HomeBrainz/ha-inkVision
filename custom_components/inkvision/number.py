"""Number platform for inkVision integration."""
from __future__ import annotations

from copy import deepcopy
import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import InkVisionDataUpdateCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([InkVisionBrightnessNumber(coordinator, config_entry)])


class InkVisionBrightnessNumber(CoordinatorEntity, NumberEntity):
    """Number entity to control display brightness."""

    _attr_name = "Display Brightness"
    _attr_icon = "mdi:brightness-6"
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
        self._attr_unique_id = f"{config_entry.entry_id}_brightness_level"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_name = "inkVision"
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

    @property
    def native_value(self) -> int | None:
        """Return the current brightness level."""
        if not self.coordinator.data:
            return None
        status = self.coordinator.data.get("status")
        if not isinstance(status, dict):
            return None
        value = status.get("brightness")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            _LOGGER.debug("Received non-integer brightness value: %s", value)
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set a new brightness level."""
        brightness = max(self._attr_native_min_value, min(self._attr_native_max_value, int(round(value))))

        if not await self.coordinator.send_device_command("set_brightness", value=brightness):
            raise HomeAssistantError("Unable to send brightness command to inkVision device.")

        existing = self.coordinator.data or {"sensors": {}, "status": {}}
        new_data = deepcopy(existing)
        status = new_data.setdefault("status", {})
        status["brightness"] = brightness
        self.coordinator.async_set_updated_data(new_data)

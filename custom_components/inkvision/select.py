"""Select platform for inkVision integration."""
from __future__ import annotations

from copy import deepcopy
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import InkVisionDataUpdateCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)

# Supported IANA timezones (must match ESP tz_lookup.h)
SUPPORTED_TIMEZONES = [
    "Etc/UTC",
    "Europe/London",
    "Europe/Dublin",
    "Europe/Lisbon",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Rome",
    "Europe/Madrid",
    "Europe/Amsterdam",
    "Europe/Brussels",
    "Europe/Prague",
    "Europe/Vienna",
    "Europe/Warsaw",
    "Europe/Stockholm",
    "Europe/Oslo",
    "Europe/Copenhagen",
    "Europe/Helsinki",
    "Europe/Athens",
    "Europe/Istanbul",
    "Europe/Moscow",
    "Europe/Kiev",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Toronto",
    "America/Vancouver",
    "America/Sao_Paulo",
    "America/Argentina/Buenos_Aires",
    "America/Mexico_City",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Singapore",
    "Asia/Seoul",
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Bangkok",
    "Asia/Jakarta",
    "Asia/Manila",
    "Asia/Taipei",
    "Australia/Sydney",
    "Australia/Melbourne",
    "Australia/Perth",
    "Australia/Brisbane",
    "Pacific/Auckland",
    "Pacific/Honolulu",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Africa/Lagos",
    "Africa/Nairobi",
    "America/Santiago",
    "America/Lima",
    "America/Bogota",
    "America/Caracas",
    "America/La_Paz",
    "America/Montevideo",
    "Atlantic/Reykjavik",
    "Atlantic/Azores",
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[SelectEntity] = [InkVisionTimezoneSelect(coordinator, config_entry)]
    if coordinator.button_actions:
        entities.extend(
            InkVisionButtonActionSelect(coordinator, config_entry, button, gesture)
            for button in ("button1", "button2")
            for gesture in ("singleTap", "longPress")
        )
    async_add_entities(entities)


class InkVisionTimezoneSelect(CoordinatorEntity, SelectEntity):
    """Select entity for device timezone."""

    _attr_name = "Timezone"
    _attr_icon = "mdi:map-clock-outline"
    _attr_options = SUPPORTED_TIMEZONES

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._attr_unique_id = f"{config_entry.entry_id}_timezone"

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
    def current_option(self) -> str | None:
        """Return the current timezone."""
        if not self.coordinator.data:
            return None
        config = self.coordinator.data.get("config", {})
        if not isinstance(config, dict):
            return None
        tz = config.get("timeZone")
        if tz and tz in SUPPORTED_TIMEZONES:
            return tz
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the timezone on the device."""
        if option not in SUPPORTED_TIMEZONES:
            raise HomeAssistantError(f"Unsupported timezone: {option}")

        success = await self.coordinator.async_set_timezone(option)
        if not success:
            raise HomeAssistantError("Unable to set timezone on inkVision device.")


# HA option -> firmware action name. Options are snake_case for translations.
BUTTON_ACTIONS = {
    "none": "none",
    "brightness_up": "brightnessUp",
    "brightness_down": "brightnessDown",
    "next_screen": "nextScreen",
    "prev_screen": "prevScreen",
    "toggle_screen": "toggleScreen",
}
GESTURE_NAMES = {"singleTap": "tap", "longPress": "long press"}


class InkVisionButtonActionSelect(CoordinatorEntity, SelectEntity):
    """What one button gesture does on the device itself.

    "none" leaves the gesture to Home Assistant automations (event entities).
    """

    _attr_icon = "mdi:gesture-tap-button"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(BUTTON_ACTIONS)
    _attr_translation_key = "button_action"

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
        button: str,
        gesture: str,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._button = button
        self._gesture = gesture
        self._attr_name = f"Button {button[-1]} {GESTURE_NAMES[gesture]} action"
        self._attr_unique_id = f"{config_entry.entry_id}_{button}_{gesture}_action"

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.button_actions)

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
    def current_option(self) -> str | None:
        """Return the gesture's current action."""
        action = (self.coordinator.button_actions.get(self._button) or {}).get(self._gesture)
        for option, firmware_name in BUTTON_ACTIONS.items():
            if firmware_name == action:
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the gesture's action on the device."""
        if option not in BUTTON_ACTIONS:
            raise HomeAssistantError(f"Unsupported button action: {option}")

        success = await self.coordinator.async_set_button_action(
            self._button, self._gesture, BUTTON_ACTIONS[option]
        )
        if not success:
            raise HomeAssistantError("Unable to set the button action on inkVision device.")

"""Select platform for inkVision integration."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

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
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the select platform."""
    coordinator = config_entry.runtime_data
    entities: list[SelectEntity] = [InkVisionTimezoneSelect(coordinator, config_entry)]
    if coordinator.button_actions:
        entities.extend(
            InkVisionButtonActionSelect(coordinator, config_entry, button, gesture)
            for button in ("button1", "button2")
            for gesture in ("singleTap", "longPress")
        )
    async_add_entities(entities)


class InkVisionTimezoneSelect(InkVisionEntity, SelectEntity):
    """Select entity for device timezone."""

    _attr_translation_key = "timezone"
    _attr_options = SUPPORTED_TIMEZONES

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._attr_unique_id = f"{coordinator.device_key}_timezone"

    @property
    def current_option(self) -> str | None:
        """Return the current timezone."""
        tz = self.coordinator.data.config.get("timeZone")
        if tz and tz in SUPPORTED_TIMEZONES:
            return tz
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the timezone on the device."""
        if option not in SUPPORTED_TIMEZONES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_timezone",
                translation_placeholders={"timezone": option},
            )

        success = await self.coordinator.async_set_timezone(option)
        if not success:
            raise self.coordinator.error("request_failed")


# HA option -> firmware action name. Options are snake_case for translations.
BUTTON_ACTIONS = {
    "none": "none",
    "brightness_up": "brightnessUp",
    "brightness_down": "brightnessDown",
    "next_screen": "nextScreen",
    "prev_screen": "prevScreen",
    "toggle_screen": "toggleScreen",
}
GESTURE_TRANSLATION_KEYS = {"singleTap": "button_tap_action", "longPress": "button_long_press_action"}


class InkVisionButtonActionSelect(InkVisionEntity, SelectEntity):
    """What one button gesture does on the device itself.

    "none" leaves the gesture to Home Assistant automations (event entities).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(BUTTON_ACTIONS)

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
        self._attr_translation_key = GESTURE_TRANSLATION_KEYS[gesture]
        self._attr_translation_placeholders = {"number": button[-1]}
        self._attr_unique_id = f"{coordinator.device_key}_{button}_{gesture}_action"

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.button_actions)

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
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_button_action",
                translation_placeholders={"action": option},
            )

        success = await self.coordinator.async_set_button_action(
            self._button, self._gesture, BUTTON_ACTIONS[option]
        )
        if not success:
            raise self.coordinator.error("request_failed")

"""Event platform for inkVision integration (physical button gestures)."""
from __future__ import annotations

import logging
from typing import override

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

BUTTON_COUNT = 2
EVENT_TYPES = ["single_tap", "long_press"]

# Models with touch buttons. Their entities exist even on firmware that
# predates button events, so they appear without a reload after an OTA update.
BUTTON_MODEL_PREFIXES = ("ATMOS-SMART-CLOCK",)


def _has_buttons(status: dict) -> bool:
    if status.get("button_events"):
        return True
    model = status.get("model")
    return isinstance(model, str) and model.upper().startswith(BUTTON_MODEL_PREFIXES)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the event platform."""
    coordinator = config_entry.runtime_data
    if not _has_buttons(coordinator.data.status):
        return

    async_add_entities(
        InkVisionButtonEvent(coordinator, config_entry, button)
        for button in range(1, BUTTON_COUNT + 1)
    )


class InkVisionButtonEvent(InkVisionEntity, EventEntity):
    """Fires on a single tap or long press of one of the device's buttons.

    Gestures fire whatever the button's on-device action is; set the action to
    "none" in the app to use a button for Home Assistant automations only.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "button"

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
        button: int,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]
        self._button = button
        self._attr_translation_placeholders = {"number": str(button)}
        self._attr_unique_id = f"{coordinator.device_key}_button_{button}_event"

    @property
    @override
    def available(self) -> bool:
        """Unavailable on firmware that doesn't push button events."""
        return super().available and bool(self.coordinator.data.status.get("button_events"))

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to button pushes from the coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_button_listener(self._handle_button_event))

    @callback
    def _handle_button_event(self, data: dict) -> None:
        if data.get("button") != self._button:
            return
        gesture = data.get("gesture")
        if gesture not in EVENT_TYPES:
            _LOGGER.debug("Ignoring unknown gesture %s on button %s", gesture, self._button)
            return
        # "action" is what the device itself did for this gesture (e.g. brightnessUp, none).
        self._trigger_event(gesture, {"action": data.get("action")})
        self.async_write_ha_state()

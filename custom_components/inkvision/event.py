"""Event platform for inkVision integration (physical button gestures)."""
from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import InkVisionDataUpdateCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)

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
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the event platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    status = (coordinator.data or {}).get("status", {}) or {}
    if not _has_buttons(status):
        return

    async_add_entities(
        InkVisionButtonEvent(coordinator, config_entry, button)
        for button in range(1, BUTTON_COUNT + 1)
    )


class InkVisionButtonEvent(CoordinatorEntity, EventEntity):
    """Fires on a single tap or long press of one of the device's buttons.

    Gestures fire whatever the button's on-device action is; set the action to
    "none" in the app to use a button for Home Assistant automations only.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES
    _attr_icon = "mdi:gesture-tap-button"

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
        self._attr_name = f"Button {button}"
        self._attr_unique_id = f"{config_entry.entry_id}_button_{button}_event"

    @property
    def available(self) -> bool:
        """Unavailable on firmware that doesn't push button events."""
        if not super().available or not self.coordinator.data:
            return False
        status = self.coordinator.data.get("status", {}) or {}
        return bool(status.get("button_events"))

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

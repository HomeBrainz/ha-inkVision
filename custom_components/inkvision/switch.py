"""Switch platform for inkVision integration."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging

import aiohttp

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

DEFAULT_SCREEN_ORDER = [
    "clock",
    "temp",
    "humidity",
    "pressure",
    "gas",
    "iaq",
]

SCREEN_LABELS = {
    "clock": "Clock",
    "temp": "Temperature",
    "humidity": "Humidity",
    "pressure": "Pressure",
    "gas": "Gas",
    "iaq": "IAQ",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    coordinator = config_entry.runtime_data

    entities = [
        InkVisionScreenSwitch(
            coordinator,
            config_entry,
            screen_id=screen_id,
            name=SCREEN_LABELS[screen_id],
        )
        for screen_id in DEFAULT_SCREEN_ORDER
    ]

    async_add_entities(entities)


class InkVisionSwitchEntity(InkVisionEntity, SwitchEntity):
    """Base class for inkVision switch entities."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]


class InkVisionScreenSwitch(InkVisionSwitchEntity):
    """Switch to enable/disable a screen in the rotation list."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
        *,
        screen_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._screen_id = screen_id
        self._attr_unique_id = f"{config_entry.entry_id}_screen_{screen_id}"
        self._attr_name = f"Screen {name}"

    @property
    def is_on(self) -> bool:
        """Return whether the screen is enabled in rotation."""
        return self._screen_id in self._get_enabled_screens()

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the screen."""
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the screen."""
        await self._set_enabled(False)

    def _get_enabled_screens(self) -> list[str]:
        if not self.coordinator.data:
            return []
        screens = self.coordinator.data.get("screens", [])
        if not isinstance(screens, list):
            return []
        return [screen for screen in screens if isinstance(screen, str)]

    async def _set_enabled(self, enabled: bool) -> None:
        current = set(self._get_enabled_screens())
        if enabled:
            current.add(self._screen_id)
        else:
            current.discard(self._screen_id)

        new_screens = [screen for screen in DEFAULT_SCREEN_ORDER if screen in current]

        if not new_screens:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="last_screen")

        if new_screens == self._get_enabled_screens():
            return

        try:
            async with asyncio.timeout(10):
                async with self.coordinator.session.post(
                    f"http://{self.coordinator.host}/display/screens",
                    json={"screens": new_screens},
                ) as response:
                    if response.status != 200:
                        raise self.coordinator.error("device_rejected", status=str(response.status))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise self.coordinator.error("communication_error", error=str(err) or "timeout") from err

        existing = self.coordinator.data or {"sensors": {}, "status": {}, "screens": []}
        new_data = deepcopy(existing)
        new_data["screens"] = new_screens
        self.coordinator.async_set_updated_data(new_data)

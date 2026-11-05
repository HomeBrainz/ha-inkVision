"""Button platform for inkVision integration."""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up the button platform."""
    coordinator = config_entry.runtime_data

    entities = [
        InkVisionFirmwareCheckButton(coordinator, config_entry),
        InkVisionFirmwareUpdateButton(coordinator, config_entry),
    ]

    async_add_entities(entities)


class InkVisionButtonEntity(InkVisionEntity, ButtonEntity):
    """Base class for inkVision button entities."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]


class InkVisionFirmwareCheckButton(InkVisionButtonEntity):
    """Button to trigger firmware update check."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_firmware_check"
        self._attr_name = "Check Firmware Update"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_update_ota_status()


class InkVisionFirmwareUpdateButton(InkVisionButtonEntity):
    """Button to start firmware update."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_firmware_update"
        self._attr_name = "Install Firmware Update"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Re-check for an update, then install it."""
        # The cached OTA data can be hours old; always act on a fresh check.
        await self.coordinator.async_update_ota_status()
        ota = (self.coordinator.data or {}).get("ota", {})
        if not isinstance(ota, dict) or not ota:
            raise self.coordinator.error("update_check_failed")
        if ota.get("error"):
            raise self.coordinator.error("update_check_error", error=str(ota["error"]))
        if not ota.get("updateAvailable"):
            raise self.coordinator.error(
                "firmware_up_to_date", version=str(ota.get("currentVersion", "unknown"))
            )

        download_url = ota.get("downloadUrl")
        if not download_url:
            raise self.coordinator.error("no_download_url")

        try:
            async with asyncio.timeout(15):
                async with self.coordinator.session.post(
                    f"http://{self._host}/api/ota/update",
                    json={"url": download_url, "confirm": True},
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        raise self.coordinator.error(
                            "firmware_update_rejected", status=str(response.status), details=body[:200]
                        )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise self.coordinator.error("communication_error", error=str(err) or "timeout") from err

        _LOGGER.info(
            "Firmware update to %s started on %s; the device reboots when done",
            ota.get("latestVersion"),
            self._host,
        )

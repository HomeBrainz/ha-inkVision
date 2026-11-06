"""Binary sensor platform for inkVision integration."""
from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity, async_keep_deprecated_entity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = config_entry.runtime_data

    # Replaced by the firmware update entity; kept for now where it already exists.
    if async_keep_deprecated_entity(
        hass,
        Platform.BINARY_SENSOR,
        f"{coordinator.device_key}_firmware_update_available",
        "update.install",
    ):
        async_add_entities([InkVisionFirmwareUpdateAvailableSensor(coordinator, config_entry)])


class InkVisionBinarySensor(InkVisionEntity, BinarySensorEntity):
    """Base class for inkVision binary sensors."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]


class InkVisionFirmwareUpdateAvailableSensor(InkVisionBinarySensor):
    """Binary sensor for firmware update availability. Deprecated: use the update entity."""

    _attr_translation_key = "firmware_update_available"

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.device_key}_firmware_update_available"
        self._attr_device_class = BinarySensorDeviceClass.UPDATE
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True if an update is available."""
        value = self.coordinator.data.ota.get("updateAvailable")
        if value is None:
            return None
        return bool(value)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return OTA metadata attributes."""
        ota = self.coordinator.data.ota
        return {
            "current_firmware_id": ota.get("currentFirmwareId"),
            "latest_firmware_id": ota.get("latestFirmwareId"),
            "current_version": ota.get("currentVersion"),
            "latest_version": ota.get("latestVersion"),
            "download_url": ota.get("downloadUrl"),
            "release_notes": ota.get("releaseNotes"),
        }

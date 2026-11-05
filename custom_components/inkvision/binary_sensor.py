"""Binary sensor platform for inkVision integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
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
    """Set up the binary sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = [
        InkVisionFirmwareUpdateAvailableSensor(coordinator, config_entry),
    ]

    async_add_entities(entities)


class InkVisionBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base class for inkVision binary sensors."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]

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


class InkVisionFirmwareUpdateAvailableSensor(InkVisionBinarySensor):
    """Binary sensor for firmware update availability."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_firmware_update_available"
        self._attr_name = "Firmware Update Available"
        self._attr_device_class = BinarySensorDeviceClass.UPDATE
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return True if an update is available."""
        ota = (self.coordinator.data or {}).get("ota", {})
        if not isinstance(ota, dict):
            return None
        value = ota.get("updateAvailable")
        if value is None:
            return None
        return bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return OTA metadata attributes."""
        ota = (self.coordinator.data or {}).get("ota", {})
        if not isinstance(ota, dict):
            return None
        return {
            "current_firmware_id": ota.get("currentFirmwareId"),
            "latest_firmware_id": ota.get("latestFirmwareId"),
            "current_version": ota.get("currentVersion"),
            "latest_version": ota.get("latestVersion"),
            "download_url": ota.get("downloadUrl"),
            "release_notes": ota.get("releaseNotes"),
        }

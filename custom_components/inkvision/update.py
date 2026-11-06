"""Update platform for inkVision integration (device firmware)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the update platform."""
    async_add_entities([InkVisionFirmwareUpdate(config_entry.runtime_data)])


class InkVisionFirmwareUpdate(InkVisionEntity, UpdateEntity):
    """Firmware of the device, updated over the air from the inkVision servers."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    _attr_title = "inkVision firmware"

    def __init__(self, coordinator: InkVisionDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_key}_firmware"

    def _ota(self) -> dict[str, Any]:
        ota = (self.coordinator.data or {}).get("ota", {})
        return ota if isinstance(ota, dict) else {}

    @property
    def installed_version(self) -> str | None:
        """Return the running firmware version."""
        if version := self._ota().get("currentVersion"):
            return str(version)
        status = (self.coordinator.data or {}).get("status", {}) or {}
        version = status.get("version") if isinstance(status, dict) else None
        return str(version) if version else None

    @property
    def latest_version(self) -> str | None:
        """Return the newest firmware version, or the installed one when it is current."""
        ota = self._ota()
        if not ota.get("updateAvailable") or not ota.get("latestVersion"):
            return self.installed_version
        latest = str(ota["latestVersion"])
        if latest == self.installed_version and ota.get("latestFirmwareId") is not None:
            # A newer build can carry the same version string; the build ID tells them apart.
            latest = f"{latest} (build {ota['latestFirmwareId']})"
        return latest

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Trust the device, which compares build IDs rather than version strings."""
        return bool(self._ota().get("updateAvailable"))

    async def async_release_notes(self) -> str | None:
        """Return the release notes of the available update."""
        notes = self._ota().get("releaseNotes")
        return str(notes) if notes else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Install the latest firmware; the device reboots when done."""
        await self.coordinator.async_install_firmware()

    async def async_update(self) -> None:
        """Check for a new firmware (homeassistant.update_entity)."""
        await self.coordinator.async_update_ota_status()

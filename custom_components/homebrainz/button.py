"""Button platform for HomeBrainz integration."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HomeBrainzDataUpdateCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = [
        HomeBrainzFirmwareCheckButton(coordinator, config_entry),
        HomeBrainzFirmwareUpdateButton(coordinator, config_entry),
    ]

    async_add_entities(entities)


class HomeBrainzButtonEntity(CoordinatorEntity, ButtonEntity):
    """Base class for HomeBrainz button entities."""

    def __init__(
        self,
        coordinator: HomeBrainzDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_name = "HomeBrainz Clock"
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


class HomeBrainzFirmwareCheckButton(HomeBrainzButtonEntity):
    """Button to trigger firmware update check."""

    def __init__(
        self,
        coordinator: HomeBrainzDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_firmware_check"
        self._attr_name = "Check Firmware Update"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_update_ota_status()


class HomeBrainzFirmwareUpdateButton(HomeBrainzButtonEntity):
    """Button to start firmware update."""

    def __init__(
        self,
        coordinator: HomeBrainzDataUpdateCoordinator,
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
            raise HomeAssistantError("Could not check for a firmware update; is the device online?")
        if ota.get("error"):
            raise HomeAssistantError(f"Firmware update check failed: {ota['error']}")
        if not ota.get("updateAvailable"):
            raise HomeAssistantError(
                f"Firmware is already up to date ({ota.get('currentVersion', 'unknown')})"
            )

        download_url = ota.get("downloadUrl")
        if not download_url:
            raise HomeAssistantError("The update server did not provide a download URL")

        try:
            async with asyncio.timeout(15):
                async with self.coordinator.session.post(
                    f"http://{self._host}/api/ota/update",
                    json={"url": download_url, "confirm": True},
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        raise HomeAssistantError(
                            f"Device rejected the firmware update ({response.status}): {body[:200]}"
                        )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the device to start the update: {err}") from err

        _LOGGER.info(
            "Firmware update to %s started on %s; the device reboots when done",
            ota.get("latestVersion"),
            self._host,
        )

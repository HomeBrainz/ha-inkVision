"""Button platform for inkVision integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import InkVisionConfigEntry, InkVisionDataUpdateCoordinator
from .entity import InkVisionEntity, async_keep_deprecated_entity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: InkVisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button platform."""
    coordinator = config_entry.runtime_data
    key = coordinator.device_key

    entities: list[ButtonEntity] = [InkVisionRestartButton(coordinator, config_entry)]
    # Replaced by the firmware update entity; kept for now where they already exist.
    if async_keep_deprecated_entity(hass, Platform.BUTTON, f"{key}_firmware_check", "update.install"):
        entities.append(InkVisionFirmwareCheckButton(coordinator, config_entry))
    if async_keep_deprecated_entity(hass, Platform.BUTTON, f"{key}_firmware_update", "update.install"):
        entities.append(InkVisionFirmwareUpdateButton(coordinator, config_entry))

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


class InkVisionRestartButton(InkVisionButtonEntity):
    """Button to restart the device."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.device_key}_restart"

    async def async_press(self) -> None:
        """Restart the device."""
        if not await self.coordinator.send_device_command("restart"):
            raise self.coordinator.error("not_connected")


class InkVisionFirmwareCheckButton(InkVisionButtonEntity):
    """Button to trigger firmware update check. Deprecated: use the update entity."""

    _attr_translation_key = "firmware_check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.device_key}_firmware_check"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.async_update_ota_status()


class InkVisionFirmwareUpdateButton(InkVisionButtonEntity):
    """Button to start firmware update. Deprecated: use the update entity."""

    _attr_translation_key = "firmware_update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{coordinator.device_key}_firmware_update"

    async def async_press(self) -> None:
        """Re-check for an update, then install it."""
        await self.coordinator.async_install_firmware()

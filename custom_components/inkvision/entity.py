"""Base entity for inkVision."""
from __future__ import annotations

from typing import override

from homeassistant.components.automation import automations_with_entity
from homeassistant.components.script import scripts_with_entity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import InkVisionDataUpdateCoordinator


class InkVisionEntity(CoordinatorEntity[InkVisionDataUpdateCoordinator]):
    """An entity of an inkVision device."""

    _attr_has_entity_name = True

    @property
    @override
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        status = self.coordinator.data.status
        mac_address = status.get("mac_address", "")

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_key)},
            connections={(CONNECTION_NETWORK_MAC, format_mac(mac_address))} if mac_address else set(),
            name=status.get("device", "inkVision"),
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=status.get("version", "Unknown"),
            configuration_url=f"http://{self.coordinator.host}",
        )


@callback
def async_keep_deprecated_entity(
    hass: HomeAssistant, platform: str, unique_id: str, replacement: str
) -> bool:
    """Return whether to still create an entity that a newer one replaces.

    Only devices that already have it keep it, until it is removed in a later
    release. A disabled one is removed now. If an automation or script uses it,
    a repair issue names the replacement.
    """
    entity_registry = er.async_get(hass)
    if (entity_id := entity_registry.async_get_entity_id(platform, DOMAIN, unique_id)) is None:
        return False

    issue_id = f"deprecated_entity_{entity_id}"
    entity = entity_registry.async_get(entity_id)
    if entity is not None and entity.disabled:
        entity_registry.async_remove(entity_id)
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return False

    if users := automations_with_entity(hass, entity_id) + scripts_with_entity(hass, entity_id):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="deprecated_entity",
            translation_placeholders={
                "entity": entity_id,
                "replacement": replacement,
                "users": "\n".join(f"- {user}" for user in users),
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    return True

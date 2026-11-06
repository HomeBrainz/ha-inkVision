"""Actions (services) for inkVision."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)

from . import legacy_migration
from .const import CUSTOM_SCREEN_SLOTS, DOMAIN
from .coordinator import InkVisionDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_BRIGHTNESS = "set_brightness"
SERVICE_DISPLAY_TEXT = "display_text"
SERVICE_RESTART_DEVICE = "restart_device"
SERVICE_SET_SCREEN_ROTATION = "set_screen_rotation"
SERVICE_SET_TIMEZONE = "set_timezone"
SERVICE_SET_CUSTOM_SCREEN = "set_custom_screen"

# Actions that duplicate an entity: the entity action and entity that replace them.
DEPRECATED_ACTIONS = {
    SERVICE_SET_BRIGHTNESS: ("number.set_value", "Display brightness"),
    SERVICE_DISPLAY_TEXT: ("text.set_value", "Custom message"),
    SERVICE_SET_TIMEZONE: ("select.select_option", "Timezone"),
}

BRIGHTNESS_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("brightness"): vol.Coerce(int),
})

DISPLAY_TEXT_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("text"): str,
})

RESTART_DEVICE_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
})

SCREEN_ROTATION_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("screens"): vol.All(cv.ensure_list, [cv.string]),
})

TIMEZONE_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("timezone"): str,
})

CUSTOM_SCREEN_SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=CUSTOM_SCREEN_SLOTS)),
    vol.Optional("text", default=""): cv.string,
})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the inkVision actions. Called once from async_setup."""

    async def set_brightness_service(call: ServiceCall) -> None:
        """Handle set brightness service call."""
        device_id = call.data["device_id"]
        brightness = call.data["brightness"]

        coordinator = _get_coordinator_for_device(hass, device_id)
        if not await coordinator.send_device_command("set_brightness", value=brightness):
            raise coordinator.error("not_connected")
        _LOGGER.info("Set brightness to %d for device %s", brightness, device_id)

    async def display_text_service(call: ServiceCall) -> None:
        """Handle display text service call."""
        device_id = call.data["device_id"]
        text = call.data["text"]

        coordinator = _get_coordinator_for_device(hass, device_id)
        if not await coordinator.send_device_command("display_text", text=text):
            raise coordinator.error("not_connected")
        _LOGGER.debug("Displayed text '%s' on device %s", text, device_id)

    async def restart_device_service(call: ServiceCall) -> None:
        """Handle restart device service call."""
        device_id = call.data["device_id"]

        coordinator = _get_coordinator_for_device(hass, device_id)
        if not await coordinator.send_device_command("restart"):
            raise coordinator.error("not_connected")
        _LOGGER.info("Restart command sent to device %s", device_id)

    async def set_screen_rotation_service(call: ServiceCall) -> None:
        """Handle screen rotation service call."""
        device_id = call.data["device_id"]
        screens = call.data["screens"]

        coordinator = _get_coordinator_for_device(hass, device_id)

        if isinstance(screens, str):
            screens = [screen.strip() for screen in screens.split(",") if screen.strip()]

        if not screens:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_screens")

        try:
            async with asyncio.timeout(10):
                async with coordinator.session.post(
                    f"http://{coordinator.host}/display/screens",
                    json={"screens": screens},
                ) as response:
                    if response.status != 200:
                        raise coordinator.error("device_rejected", status=str(response.status))
        except aiohttp.ClientError as err:
            raise coordinator.error("communication_error", error=str(err)) from err
        except asyncio.TimeoutError as err:
            raise coordinator.error("communication_error", error="timeout") from err
        _LOGGER.info("Updated screen rotation for device %s", device_id)

    async def set_timezone_service(call: ServiceCall) -> None:
        """Handle set timezone service call."""
        device_id = call.data["device_id"]
        timezone = call.data["timezone"]

        coordinator = _get_coordinator_for_device(hass, device_id)
        if not await coordinator.async_set_timezone(timezone):
            raise coordinator.error("request_failed")
        _LOGGER.info("Set timezone to %s for device %s", timezone, device_id)

    async def set_custom_screen_service(call: ServiceCall) -> None:
        """Handle set custom screen service call."""
        device_id = call.data["device_id"]
        slot = call.data["slot"]

        coordinator = _get_coordinator_for_device(hass, device_id)

        if not coordinator.custom_screens_supported:
            raise coordinator.error("custom_screens_unsupported")

        await coordinator.async_set_custom_screen(slot, call.data["text"])

    services = {
        SERVICE_SET_BRIGHTNESS: (set_brightness_service, BRIGHTNESS_SCHEMA),
        SERVICE_DISPLAY_TEXT: (display_text_service, DISPLAY_TEXT_SCHEMA),
        SERVICE_RESTART_DEVICE: (restart_device_service, RESTART_DEVICE_SCHEMA),
        SERVICE_SET_SCREEN_ROTATION: (set_screen_rotation_service, SCREEN_ROTATION_SCHEMA),
        SERVICE_SET_TIMEZONE: (set_timezone_service, TIMEZONE_SCHEMA),
        SERVICE_SET_CUSTOM_SCREEN: (set_custom_screen_service, CUSTOM_SCREEN_SCHEMA),
    }
    for name in DEPRECATED_ACTIONS:
        services[name] = (_deprecated(hass, name, services[name][0]), services[name][1])
    for name, (handler, schema) in services.items():
        hass.services.async_register(DOMAIN, name, handler, schema=schema)
    legacy_migration.async_register_service_aliases(hass, services)  # legacy: HACS-only


def _deprecated(
    hass: HomeAssistant,
    name: str,
    handler: Callable[[ServiceCall], Coroutine[Any, Any, None]],
) -> Callable[[ServiceCall], Coroutine[Any, Any, None]]:
    """Run a deprecated action, and raise a repair issue naming its replacement."""
    replacement, entity = DEPRECATED_ACTIONS[name]

    async def deprecated(call: ServiceCall) -> None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"deprecated_action_{name}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="deprecated_action",
            translation_placeholders={
                "action": f"{DOMAIN}.{name}",
                "replacement": replacement,
                "entity": entity,
            },
        )
        await handler(call)

    return deprecated


@callback
def _get_coordinator_for_device(hass: HomeAssistant, device_id: str) -> InkVisionDataUpdateCoordinator:
    """Return the coordinator of a device's loaded config entry."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_loaded",
                translation_placeholders={"device": device.name_by_user or device.name or device_id},
            )
        return entry.runtime_data

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="device_not_found",
        translation_placeholders={"device_id": device_id},
    )

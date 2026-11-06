"""The inkVision integration."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import ConfigType

from . import legacy_migration
from .const import CONF_CUSTOM_SCREEN_PREFIX, CUSTOM_SCREEN_SLOTS, DOMAIN
from .coordinator import (
    InkVisionConfigEntry,
    InkVisionDataUpdateCoordinator,
    device_key,
    normalize_mac,
)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.MEDIA_PLAYER,
    Platform.TEXT,
    Platform.UPDATE,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    async_setup_services(hass)
    await legacy_migration.async_setup_legacy(hass)  # legacy: HACS-only
    return True


async def async_setup_entry(hass: HomeAssistant, entry: InkVisionConfigEntry) -> bool:
    """Set up inkVision from a config entry."""
    if legacy_migration.CONF_MIGRATE_FROM in entry.data:  # legacy: HACS-only
        await legacy_migration.async_take_over_legacy_entry(hass, entry)

    coordinator = InkVisionDataUpdateCoordinator(hass, entry)

    await coordinator.async_config_entry_first_refresh()

    # Start WebSocket connection
    await coordinator.async_start_websocket()

    # Independent of the coordinator's own timer, which WebSocket pushes keep
    # resetting; check once a minute and poll when the last poll is stale.
    entry.async_on_unload(
        async_track_time_interval(hass, coordinator.async_poll_if_stale, timedelta(minutes=1))
    )

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_track_custom_screen_templates(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True



@callback
def _async_track_custom_screen_templates(
    hass: HomeAssistant, entry: InkVisionConfigEntry, coordinator: InkVisionDataUpdateCoordinator
) -> None:
    """Keep each templated custom screen slot in sync with its template's result."""
    for slot in range(1, CUSTOM_SCREEN_SLOTS + 1):
        template_str = (entry.options.get(f"{CONF_CUSTOM_SCREEN_PREFIX}{slot}") or "").strip()
        if not template_str:
            continue

        template = Template(template_str, hass)

        @callback
        def _on_result(event, updates: list[TrackTemplateResult], slot=slot) -> None:
            result = updates.pop().result
            if isinstance(result, TemplateError):
                _LOGGER.warning("Custom screen %d template error: %s", slot, result)
                text = ""
            else:
                text = "" if result is None else str(result)
            if coordinator.custom_screens.get(slot) != text:
                hass.async_create_task(coordinator.async_set_custom_screen(slot, text))

        info = async_track_template_result(hass, [TrackTemplate(template, None)], _on_result)
        entry.async_on_unload(info.async_remove)
        info.async_refresh()


async def _async_options_updated(hass: HomeAssistant, entry: InkVisionConfigEntry) -> None:
    """Reload so changed custom screen templates take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: InkVisionConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version < 2:
        entity_registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        removed = 0

        for entity in entries:
            should_remove = entity.domain == "weather"
            if not should_remove:
                haystack = " ".join(
                    part for part in (entity.unique_id, entity.original_name, entity.entity_id) if part
                ).lower()
                should_remove = "weather" in haystack or "forecast" in haystack

            if should_remove:
                _LOGGER.info("Removing deprecated weather entity: %s", entity.entity_id)
                entity_registry.async_remove(entity.entity_id)
                removed += 1

        if removed:
            _LOGGER.info("Removed %d deprecated weather entities", removed)

        hass.config_entries.async_update_entry(entry, version=2)

    if entry.version < 3:
        entity_registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        removed = 0

        deprecated_tokens = [
            "tvoc",
            "co2",
            "aqi",
            "air quality",
            "air_quality",
            "humidity offset",
            "humidity_offset",
            "temperature offset",
            "temperature_offset",
            "display mode",
            "display_mode",
            "free heap",
            "free_heap",
            "sensors available",
            "sensors_available",
            "websocket",
            "websocket client",
            "websocket_clients",
            "wifi connected",
            "wifi_connected",
        ]

        for entity in entries:
            haystack = " ".join(
                part for part in (entity.unique_id, entity.original_name, entity.entity_id) if part
            ).lower()
            if any(token in haystack for token in deprecated_tokens):
                _LOGGER.info("Removing deprecated entity: %s", entity.entity_id)
                entity_registry.async_remove(entity.entity_id)
                removed += 1

        if removed:
            _LOGGER.info("Removed %d deprecated entities", removed)

        hass.config_entries.async_update_entry(entry, version=3)

    if entry.version == 3 and entry.minor_version < 2:
        # Key entities and the device by the MAC instead of the config entry ID.
        if entry.unique_id and (mac := normalize_mac(entry.unique_id)):
            hass.config_entries.async_update_entry(entry, unique_id=mac)
        key = device_key(entry)
        if key != entry.entry_id:
            entity_registry = er.async_get(hass)
            for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
                if entity.unique_id.startswith(f"{entry.entry_id}_"):
                    entity_registry.async_update_entity(
                        entity.entity_id,
                        new_unique_id=entity.unique_id.replace(entry.entry_id, key, 1),
                    )
        device_registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
            device_registry.async_update_device(
                device.id,
                new_identifiers={(d, v) for d, v in device.identifiers if d != DOMAIN} | {(DOMAIN, key)},
            )
        hass.config_entries.async_update_entry(entry, minor_version=2)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: InkVisionConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = entry.runtime_data

    await coordinator.async_clear_custom_screens()

    # Stop WebSocket connection
    await coordinator.async_stop_websocket()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

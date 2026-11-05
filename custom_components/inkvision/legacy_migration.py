"""Take over installs of the integration from before the rename (domain "homebrainz").

HACS-only: delete this module, repairs.py and the calls marked "legacy" in
__init__.py and config_flow.py before submitting the integration to Home
Assistant core.

On startup every "homebrainz" config entry is imported as an "inkvision"
entry. Before the new entry loads, the old entry's devices and entities move
over to it, so entity IDs, history and device IDs stay the same; then the old
entry is removed. The old "homebrainz.*" actions keep working as aliases.
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
import json
import logging
from pathlib import Path
import shutil
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, LEGACY_DOMAIN

_LOGGER = logging.getLogger(__name__)

# Entry data key: the homebrainz entry an imported inkvision entry replaces.
CONF_MIGRATE_FROM = "migrate_from"

ISSUE_LEGACY_FOLDER = "legacy_folder"
ISSUE_LEGACY_SERVICE_PREFIX = "legacy_service_"
ISSUE_LEGACY_DUPLICATE_PREFIX = "legacy_duplicate_"

DATA_LEGACY_ALIASES = f"{DOMAIN}_legacy_aliases"


def legacy_folder(hass: HomeAssistant) -> Path:
    """Return where HACS left the pre-rename integration."""
    return Path(hass.config.path("custom_components", LEGACY_DOMAIN))


def _is_legacy_folder(path: Path) -> bool:
    """True when the folder holds our old integration, not something else named alike."""
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("domain") == LEGACY_DOMAIN


def remove_legacy_folder(hass: HomeAssistant) -> None:
    """Delete the old integration's folder (runs in the executor)."""
    path = legacy_folder(hass)
    if _is_legacy_folder(path):
        shutil.rmtree(path)


async def async_setup_legacy(hass: HomeAssistant) -> None:
    """Import homebrainz entries and flag a leftover homebrainz folder."""
    configured = {entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)}
    for old in hass.config_entries.async_entries(LEGACY_DOMAIN):
        if old.unique_id in configured:
            # The device was also added under the new name; leave the old one to the user.
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"{ISSUE_LEGACY_DUPLICATE_PREFIX}{old.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="legacy_duplicate",
                translation_placeholders={"title": old.title},
            )
            continue
        # Not awaited: the new entry's setup waits for this integration to finish setting up.
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={**old.data, CONF_MIGRATE_FROM: old.entry_id},
            )
        )

    if await hass.async_add_executor_job(_is_legacy_folder, legacy_folder(hass)):
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_LEGACY_FOLDER,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_LEGACY_FOLDER,
            translation_placeholders={"path": str(legacy_folder(hass))},
        )


async def async_take_over_legacy_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move the old entry's devices and entities to this entry, then remove the old entry."""
    old_id = entry.data[CONF_MIGRATE_FROM]
    if (old := hass.config_entries.async_get_entry(old_id)) is not None:
        if old.state is ConfigEntryState.SETUP_IN_PROGRESS:
            raise ConfigEntryNotReady(f"Waiting for {old.title} ({LEGACY_DOMAIN}) to finish loading")
        if old.state is not ConfigEntryState.NOT_LOADED and old.state.recoverable:
            # The old folder is still installed and its code has the entry loaded.
            await hass.config_entries.async_unload(old_id)

        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(entity_registry, old_id):
            # An entity that isn't loaded keeps a placeholder state, which blocks the move.
            if (state := hass.states.get(entity.entity_id)) is not None and state.attributes.get("restored"):
                hass.states.async_remove(entity.entity_id)
            entity_registry.async_update_entity_platform(
                entity.entity_id,
                DOMAIN,
                new_config_entry_id=entry.entry_id,
                # Unique IDs start with the config entry ID.
                new_unique_id=entity.unique_id.replace(old_id, entry.entry_id, 1),
            )

        device_registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(device_registry, old_id):
            # Two calls: adding and removing an entry in one update loses the new
            # entry's subentry bookkeeping.
            device_registry.async_update_device(
                device.id,
                add_config_entry_id=entry.entry_id,
                new_identifiers={
                    (DOMAIN if domain == LEGACY_DOMAIN else domain, value)
                    for domain, value in device.identifiers
                },
            )
            device_registry.async_update_device(device.id, remove_config_entry_id=old_id)

        await hass.config_entries.async_remove(old_id)
        _LOGGER.info("Moved %s from %s to %s", entry.title, LEGACY_DOMAIN, DOMAIN)

    hass.config_entries.async_update_entry(
        entry, data={key: value for key, value in entry.data.items() if key != CONF_MIGRATE_FROM}
    )


@callback
def async_register_service_aliases(
    hass: HomeAssistant,
    services: dict[str, tuple[Callable[[ServiceCall], Coroutine[Any, Any, None]], vol.Schema]],
) -> None:
    """Keep "homebrainz.<action>" working; each use raises a repair issue naming the new action."""
    aliases: set[str] = hass.data.setdefault(DATA_LEGACY_ALIASES, set())
    for name, (handler, schema) in services.items():
        if hass.services.has_service(LEGACY_DOMAIN, name):
            # The old integration is still installed and registered it itself.
            continue

        async def alias(call: ServiceCall, name: str = name, handler=handler) -> None:
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"{ISSUE_LEGACY_SERVICE_PREFIX}{name}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="legacy_service",
                translation_placeholders={
                    "old": f"{LEGACY_DOMAIN}.{name}",
                    "new": f"{DOMAIN}.{name}",
                },
            )
            await handler(call)

        hass.services.async_register(LEGACY_DOMAIN, name, alias, schema=schema)
        aliases.add(name)


@callback
def async_remove_service_aliases(hass: HomeAssistant) -> None:
    """Remove the aliases this integration registered."""
    for name in hass.data.pop(DATA_LEGACY_ALIASES, set()):
        hass.services.async_remove(LEGACY_DOMAIN, name)

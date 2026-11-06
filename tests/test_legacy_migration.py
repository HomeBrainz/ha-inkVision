"""Tests for taking over installs from before the rename (domain "homebrainz")."""
from __future__ import annotations

from ipaddress import ip_address
import json
from pathlib import Path
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.inkvision.const import DOMAIN, LEGACY_DOMAIN
from custom_components.inkvision.legacy_migration import (
    CONF_MIGRATE_FROM,
    ISSUE_LEGACY_FOLDER,
    async_setup_legacy,
    legacy_folder,
)
from homeassistant.config_entries import SOURCE_ZEROCONF, ConfigEntryDisabler
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.setup import async_setup_component

from .conftest import (
    BASE_URL,
    HOST,
    INFO_PAYLOAD,
    MAC,
    UNIQUE_ID,
    FakeWebSocketFactory,
    mock_device_http,
)

OLD_ENTRY_ID = "01OLDHOMEBRAINZENTRY0000000"


def _add_legacy_install(hass: HomeAssistant) -> tuple[MockConfigEntry, str, str]:
    """Register a homebrainz entry with a device and an entity, as the old code left them."""
    old = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        entry_id=OLD_ENTRY_ID,
        title="Atmos Smart Clock",
        data={CONF_HOST: HOST},
        options={"custom_screen_1": "{{ 21 }}"},
        unique_id=UNIQUE_ID,
        version=3,
        minor_version=2,
    )
    old.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=old.entry_id, identifiers={(LEGACY_DOMAIN, MAC)}, name="Atmos Smart Clock"
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        LEGACY_DOMAIN,
        f"{OLD_ENTRY_ID}_ip_address",
        config_entry=old,
        device_id=device.id,
        suggested_object_id="kitchen_clock_ip",
    )
    return old, device.id, entity.entity_id


async def _setup(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def _unload_all(hass: HomeAssistant) -> None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_legacy_entry_is_taken_over(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test devices, entities and options move over and the old entry is removed."""
    mock_device_http(aioclient_mock)
    _, device_id, entity_id = _add_legacy_install(hass)

    await _setup(hass)

    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == []
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.unique_id == UNIQUE_ID
    assert entry.title == "Atmos Smart Clock"
    assert entry.data == {CONF_HOST: HOST}
    assert entry.options == {"custom_screen_1": "{{ 21 }}"}

    entity = er.async_get(hass).async_get(entity_id)
    assert entity is not None
    assert entity.platform == DOMAIN
    assert entity.config_entry_id == entry.entry_id
    assert entity.unique_id == f"{UNIQUE_ID}_ip_address"
    # Same entity ID, now backed by the new entry and showing live data.
    assert hass.states.get(entity_id).state == HOST

    device = dr.async_get(hass).async_get(device_id)
    assert device is not None
    assert device.config_entries == {entry.entry_id}
    assert device.identifiers == {(DOMAIN, UNIQUE_ID)}
    assert (LEGACY_DOMAIN, MAC) not in device.identifiers

    await _unload_all(hass)


async def test_legacy_service_alias(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test homebrainz.* actions still work and raise a repair issue."""
    mock_device_http(aioclient_mock)
    _, device_id, _ = _add_legacy_install(hass)
    await _setup(hass)
    await mock_websocket.wait_listening()

    assert hass.services.has_service(LEGACY_DOMAIN, "set_screen_rotation")
    aioclient_mock.post(f"{BASE_URL}/display/screens", json={})
    await hass.services.async_call(
        LEGACY_DOMAIN,
        "set_screen_rotation",
        {"device_id": device_id, "screens": ["clock"]},
        blocking=True,
    )

    assert aioclient_mock.mock_calls[-1][2] == {"screens": ["clock"]}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "legacy_service_set_screen_rotation")
    assert issue is not None
    assert issue.translation_placeholders == {
        "old": "homebrainz.set_screen_rotation",
        "new": "inkvision.set_screen_rotation",
    }

    await _unload_all(hass)
    # Registered in async_setup like the actions themselves, so they outlive the entries.
    assert hass.services.has_service(LEGACY_DOMAIN, "set_screen_rotation")


async def test_no_legacy_install(hass: HomeAssistant) -> None:
    """Test setup without old entries or folder starts nothing and raises no issue."""
    await _setup(hass)

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_FOLDER) is None


async def test_legacy_duplicate_is_left_alone(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test an old entry for a device already set up under the new name is reported, not moved."""
    mock_config_entry.add_to_hass(hass)
    old, _, entity_id = _add_legacy_install(hass)

    with patch("custom_components.inkvision.async_setup_entry", return_value=True):
        await _setup(hass)

    assert hass.config_entries.async_get_entry(old.entry_id) is not None
    assert er.async_get(hass).async_get(entity_id).platform == LEGACY_DOMAIN
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"legacy_duplicate_{old.entry_id}") is not None


async def test_import_without_legacy_entry_aborts(hass: HomeAssistant) -> None:
    """Test the import step aborts when the old entry is already gone."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "import"},
        data={CONF_HOST: HOST, CONF_MIGRATE_FROM: "missing"},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "legacy_entry_missing"


async def test_legacy_folder_repair(hass: HomeAssistant, hass_client, tmp_path: Path) -> None:
    """Test a leftover homebrainz folder raises a fixable issue that deletes it."""
    hass.config.config_dir = str(tmp_path)
    folder: Path = legacy_folder(hass)
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"domain": LEGACY_DOMAIN}))

    assert await async_setup_component(hass, "repairs", {})
    await _setup(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_FOLDER) is not None

    client = await hass_client()
    response = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": ISSUE_LEGACY_FOLDER}
    )
    flow_id = (await response.json())["flow_id"]
    response = await client.post(f"/api/repairs/issues/fix/{flow_id}", json={})
    assert (await response.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    assert not folder.exists()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_FOLDER) is None


async def test_unrelated_folder_is_kept(hass: HomeAssistant, tmp_path: Path) -> None:
    """Test a homebrainz folder that isn't our old integration raises no issue."""
    hass.config.config_dir = str(tmp_path)
    folder: Path = legacy_folder(hass)
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"domain": "something_else"}))

    await _setup(hass)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_FOLDER) is None
    assert folder.exists()


async def test_discovery_takes_over_legacy_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test discovering a device still set up under homebrainz moves it instead of adding it again.

    With no inkvision entry yet the integration isn't loaded at startup, so
    discovery is what triggers the first takeover.
    """
    mock_device_http(aioclient_mock)
    aioclient_mock.get(f"{BASE_URL}/api/info", json=INFO_PAYLOAD)
    _, device_id, entity_id = _add_legacy_install(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_ZEROCONF},
        data=ZeroconfServiceInfo(
            ip_address=ip_address(HOST),
            ip_addresses=[ip_address(HOST)],
            port=80,
            hostname="atmos-eeff.local.",
            type="_http._tcp.local.",
            name="atmos-eeff._http._tcp.local.",
            properties={},
        ),
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert result["title"] == "Atmos Smart Clock"
    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == []
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.options == {"custom_screen_1": "{{ 21 }}"}
    assert er.async_get(hass).async_get(entity_id).config_entry_id == entry.entry_id
    assert dr.async_get(hass).async_get(device_id).config_entries == {entry.entry_id}
    assert not [
        issue for (domain, issue) in ir.async_get(hass).issues if issue.startswith("legacy_duplicate")
    ]

    await _unload_all(hass)


async def test_user_flow_takes_over_legacy_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
) -> None:
    """Test adding a device by hand that is still set up under homebrainz moves it."""
    mock_device_http(aioclient_mock)
    aioclient_mock.get(f"{BASE_URL}/api/info", json=INFO_PAYLOAD)
    _, _, entity_id = _add_legacy_install(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: HOST})
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == []
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert er.async_get(hass).async_get(entity_id).config_entry_id == entry.entry_id

    await _unload_all(hass)


async def test_legacy_entry_disabled_while_old_folder_installed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_websocket: FakeWebSocketFactory,
    tmp_path: Path,
) -> None:
    """Test the emptied old entry is disabled, not removed, until the old folder is gone.

    Removing it while the old code is installed lets that code rediscover the
    device and add it back under homebrainz.
    """
    hass.config.config_dir = str(tmp_path)
    folder = legacy_folder(hass)
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"domain": LEGACY_DOMAIN}))
    mock_device_http(aioclient_mock)
    old, _, entity_id = _add_legacy_install(hass)

    await _setup(hass)

    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert er.async_get(hass).async_get(entity_id).config_entry_id == entry.entry_id
    kept = hass.config_entries.async_get_entry(old.entry_id)
    assert kept is not None
    assert kept.disabled_by is ConfigEntryDisabler.USER
    assert not [issue for (_, issue) in ir.async_get(hass).issues if issue.startswith("legacy_duplicate")]

    # Next start after the repair deleted the folder: the emptied entry goes away.
    (folder / "manifest.json").unlink()
    folder.rmdir()
    await async_setup_legacy(hass)
    await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(old.entry_id) is None
    assert er.async_get(hass).async_get(entity_id).config_entry_id == entry.entry_id
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_FOLDER) is None

    await _unload_all(hass)

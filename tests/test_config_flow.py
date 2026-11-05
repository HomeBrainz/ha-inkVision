"""Tests for the HomeBrainz config flow."""
from __future__ import annotations

import asyncio
from ipaddress import ip_address
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.homebrainz.config_flow import (
    _decode_discovery_value,
    _extract_device_info,
    normalize_host,
)
from custom_components.homebrainz.const import DOMAIN

from .conftest import BASE_URL, HOST, INFO_PAYLOAD, MAC

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


def _zeroconf_info(
    *,
    host: str = HOST,
    hostname: str = "atmos-eeff.local.",
    name: str = "atmos-eeff._http._tcp.local.",
    service_type: str = "_http._tcp.local.",
    properties: dict[Any, Any] | None = None,
) -> ZeroconfServiceInfo:
    """Build a zeroconf discovery for a device at host."""
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        port=80,
        hostname=hostname,
        type=service_type,
        name=name,
        properties=properties or {},
    )


async def _start_user_flow(hass: HomeAssistant) -> str:
    """Open the user step and return the flow id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    return result["flow_id"]


async def test_user_flow(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test a manually entered device creates an entry keyed by MAC."""
    aioclient_mock.get(f"{BASE_URL}/api/info", json=INFO_PAYLOAD)
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Atmos Smart Clock"
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == MAC
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    "user_host",
    [
        f"http://{HOST}/",
        f"  https://{HOST}  ",
        f"{HOST}.",
    ],
)
async def test_user_flow_normalizes_host(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    user_host: str,
) -> None:
    """Test URLs and trailing characters are stripped from the host."""
    aioclient_mock.get(f"{BASE_URL}/api/info", json=INFO_PAYLOAD)
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: user_host})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}


async def test_user_flow_without_mac_uses_host_as_unique_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test firmware that reports no MAC address falls back to the host."""
    aioclient_mock.get(f"{BASE_URL}/api/info", json={"device": "InkVision Frame"})
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "InkVision Frame"
    assert result["result"].unique_id == HOST


async def test_user_flow_falls_back_to_status_endpoint(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test older firmware without /api/info is identified through /status."""
    aioclient_mock.get(f"{BASE_URL}/api/info", status=404)
    aioclient_mock.get(
        f"{BASE_URL}/status",
        json={"device_name": "HomeBrainz Clock", "macAddress": MAC},
    )
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "HomeBrainz Clock"
    assert result["result"].unique_id == MAC
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"exc": aiohttp.ClientError()}, "cannot_connect"),
        ({"exc": asyncio.TimeoutError()}, "cannot_connect"),
        ({"text": "not json"}, "cannot_connect"),
        ({"status": 500}, "cannot_connect"),
        ({"json": {"device": "Shelly Plug", "mac": MAC}}, "invalid_device"),
        ({"json": ["not", "a", "dict"]}, "invalid_device"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, Any],
    error: str,
) -> None:
    """Test connection and identification errors, then recovery."""
    aioclient_mock.get(f"{BASE_URL}/api/info", **mock_kwargs)
    aioclient_mock.get(f"{BASE_URL}/status", **mock_kwargs)
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE_URL}/api/info", json=INFO_PAYLOAD)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == MAC


async def test_user_flow_non_homebrainz_wins_over_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a reachable non-HomeBrainz device reports invalid_device, not cannot_connect."""
    aioclient_mock.get(f"{BASE_URL}/api/info", json={"device": "Shelly Plug"})
    aioclient_mock.get(f"{BASE_URL}/status", exc=aiohttp.ClientError())
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_device"}


async def test_user_flow_unknown_error(hass: HomeAssistant) -> None:
    """Test an unexpected exception is reported as unknown."""
    flow_id = await _start_user_flow(hass)

    with patch(
        "custom_components.homebrainz.config_flow.validate_input",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test adding the same device twice aborts."""
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE_URL}/api/info", json=INFO_PAYLOAD)
    flow_id = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_HOST: HOST})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    "discovery_info",
    [
        _zeroconf_info(
            service_type="_homebrainz._tcp.local.",
            name="Living room._homebrainz._tcp.local.",
            hostname="esp32.local.",
        ),
        _zeroconf_info(
            name="Living room._http._tcp.local.",
            hostname="esp32.local.",
            properties={"path": "/api/info"},
        ),
        _zeroconf_info(
            name="Living room._http._tcp.local.",
            hostname="esp32.local.",
            properties={b"path": b"/api/info"},
        ),
        _zeroconf_info(name="homebrainz-1._http._tcp.local."),
        _zeroconf_info(name="hbz-1._http._tcp.local."),
        _zeroconf_info(name="inkvision-1._http._tcp.local."),
        _zeroconf_info(name="Living room._http._tcp.local.", hostname="atmos-eeff.local."),
    ],
    ids=[
        "homebrainz_service",
        "api_info_path",
        "api_info_path_bytes",
        "homebrainz_name",
        "hbz_name",
        "inkvision_name",
        "atmos_hostname",
    ],
)
async def test_zeroconf_flow(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_setup_entry: AsyncMock,
    discovery_info: ZeroconfServiceInfo,
) -> None:
    """Test every zeroconf matcher in the manifest leads to an entry."""
    aioclient_mock.get(f"{BASE_URL}/api/info", json=INFO_PAYLOAD)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=discovery_info
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Atmos Smart Clock"
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == MAC
    assert len(mock_setup_entry.mock_calls) == 1


async def test_zeroconf_flow_not_homebrainz(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an unrelated _http._tcp service is ignored without probing it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_ZEROCONF},
        data=_zeroconf_info(name="printer._http._tcp.local.", hostname="printer.local."),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_device"
    assert aioclient_mock.call_count == 0


async def test_zeroconf_flow_without_host(hass: HomeAssistant) -> None:
    """Test a discovery that carries no address aborts."""
    discovery_info = SimpleNamespace(
        host="",
        hostname="atmos-eeff.local.",
        name="atmos-eeff._http._tcp.local.",
        server=None,
        type="_http._tcp.local.",
        properties={},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=discovery_info
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.parametrize(
    ("mock_kwargs", "reason"),
    [
        ({"exc": aiohttp.ClientError()}, "cannot_connect"),
        ({"json": {"device": "Shelly Plug"}}, "invalid_device"),
    ],
)
async def test_zeroconf_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, Any],
    reason: str,
) -> None:
    """Test a discovered device that can't be reached or identified aborts."""
    aioclient_mock.get(f"{BASE_URL}/api/info", **mock_kwargs)
    aioclient_mock.get(f"{BASE_URL}/status", **mock_kwargs)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_zeroconf_flow_updates_host_of_configured_device(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test rediscovery at a new address aborts and updates the entry's host."""
    mock_config_entry.add_to_hass(hass)
    new_host = "192.168.1.77"
    aioclient_mock.get(f"http://{new_host}/api/info", json=INFO_PAYLOAD)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info(host=new_host)
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == new_host


async def test_zeroconf_flow_without_mac_uses_host_as_unique_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a discovered device without a MAC address is keyed by host."""
    aioclient_mock.get(f"{BASE_URL}/api/info", json={"model": "Bento Bar"})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info()
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bento Bar"
    assert result["result"].unique_id == HOST


async def test_options_flow(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test editing the custom screen templates."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert set(result["data_schema"].schema) == {
        "custom_screen_1",
        "custom_screen_2",
        "custom_screen_3",
        "custom_screen_4",
    }
    assert "{{" in result["description_placeholders"]["example"]

    template = "{{ states('sensor.outdoor_temperature') }}°C"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"custom_screen_1": template}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {"custom_screen_1": template}


async def test_options_flow_suggests_current_templates(
    hass: HomeAssistant,
) -> None:
    """Test the form pre-fills templates that are already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        unique_id=MAC,
        version=3,
        options={"custom_screen_2": "{{ now().hour }}"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    suggested = {
        str(key): (key.description or {}).get("suggested_value")
        for key in result["data_schema"].schema
    }
    assert suggested == {
        "custom_screen_1": None,
        "custom_screen_2": "{{ now().hour }}",
        "custom_screen_3": None,
        "custom_screen_4": None,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [(b"atmos", "atmos"), (b"\xffhbz", "hbz"), ("hbz", "hbz"), (None, ""), (0, "")],
)
def test_decode_discovery_value(value: Any, expected: str) -> None:
    """Test zeroconf values are decoded to plain strings."""
    assert _decode_discovery_value(value) == expected


def test_normalize_host() -> None:
    """Test host normalization strips schemes, slashes and the root dot."""
    assert normalize_host(" http://clock.local./ ") == "clock.local"
    assert normalize_host("192.168.1.50") == "192.168.1.50"


@pytest.mark.parametrize(
    ("payload", "title"),
    [
        ({"name": "My Atmos"}, "My Atmos"),
        ({"device_name": "Bento"}, "Bento"),
        ({"model": "HBZ-1"}, "HBZ-1"),
        ({"device_type": "inkvision", "mac": MAC}, "HomeBrainz"),
        ({"type": "HOMEBRAINZ_DEVICE"}, "HomeBrainz"),
    ],
)
def test_extract_device_info_markers(payload: dict[str, Any], title: str) -> None:
    """Test each identification marker the firmware variants send."""
    info = _extract_device_info(payload, HOST)
    assert info is not None
    assert info["title"] == title
    assert info["host"] == HOST


@pytest.mark.xfail(
    reason=(
        "Bug: _extract_device_info includes the fallback title 'HomeBrainz' in its "
        "markers, so any JSON object without name/device/model keys is accepted"
    ),
    strict=True,
)
def test_extract_device_info_rejects_unmarked_payload() -> None:
    """Test a JSON object with no HomeBrainz markers is not identified as one."""
    assert _extract_device_info({"status": "ok", "uptime": 12}, HOST) is None

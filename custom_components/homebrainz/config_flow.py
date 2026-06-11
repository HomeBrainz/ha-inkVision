"""Config flow for HomeBrainz integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import async_timeout
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    # Newer Home Assistant versions
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # pragma: no cover - fallback for older HA cores
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DISCOVERY_ENDPOINTS: tuple[str, ...] = (
    "/api/info",
    "/status",
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)


def normalize_host(host: str) -> str:
    """Normalize user/discovery host input to a plain host value."""
    normalized_host = host.strip()

    if normalized_host.startswith(("http://", "https://")):
        normalized_host = normalized_host.split("://", 1)[1]

    return normalized_host.rstrip("/").rstrip(".")


def _decode_discovery_value(value: Any) -> str:
    """Decode zeroconf values that may be returned as bytes."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")

    return str(value or "")


def _is_homebrainz_discovery(discovery_info: ZeroconfServiceInfo) -> bool:
    """Check whether the zeroconf service looks like a HomeBrainz device."""
    properties = getattr(discovery_info, "properties", {}) or {}
    discovery_name = _decode_discovery_value(getattr(discovery_info, "name", "")).lower()
    hostname = _decode_discovery_value(getattr(discovery_info, "hostname", "")).lower()
    server = _decode_discovery_value(getattr(discovery_info, "server", "")).lower()
    service_type = _decode_discovery_value(getattr(discovery_info, "type", "")).lower()
    path = _decode_discovery_value(properties.get("path") or properties.get(b"path")).lower()

    if service_type == "_homebrainz._tcp.local.":
        return True

    if path == "/api/info":
        return True

    return any(
        candidate.startswith(("homebrainz", "hbz"))
        for candidate in (discovery_name, hostname, server)
        if candidate
    )


def _extract_device_info(payload: Any, host: str) -> dict[str, Any] | None:
    """Normalize device info returned by different HomeBrainz firmware APIs."""
    if not isinstance(payload, dict):
        return None

    mac_address = (
        payload.get("mac_address")
        or payload.get("macAddress")
        or payload.get("mac")
        or ""
    )
    title = (
        payload.get("name")
        or payload.get("device_name")
        or payload.get("device")
        or payload.get("model")
        or "HomeBrainz"
    )

    markers = (
        payload.get("device"),
        payload.get("name"),
        payload.get("device_name"),
        payload.get("device_type"),
        payload.get("model"),
        payload.get("type"),
        title,
    )
    supported_markers = (
        "homebrainz",
        "inkvision",
        "atmos",
        "bento",
        "hbz",
    )
    looks_like_homebrainz = any(
        isinstance(marker, str)
        and any(candidate in marker.lower() for candidate in supported_markers)
        for marker in markers
    )

    if payload.get("type") == "HOMEBRAINZ_DEVICE":
        looks_like_homebrainz = True

    if payload.get("model") in {"ATMOS-SMART-CLOCK-001", "ATMOS_BOX_001", "BENTO-BAR-001"}:
        looks_like_homebrainz = True

    if not looks_like_homebrainz:
        return None

    return {
        "title": title,
        "host": host,
        "mac_address": mac_address,
    }


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    host = normalize_host(data[CONF_HOST])
    session = async_get_clientsession(hass)

    last_error: Exception | None = None
    received_non_homebrainz_response = False

    for endpoint in DISCOVERY_ENDPOINTS:
        try:
            async with async_timeout.timeout(10):
                async with session.get(f"http://{host}{endpoint}") as response:
                    if response.status != 200:
                        continue

                    payload = await response.json()

            info = _extract_device_info(payload, host)
            if info is not None:
                return info

            received_non_homebrainz_response = True
        except aiohttp.ClientError as err:
            last_error = err
        except (asyncio.TimeoutError, TimeoutError) as err:
            last_error = err
        except ValueError as err:
            last_error = err
        except Exception as err:  # pragma: no cover - defensive logging
            _LOGGER.debug("Unexpected validation error for %s%s: %s", host, endpoint, err)
            last_error = err

    if received_non_homebrainz_response:
        raise InvalidDevice

    if last_error is not None:
        raise CannotConnect from last_error

    raise CannotConnect


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HomeBrainz."""

    VERSION = 3

    async def async_step_zeroconf(
        self,
        discovery_info: ZeroconfServiceInfo,
    ) -> FlowResult:
        """Handle Zeroconf discovery."""
        if not _is_homebrainz_discovery(discovery_info):
            return self.async_abort(reason="invalid_device")

        host = discovery_info.host
        if not host:
            return self.async_abort(reason="cannot_connect")

        try:
            info = await validate_input(self.hass, {CONF_HOST: host})
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")
        except InvalidDevice:
            return self.async_abort(reason="invalid_device")

        unique_id = info["mac_address"] if info["mac_address"] else info["host"]
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=info["title"],
            data={CONF_HOST: info["host"]},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidDevice:
                errors["base"] = "invalid_device"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Create a unique ID based on MAC address if available
                unique_id = info["mac_address"] if info["mac_address"] else info["host"]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_HOST: info["host"]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidDevice(HomeAssistantError):
    """Error to indicate the device is not a valid HomeBrainz Clock device."""
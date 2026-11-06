"""Config flow for inkVision integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TemplateSelector

try:
    # Newer Home Assistant versions
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # pragma: no cover - fallback for older HA cores
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .const import CONF_CUSTOM_SCREEN_PREFIX, CUSTOM_SCREEN_SLOTS, DOMAIN, LEGACY_DOMAIN
from .legacy_migration import CONF_MIGRATE_FROM

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


def _is_inkvision_discovery(discovery_info: ZeroconfServiceInfo) -> bool:
    """Check whether the zeroconf service looks like an inkVision device."""
    properties = getattr(discovery_info, "properties", {}) or {}
    discovery_name = _decode_discovery_value(getattr(discovery_info, "name", "")).lower()
    hostname = _decode_discovery_value(getattr(discovery_info, "hostname", "")).lower()
    server = _decode_discovery_value(getattr(discovery_info, "server", "")).lower()
    service_type = _decode_discovery_value(getattr(discovery_info, "type", "")).lower()
    path = _decode_discovery_value(properties.get("path") or properties.get(b"path")).lower()

    # "homebrainz"/"hbz" here and below are what the firmware advertises and
    # reports, not the integration's name; they stay after the rename.
    if service_type == "_homebrainz._tcp.local.":
        return True

    if path == "/api/info":
        return True

    return any(
        candidate.startswith(("homebrainz", "hbz", "inkvision", "atmos"))
        for candidate in (discovery_name, hostname, server)
        if candidate
    )


def _extract_device_info(payload: Any, host: str) -> dict[str, Any] | None:
    """Normalize device info returned by different inkVision firmware APIs."""
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
        or "inkVision"
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
    looks_like_inkvision = any(
        isinstance(marker, str)
        and any(candidate in marker.lower() for candidate in supported_markers)
        for marker in markers
    )

    if payload.get("type") == "HOMEBRAINZ_DEVICE":
        looks_like_inkvision = True

    if not looks_like_inkvision:
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
    received_non_inkvision_response = False

    for endpoint in DISCOVERY_ENDPOINTS:
        try:
            async with asyncio.timeout(10):
                async with session.get(f"http://{host}{endpoint}") as response:
                    if response.status != 200:
                        continue

                    payload = await response.json()

            info = _extract_device_info(payload, host)
            if info is not None:
                return info

            received_non_inkvision_response = True
        except aiohttp.ClientError as err:
            last_error = err
        except (asyncio.TimeoutError, TimeoutError) as err:
            last_error = err
        except ValueError as err:
            last_error = err
        except Exception as err:  # pragma: no cover - defensive logging
            _LOGGER.debug("Unexpected validation error for %s%s: %s", host, endpoint, err)
            last_error = err

    if received_non_inkvision_response:
        raise InvalidDevice

    if last_error is not None:
        raise CannotConnect from last_error

    raise CannotConnect


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for inkVision."""

    VERSION = 3

    _discovered: dict[str, Any]

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlowHandler:
        """Return the options flow."""
        return OptionsFlowHandler(config_entry)

    async def async_step_zeroconf(
        self,
        discovery_info: ZeroconfServiceInfo,
    ) -> FlowResult:
        """Handle Zeroconf discovery."""
        if not _is_inkvision_discovery(discovery_info):
            return self.async_abort(reason="invalid_device")

        host = discovery_info.host
        if not host:
            return self.async_abort(reason="cannot_connect")

        # Newer firmware advertises its MAC, so a known device is recognised
        # without contacting it. Older firmware is identified by the probe below.
        properties = discovery_info.properties or {}
        if mac := _decode_discovery_value(properties.get("mac") or properties.get(b"mac")):
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        try:
            info = await validate_input(self.hass, {CONF_HOST: host})
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")
        except InvalidDevice:
            return self.async_abort(reason="invalid_device")

        unique_id = info["mac_address"] if info["mac_address"] else info["host"]
        await self.async_set_unique_id(unique_id)
        # Rediscovery of a configured device: refresh its host (DHCP may have
        # moved it) and let HA reload an entry stuck in setup retry right away
        # instead of waiting out the retry backoff (up to 10 minutes).
        self._abort_if_unique_id_configured(updates={CONF_HOST: info["host"]})
        if (old := self._async_legacy_entry()) is not None:  # legacy: HACS-only
            return await self.async_step_import(
                {**old.data, CONF_HOST: info["host"], CONF_MIGRATE_FROM: old.entry_id}
            )

        self._discovered = info
        self.context["title_placeholders"] = {"name": info["title"]}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask the user before adding a discovered device."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered["title"],
                data={CONF_HOST: self._discovered["host"]},
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                "name": self._discovered["title"],
                "host": self._discovered["host"],
            },
        )

    @callback
    def _async_legacy_entry(self) -> config_entries.ConfigEntry | None:
        """Return the "homebrainz" entry for this device, if it was set up before the rename.

        Legacy, HACS-only. The integration only loads once it has an entry, so the
        first takeover happens here, when the device is discovered or added again;
        setting up that entry then imports any other homebrainz entries.
        """
        return next(
            (
                entry
                for entry in self.hass.config_entries.async_entries(LEGACY_DOMAIN)
                if entry.unique_id == self.unique_id
            ),
            None,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Take over an entry of the pre-rename "homebrainz" domain (legacy: HACS-only)."""
        old = self.hass.config_entries.async_get_entry(import_data[CONF_MIGRATE_FROM])
        if old is None:
            return self.async_abort(reason="legacy_entry_missing")

        await self.async_set_unique_id(old.unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=old.title, data=import_data, options=dict(old.options))

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
                if (old := self._async_legacy_entry()) is not None:  # legacy: HACS-only
                    return await self.async_step_import(
                        {**old.data, CONF_HOST: info["host"], CONF_MIGRATE_FROM: old.entry_id}
                    )

                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_HOST: info["host"]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change the host of a configured device."""
        entry = self._get_reconfigure_entry()
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
                if info["mac_address"]:
                    # Firmware without a MAC is keyed by its host, which is what changes here.
                    await self.async_set_unique_id(info["mac_address"])
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: info["host"]}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or {CONF_HOST: entry.data[CONF_HOST]}
            ),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Custom rotation screen templates."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Not self.config_entry: newer cores set that themselves and warn when a flow assigns it.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit the template behind each custom screen slot."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = {}
        for slot in range(1, CUSTOM_SCREEN_SLOTS + 1):
            key = f"{CONF_CUSTOM_SCREEN_PREFIX}{slot}"
            current = self._entry.options.get(key)
            schema[vol.Optional(key, description={"suggested_value": current} if current else None)] = (
                TemplateSelector()
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            # Passed as a placeholder: braces in translation strings are placeholders themselves.
            description_placeholders={"example": "{{ states('sensor.outdoor_temperature') }}°C"},
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidDevice(HomeAssistantError):
    """Error to indicate the device is not a valid inkVision device."""
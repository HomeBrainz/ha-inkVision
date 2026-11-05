"""Sensor platform for inkVision integration."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import InkVisionDataUpdateCoordinator
from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = [
        InkVisionTemperatureSensor(coordinator, config_entry),
        InkVisionHumiditySensor(coordinator, config_entry),
        InkVisionPressureSensor(coordinator, config_entry),
        InkVisionWiFiSignalSensor(coordinator, config_entry),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Gas Resistance",
            unique_id_suffix="gas_resistance",
            value_fn=lambda entity: entity.get_sensor_value("bme680", "gas_resistance_kohm"),
            native_unit_of_measurement="kΩ",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Device Uptime",
            unique_id_suffix="uptime",
            value_fn=lambda entity: entity.get_status_value("uptime"),
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Display Brightness",
            unique_id_suffix="brightness",
            value_fn=lambda entity: entity.get_status_value("brightness"),
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="IP Address",
            unique_id_suffix="ip_address",
            value_fn=lambda entity: entity.get_status_value("ip_address"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="MAC Address",
            unique_id_suffix="mac_address",
            value_fn=lambda entity: entity.get_status_value("mac_address"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Firmware Version",
            unique_id_suffix="firmware_version",
            value_fn=lambda entity: entity.get_status_value("version"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Firmware ID",
            unique_id_suffix="firmware_id",
            value_fn=lambda entity: entity.get_ota_value("currentFirmwareId"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Latest Firmware ID",
            unique_id_suffix="latest_firmware_id",
            value_fn=lambda entity: entity.get_ota_value("latestFirmwareId"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Latest Firmware Version",
            unique_id_suffix="latest_firmware_version",
            value_fn=lambda entity: entity.get_ota_value("latestVersion"),
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        # ── BSEC Air Quality sensors ──
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Indoor Air Quality",
            unique_id_suffix="bsec_iaq",
            value_fn=lambda entity: entity.get_sensor_value("bme680", "iaq"),
            icon="mdi:air-filter",
            native_unit_of_measurement="IAQ",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="IAQ Accuracy",
            unique_id_suffix="bsec_iaq_accuracy",
            value_fn=lambda entity: entity.get_sensor_value("bme680", "iaq_accuracy"),
            icon="mdi:tune-vertical",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Static IAQ",
            unique_id_suffix="bsec_static_iaq",
            value_fn=lambda entity: entity.get_sensor_value("bme680", "static_iaq"),
            icon="mdi:air-filter",
            native_unit_of_measurement="IAQ",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="CO₂ Equivalent",
            unique_id_suffix="bsec_co2_equivalent",
            value_fn=lambda entity: entity.get_sensor_value("bme680", "co2_equivalent"),
            device_class=SensorDeviceClass.CO2,
            native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="Breath VOC",
            unique_id_suffix="bsec_breath_voc",
            value_fn=lambda entity: entity.get_sensor_value("bme680", "breath_voc"),
            device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
            native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        InkVisionGenericSensor(
            coordinator,
            config_entry,
            name="IAQ Rating",
            unique_id_suffix="bsec_iaq_rating",
            value_fn=lambda entity: entity.get_sensor_value("bme680", "iaq_rating"),
            icon="mdi:air-purifier",
        ),
    ]

    async_add_entities(entities)


class InkVisionSensorEntity(CoordinatorEntity, SensorEntity):
    """Base class for inkVision sensor entities."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._host = config_entry.data["host"]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_name = "inkVision"
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

    def get_sensor_section(self, section: str) -> dict[str, Any] | None:
        """Return a sensor subsection from coordinator data."""
        if not self.coordinator.data:
            return None
        sensors = self.coordinator.data.get("sensors", {})
        section_data = sensors.get(section)
        return section_data if isinstance(section_data, dict) else None

    def get_sensor_value(self, section: str, key: str, default: Any | None = None) -> Any | None:
        """Get a value from a sensor subsection."""
        section_data = self.get_sensor_section(section)
        if section_data is None:
            return default
        return section_data.get(key, default)

    def get_status_value(self, key: str, default: Any | None = None) -> Any | None:
        """Get a value from the status section."""
        if not self.coordinator.data:
            return default
        status = self.coordinator.data.get("status", {})
        if not isinstance(status, dict):
            return default
        return status.get(key, default)

    def get_ota_value(self, key: str, default: Any | None = None) -> Any | None:
        """Get a value from the OTA section."""
        if not self.coordinator.data:
            return default
        ota = self.coordinator.data.get("ota", {})
        if not isinstance(ota, dict):
            return default
        return ota.get(key, default)


class InkVisionGenericSensor(InkVisionSensorEntity):
    """Generic sensor driven by simple extractor callbacks."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
        *,
        name: str,
        unique_id_suffix: str,
        value_fn: Callable[[InkVisionSensorEntity], Any],
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | UnitOfTemperature | UnitOfPressure | None = None,
        state_class: SensorStateClass | None = None,
        entity_category: EntityCategory | None = None,
        icon: str | None = None,
        suggested_display_precision: int | None = None,
        extra_attributes_fn: Callable[[InkVisionSensorEntity], dict[str, Any] | None] | None = None,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_{unique_id_suffix}"
        self._attr_name = name
        self._value_fn = value_fn
        self._extra_attributes_fn = extra_attributes_fn

        if device_class is not None:
            self._attr_device_class = device_class
        if state_class is not None:
            self._attr_state_class = state_class
        if native_unit_of_measurement is not None:
            self._attr_native_unit_of_measurement = native_unit_of_measurement
        if entity_category is not None:
            self._attr_entity_category = entity_category
        if icon is not None:
            self._attr_icon = icon
        if suggested_display_precision is not None:
            self._attr_suggested_display_precision = suggested_display_precision

    @property
    def native_value(self) -> Any | None:
        """Return the extracted value."""
        try:
            value = self._value_fn(self)
        except Exception:  # pragma: no cover - defensive guard on callback
            _LOGGER.exception("inkVision sensor '%s' failed to compute value", self._attr_name)
            return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._extra_attributes_fn:
            return None
        try:
            attrs = self._extra_attributes_fn(self)
        except Exception:  # pragma: no cover - defensive guard on callback
            _LOGGER.exception("inkVision sensor '%s' failed to compute attributes", self._attr_name)
            return None
        return attrs


class InkVisionTemperatureSensor(InkVisionSensorEntity):
    """Temperature sensor."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_temperature"
        self._attr_name = "Temperature"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        # Prefer BME680 temperature
        temp = self.get_sensor_value("bme680", "temperature")
        if temp is not None:
            _LOGGER.debug("Temperature sensor returning BME680 value: %s", temp)
            return temp

        # Legacy fallback for older firmware
        temp = self.get_sensor_value("aht20", "temperature")
        if temp is not None:
            _LOGGER.debug("Temperature sensor returning legacy AHT20 value: %s", temp)
            return temp

        _LOGGER.debug("Temperature sensor: no data available")
        return None


class InkVisionHumiditySensor(InkVisionSensorEntity):
    """Humidity sensor."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_humidity"
        self._attr_name = "Humidity"
        self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        humidity = self.get_sensor_value("bme680", "humidity")
        if humidity is not None:
            return humidity

        return self.get_sensor_value("aht20", "humidity")


class InkVisionPressureSensor(InkVisionSensorEntity):
    """Pressure sensor."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_pressure"
        self._attr_name = "Pressure"
        self._attr_device_class = SensorDeviceClass.ATMOSPHERIC_PRESSURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfPressure.HPA
        self._attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        pressure = self.get_sensor_value("bme680", "pressure")
        if pressure is not None:
            return pressure

        return self.get_sensor_value("bmp280", "pressure")


class InkVisionWiFiSignalSensor(InkVisionSensorEntity):
    """WiFi Signal sensor."""

    def __init__(
        self,
        coordinator: InkVisionDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_wifi_signal"
        self._attr_name = "WiFi Signal"
        self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        if self.coordinator.data and "status" in self.coordinator.data:
            status = self.coordinator.data["status"]
            return status.get("rssi")
        return None
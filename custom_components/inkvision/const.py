"""Constants for the inkVision integration."""

DOMAIN = "inkvision"
# Domain of the integration before the rename; see legacy_migration.py.
LEGACY_DOMAIN = "homebrainz"

# Device info
MANUFACTURER = "inkVision"
MODEL = "inkVision"

# Default values
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 30

# Configuration
CONF_HOST = "host"

# Custom rotation screens: options "custom_screen_1".."custom_screen_4" hold templates
CONF_CUSTOM_SCREEN_PREFIX = "custom_screen_"
CUSTOM_SCREEN_SLOTS = 4

# Sensor types
SENSOR_TEMPERATURE = "temperature"
SENSOR_HUMIDITY = "humidity"
SENSOR_PRESSURE = "pressure"
SENSOR_WIFI_SIGNAL = "wifi_signal"

# Extended telemetry
SENSOR_GAS_RESISTANCE = "gas_resistance"
SENSOR_UPTIME = "uptime"
SENSOR_BRIGHTNESS = "brightness"
SENSOR_IP_ADDRESS = "ip_address"
SENSOR_MAC_ADDRESS = "mac_address"
SENSOR_FIRMWARE_VERSION = "firmware_version"
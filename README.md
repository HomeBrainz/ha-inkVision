# HomeBrainz Home Assistant Integration for inkVision(R)

<p align="center">
  <img src="custom_components/homebrainz/icons/logo.svg" alt="HomeBrainz Logo" width="200">
</p>

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacsbadge]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]

**This integration will set up the following platforms.**

HomeBrainz develops the inkVision(R) product line. Learn more at [homebrainz.eu][homebrainz-site] and [inkvision.eu][inkvision-site].

Platform | Description
-- | --
`sensor` | Environmental, air-quality, device diagnostics, firmware metadata, and WiFi signal sensors
`number` | Display brightness control (0-15)
`select` | Device timezone selector
`switch` | Screen rotation toggles (clock, temp, humidity, pressure, gas, iaq)
`binary_sensor` | Firmware update availability state
`button` | Firmware check and firmware install actions
`media_player` | Speaker playback, volume, and mute controls

## Features

- **Real-time Updates First**: Uses WebSocket (`/ws`) for primary live updates with automatic retry, then falls back to periodic HTTP polling
- **Fallback Polling**: Polls every 5 minutes when WebSocket is unavailable
- **Multiple Sensor Support**: Monitors temperature, humidity, pressure, gas resistance, IAQ metrics, CO2 equivalent, breath VOC, and WiFi signal strength
- **Easy Setup**: Home Assistant configuration flow with mDNS discovery and manual host entry fallback
- **Device Information**: Exposes device details including firmware metadata, MAC, IP, uptime, and brightness diagnostics
- **Device Controls**: Supports brightness, timezone, screen rotation, firmware actions, and speaker control
- **Web Interface**: Direct link to device configuration page

## Supported Devices

- inkVision(R) Atmos Smart Clock (HomeBrainz firmware)
- inkVision(R) Bento Bar (HomeBrainz firmware, with compatible sensor package)
- inkVision(R) Clock (legacy HomeBrainz clock firmware)

Supported sensor packages include:
  - BME680 (+ BSEC2) for IAQ, CO2 equivalent, breath VOC, gas resistance, temperature, humidity, and pressure
  - Legacy fallback support for AHT20 and BMP280 endpoints
  - WiFi Signal Strength Monitoring

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/11ado33/ha-homebrainz-integration`
6. Select "Integration" as the category
7. Click "Add"
8. Find "HomeBrainz" in the integrations list and click "Download"
9. Restart Home Assistant

### Manual Installation

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`)
2. If you do not have a `custom_components` directory (folder) there, you need to create it
3. In the `custom_components` directory (folder) create a new folder called `homebrainz`
4. Download _all_ the files from the `custom_components/homebrainz/` directory (folder) in this repository
5. Place the files you downloaded in the new directory (folder) you created
6. Restart Home Assistant

## Configuration

### Setting up the Integration

1. In Home Assistant, go to **Settings** > **Devices & Services**
2. Click **Add Integration**
3. Search for "HomeBrainz" and select it
4. Enter your inkVision(R) device IP address (e.g., `192.168.1.207`)
5. Click **Submit**

Home Assistant auto-discovery works with current HomeBrainz and inkVision(R) mDNS variants, including `homebrainz*.local`, `HBZ*.local`, and devices that publish the dedicated `_homebrainz._tcp` service. You can always enter the host/IP manually as a fallback.

### Finding Your Device IP Address

You can find your device's IP address by:
- Checking your WiFi router's connected devices list
- Using a network scanner app
- Connecting to the device's serial console during boot

## Entities

Once configured, the integration creates sensors and controls across multiple platforms. Core entities include:

- `sensor.temperature`
- `sensor.humidity`
- `sensor.pressure`
- `sensor.wifi_signal`
- `sensor.gas_resistance`
- `sensor.indoor_air_quality`
- `sensor.static_iaq`
- `sensor.co2_equivalent`
- `sensor.breath_voc`
- `sensor.iaq_rating`
- `sensor.device_uptime` (diagnostic)
- `sensor.display_brightness` (diagnostic)
- `sensor.ip_address` (diagnostic)
- `sensor.mac_address` (diagnostic)
- `sensor.firmware_version` (diagnostic)
- `sensor.firmware_id` (diagnostic)
- `sensor.latest_firmware_id` (diagnostic)
- `sensor.latest_firmware_version` (diagnostic)
- `number.display_brightness`
- `select.timezone`
- `switch.screen_clock`, `switch.screen_temp`, `switch.screen_humidity`, `switch.screen_pressure`, `switch.screen_gas`, `switch.screen_iaq`
- `binary_sensor.firmware_update_available`
- `button.check_firmware_update`
- `button.install_firmware_update`
- `media_player.speaker`

Entity IDs can vary by Home Assistant naming rules and your device name.

## Services

The integration registers these Home Assistant services:

- `homebrainz.set_brightness`
- `homebrainz.display_text`
- `homebrainz.restart_device`
- `homebrainz.set_screen_rotation`
- `homebrainz.set_timezone`

## Device Configuration

Your inkVision(R) device can be configured through its web interface. The integration provides a direct link to the device configuration page in the device information panel.

### Web Interface Features

- Real-time sensor readings
- WiFi configuration
- MQTT settings (for alternative integration methods)
- Device information and diagnostics

## Troubleshooting

### Integration Won't Connect

1. Verify the device IP address is correct
2. Ensure the device is powered on and connected to your network
3. Check that Home Assistant can reach the device (same subnet/VLAN)
4. Verify the device's web interface is accessible by browsing to `http://[device-ip]/`

### Missing Sensors

If some sensors are not appearing:
1. Check the device's `/sensors` endpoint directly: `http://[device-ip]/sensors`
2. Ensure all sensor hardware is properly connected
3. Check device logs through serial console if available

### Update Issues

If the integration stops updating:
1. Check Home Assistant logs for connection errors
2. Verify the device is still accessible
3. Try reloading the integration from **Settings** > **Devices & Services**

## Contributing

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

[integration_blueprint]: https://github.com/ludeeus/integration_blueprint
[commits-shield]: https://img.shields.io/github/commit-activity/y/11ado33/ha-homebrainz-integration.svg?style=for-the-badge
[commits]: https://github.com/11ado33/ha-homebrainz-integration/commits/main
[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/11ado33/ha-homebrainz-integration.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-%4011ado33-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/11ado33/ha-homebrainz-integration.svg?style=for-the-badge
[releases]: https://github.com/11ado33/ha-homebrainz-integration/releases
[homebrainz-site]: https://homebrainz.eu
[inkvision-site]: https://inkvision.eu
[user_profile]: https://github.com/11ado33

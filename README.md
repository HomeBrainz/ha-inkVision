# HomeBrainz Home Assistant Integration for inkVision(R)

<p align="center">
  <img src="custom_components/homebrainz/icons/logo.svg" alt="HomeBrainz Logo" width="200">
</p>

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

Connect your inkVision(R) devices to Home Assistant for local monitoring and control. No cloud account is needed: the integration talks to the device directly on your network and receives updates in real time.

HomeBrainz develops the inkVision(R) product line. Learn more at [homebrainz.eu][homebrainz-site] and [inkvision.eu][inkvision-site].

## Supported Devices

Works with inkVision(R) devices running HomeBrainz firmware. Available entities depend on the device model and its sensors.

## Features

- **Local and real-time**: live updates over your local network, with automatic reconnection
- **Easy setup**: devices are discovered automatically; you can also add one by IP address
- **Environment sensors**: temperature, humidity, pressure, and air quality (IAQ, CO2 equivalent, VOC) on supported models
- **Display controls**: brightness, timezone, and which screens the display rotates through
- **Speaker** (models with a speaker): use the device as a media player for text-to-speech, internet radio, and your Home Assistant media library
- **Firmware updates**: see when an update is available and install it from Home Assistant

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.][my-hacs-badge]][my-hacs]

1. Click the button above, or open **HACS** in Home Assistant and search for **HomeBrainz**
2. Click **Download**
3. Restart Home Assistant

### Manual installation

1. Download the latest release from the [releases page][releases]
2. Copy the `custom_components/homebrainz` folder into the `custom_components` folder of your Home Assistant configuration directory (create `custom_components` if it does not exist)
3. Restart Home Assistant

## Configuration

[![Open your Home Assistant instance and start setting up HomeBrainz.][my-config-badge]][my-config]

Devices on your network are usually discovered automatically and appear under **Settings** > **Devices & Services** as ready to configure. To add a device yourself:

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration** and search for **HomeBrainz**
3. Enter the device's IP address or hostname

You can find the device's IP address in your router's list of connected devices.

## Entities

The integration creates the following entities, depending on your device:

Platform | Description
-- | --
`sensor` | Environment and air-quality readings, Wi-Fi signal, and device diagnostics
`number` | Display brightness
`select` | Device timezone
`switch` | Screens shown in the display rotation
`binary_sensor` | Firmware update available
`button` | Check for and install firmware updates
`media_player` | Speaker playback, volume, and mute

## Services

- `homebrainz.set_brightness`: set the display brightness
- `homebrainz.display_text`: show a custom text on the display
- `homebrainz.restart_device`: restart the device
- `homebrainz.set_screen_rotation`: choose which screens the display rotates through
- `homebrainz.set_timezone`: set the device timezone

All services take a target device and can be tried out under **Developer Tools** > **Actions**.

## Device Settings

Each device has its own settings page. Open it from the device page in Home Assistant (**Visit**).

## Troubleshooting

### The device cannot be added or shows as unavailable

1. Make sure the device is powered on and connected to Wi-Fi
2. Check that Home Assistant and the device are on the same network (same subnet/VLAN)
3. Open the device's settings page from a browser to confirm it is reachable
4. Reload the integration under **Settings** > **Devices & Services**

### The speaker does not play

1. Make sure the device firmware is up to date
2. Check that the volume is not muted or set very low
3. Check the Home Assistant logs for messages from the HomeBrainz integration

### Some sensors are missing

Sensors are created only for the hardware your device has. If an expected sensor is missing, update the firmware and reload the integration.

## Support

Found a bug or have a feature request? Please [open an issue][issues].

[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/HomeBrainz/ha-inkVision.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/HomeBrainz/ha-inkVision.svg?style=for-the-badge
[releases]: https://github.com/HomeBrainz/ha-inkVision/releases
[issues]: https://github.com/HomeBrainz/ha-inkVision/issues
[homebrainz-site]: https://homebrainz.eu
[inkvision-site]: https://inkvision.eu
[my-hacs]: https://my.home-assistant.io/redirect/hacs_repository/?owner=HomeBrainz&repository=ha-inkVision&category=integration
[my-hacs-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[my-config]: https://my.home-assistant.io/redirect/config_flow_start/?domain=homebrainz
[my-config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg

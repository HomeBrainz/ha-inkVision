---
title: inkVision
description: Instructions on how to integrate inkVision devices, such as the Atmos Smart Clock, into Home Assistant.
ha_category:
  - Media player
  - Sensor
  - Update
ha_release: TBD
ha_iot_class: Local Push
ha_config_flow: true
ha_codeowners:
  - '@11ado33'
ha_domain: inkvision
ha_zeroconf: true
ha_platforms:
  - binary_sensor
  - button
  - diagnostics
  - event
  - media_player
  - number
  - select
  - sensor
  - switch
  - text
  - update
ha_integration_type: device
ha_quality_scale: bronze
---

<!--
Draft of the home-assistant.io page, submitted as a pull request to
home-assistant/home-assistant.io (source/_integrations/inkvision.markdown)
together with the core pull request. Set ha_release then.
-->

The **inkVision** {% term integration %} connects [inkVision](https://inkvision.eu) devices to Home Assistant, such as the Atmos Smart Clock: a clock with an LED matrix display, environment and air-quality sensors, two touch buttons and a speaker.

Home Assistant talks to the device directly on your local network. The device pushes new readings as they happen, and no cloud account is needed. You can:

- Read temperature, humidity, pressure and air quality (IAQ, CO₂ equivalent, VOC).
- Set the display brightness, the timezone, and which screens the display rotates through.
- Show your own text on the display, as a message or as custom screens in the rotation.
- Use the touch buttons to trigger automations.
- Play text-to-speech, internet radio and media on the speaker.
- Install firmware updates.

## Prerequisites

- The device is set up and connected to your Wi-Fi network, for example with the HomeBrainz Hub app.
- Home Assistant and the device are on the same network, so Home Assistant can reach the device and discover it.

{% include integrations/config_flow.md %}

Home Assistant usually discovers inkVision devices on your network by itself and shows them as **Discovered**. To add a device that was not discovered, enter its address.

{% configuration_basic %}
Host:
  description: "The IP address or hostname of the device, for example `192.168.1.50`. You can find it in your router's list of connected devices."
{% endconfiguration_basic %}

## Actions

Most settings have an entity, for example the **Display brightness** number and the **Timezone** select. The integration also provides these actions.

### Action: Set screen rotation

The `inkvision.set_screen_rotation` action selects which screens the display rotates through, all in one call. The screens are always shown in the same order.

| Data attribute | Optional | Description |
| -------------- | -------- | ----------- |
| `device_id` | No | The inkVision device. |
| `screens` | No | The screens to rotate through: `clock`, `temp`, `humidity`, `pressure`, `gas`, `iaq`. |

```yaml
action: inkvision.set_screen_rotation
data:
  device_id: 0123456789abcdef0123456789abcdef
  screens:
    - clock
    - temp
```

### Action: Set custom screen

The `inkvision.set_custom_screen` action sets the text of one of the 4 custom screens. Screens with text join the display rotation after the regular screens; empty text removes the screen. Text longer than the display scrolls.

| Data attribute | Optional | Description |
| -------------- | -------- | ----------- |
| `device_id` | No | The inkVision device. |
| `slot` | No | The custom screen, 1 to 4. |
| `text` | Yes | The text to show, up to 64 characters. Leave it out or empty to remove the screen. |

```yaml
action: inkvision.set_custom_screen
data:
  device_id: 0123456789abcdef0123456789abcdef
  slot: 1
  text: "Outside {{ states('sensor.outdoor_temperature') }} °C"
```

The device doesn't store custom screens. Home Assistant sends them again whenever it reconnects to the device, and clears them when the integration is unloaded. A slot that has a template in the integration options is overwritten the next time the template's result changes.

## Removing the integration

This integration follows standard integration removal. No extra steps are required. The device keeps working on its own and keeps its settings.

{% include integrations/remove_device_service.md %}

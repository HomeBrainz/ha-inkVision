---
title: inkVision
description: Instructions on how to integrate the inkVision Atmos Smart Clock into Home Assistant.
ha_categories:
  - sensors.air_quality
  - sensors.temperature_humidity
  - media.media_player
  - firmware.update
ha_release: 2026.12
ha_iot_class: Local Push
ha_config_flow: true
ha_codeowners:
  - '@11ado33'
ha_domain: inkvision
ha_zeroconf: true
ha_platforms:
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
The full page, for when the follow-up platform PRs have landed. The page in the
home-assistant.io repo (source/_integrations/inkvision.markdown) only describes
what core has at that time; copy the matching sections from here with each
platform PR. Add a category and raise ha_quality_scale when core grants them.
-->

The **inkVision** {% term integration %} connects the [inkVision](https://inkvision.eu) Atmos Smart Clock to Home Assistant. The Atmos Smart Clock is a desk clock with an LED matrix display, an environment sensor that measures temperature, humidity, air pressure and indoor air quality, two touch buttons and a speaker.

Home Assistant talks to the clock directly on your local network. The clock pushes its readings to Home Assistant, and no cloud account is needed.

## Use cases

- Show the temperature, humidity and air quality of a room on your dashboard, and keep their history.
- Get a notification to open a window when the indoor air quality gets worse, or turn on a fan when the CO₂ equivalent rises.
- Use the touch buttons of the clock to control lights or scenes.
- Show your own text on the clock: the outdoor temperature, the next calendar event, or a message when the washing machine is done.
- Play text-to-speech announcements and internet radio on the clock's speaker.
- Dim the display at night and turn screens on or off from automations.
- Install firmware updates from Home Assistant.

## Supported devices

- Atmos Smart Clock

Other inkVision devices, such as the Frame and the Bento Bar, are not supported yet.

## Prerequisites

- The clock is set up and connected to your Wi-Fi network, for example with the HomeBrainz Hub app.
- Home Assistant and the clock are on the same network, so Home Assistant can reach the clock and discover it.

{% include integrations/config_flow.md %}

Home Assistant usually discovers the clock on your network by itself and shows it as **Discovered**. To add a clock that was not discovered, enter its address.

{% configuration_basic %}
Host:
  description: "The IP address or hostname of the clock, for example `192.168.1.50`. You can find it in your router's list of connected devices."
{% endconfiguration_basic %}

If the clock gets a new IP address later, Home Assistant updates it when it rediscovers the clock. You can also change it with the {% term "reconfigure" %} option of the integration entry.

## Configuration options

The custom screens of the clock are set in the integration options: open the integration entry and select **Configure**.

{% configuration_basic %}
Custom screen 1 to 4:
  description: "A [template](/docs/configuration/templating/) whose result is shown as a screen in the display rotation, after the regular screens, for example `{% raw %}{{ states('sensor.outdoor_temperature') }}°C{% endraw %}`. The screen updates whenever the template's result changes. Leave a field empty to remove that screen. Text longer than the display scrolls."
{% endconfiguration_basic %}

## Supported functionality

### Sensors

- **Temperature**: Room temperature. The sensor sits inside the clock, so it reads a little above the room temperature while the display is bright.
- **Humidity**: Relative humidity.
- **Atmospheric pressure**
- **Indoor air quality**: The IAQ index of the Bosch BME680 sensor, from 0 (clean air) to 500 (heavily polluted air). 0–50 is excellent, 51–100 good, 101–150 lightly polluted, 151–200 moderately polluted, above 200 heavily polluted.
- **IAQ rating**: The index above as a word, as the clock shows it.
- **Static IAQ**: The IAQ index without the short-term adaptation to the room, for comparing rooms or days.
- **IAQ accuracy**: How well the air quality sensor is calibrated, from 0 (not calibrated, values unreliable) to 3 (fully calibrated). Diagnostic.
- **CO₂ equivalent**: An estimate of the CO₂ concentration, calculated from the volatile organic compounds that the sensor measures. It is not a CO₂ measurement.
- **Breath VOC**: An estimate of the concentration of volatile organic compounds in breath.
- **Gas resistance**: The raw reading of the gas sensor, which the air quality values are calculated from.
- **Wi-Fi signal**: The Wi-Fi signal strength of the clock. Diagnostic.
- **Uptime**, **Reported brightness**, **IP address**, **MAC address**, **Firmware version**, **Firmware ID**, **Latest firmware version**, **Latest firmware ID**: Diagnostic, disabled by default.

After the clock starts, the air quality sensor calibrates itself, which takes a few hours. While **IAQ accuracy** is below 3, the air quality values are less reliable.

### Numbers

- **Display brightness**: Brightness of the LED display, 0 to 15. 0 turns the display off.

### Switches

- **Clock screen**, **Temperature screen**, **Humidity screen**, **Pressure screen**, **Gas screen**, **Air quality screen**: Whether the screen is in the display rotation. At least one screen stays on.

### Text

- **Custom message**: Shows a message of up to 96 characters on the display. Longer text scrolls, and the clock returns to its rotation after a while. Unavailable on firmware that cannot show messages.

### Selects

- **Timezone**: The timezone the clock shows the time in, from a list of common timezones.
- **Button 1 tap action**, **Button 1 long press action**, **Button 2 tap action**, **Button 2 long press action**: What the clock itself does on the gesture: brightness up or down, next or previous screen, screen on or off, or **None (Home Assistant only)**.

### Buttons

- **Restart**: Restarts the clock.

### Events

- **Button 1**, **Button 2**: Fire on every `single_tap` and `long_press` of the touch button. The event's `action` attribute tells you what the clock did itself. Unavailable on firmware that does not report button presses.

### Media player

- **Speaker**: Plays text-to-speech, internet radio and media from your media library on the clock's speaker, with volume and mute. You can select it as the output of an Assist pipeline for announcements.

The clock plays plain HTTP audio only. Home Assistant converts everything it sends with FFmpeg, so the source can be any format or stream, including `.pls` and `.m3u` radio playlists, and the clock fetches the converted audio from Home Assistant. For that, the clock must be able to reach the internal URL of your Home Assistant ({% my network title="**Settings** > **System** > **Network**" %}) over HTTP.

### Update

- **Firmware**: Shows whether a firmware update is available, with its release notes, and installs it. The clock downloads the firmware, flashes it and restarts.

## Actions

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
| `text` | Yes | The text to show. Leave it out or empty to remove the screen. |

```yaml
action: inkvision.set_custom_screen
data:
  device_id: 0123456789abcdef0123456789abcdef
  slot: 1
  text: "Washing done"
```

A slot that has a template in the integration options is overwritten the next time the template's result changes.

## Data updates

The clock pushes its sensor readings and status changes to Home Assistant over a WebSocket connection every few seconds, so the entities update as soon as a value changes. Button presses arrive the same way.

Every 5 minutes, Home Assistant also {% term polling polls %} the clock for what the WebSocket does not push: the firmware update check, the timezone and the screen rotation. If the WebSocket connection drops, Home Assistant reconnects within 10 to 30 seconds and polls right away, so the entities become unavailable within a minute when the clock is unplugged.

Changes you make in Home Assistant are sent to the clock immediately.

## Examples

### Open a window when the air gets bad

```yaml
automation:
  - alias: "Air quality warning"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.atmos_smart_clock_indoor_air_quality
        above: 150
        for: "00:10:00"
    conditions:
      - condition: numeric_state
        entity_id: sensor.atmos_smart_clock_iaq_accuracy
        above: 1
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "The air in the office is getting stale, open a window."
```

### Toggle a light with a button

If you want a button to do nothing on the clock itself, set its action to **None (Home Assistant only)**.

```yaml
automation:
  - alias: "Clock button toggles the desk lamp"
    triggers:
      - trigger: state
        entity_id: event.atmos_smart_clock_button_1
        attribute: event_type
        to: long_press
    actions:
      - action: light.toggle
        target:
          entity_id: light.desk_lamp
```

### Dim the display at night

```yaml
automation:
  - alias: "Clock night mode"
    triggers:
      - trigger: time
        at: "22:30:00"
        variables:
          brightness: 1
      - trigger: time
        at: "07:00:00"
        variables:
          brightness: 8
    actions:
      - action: number.set_value
        target:
          entity_id: number.atmos_smart_clock_display_brightness
        data:
          value: "{% raw %}{{ brightness }}{% endraw %}"
```

### Announce on the speaker

```yaml
action: tts.speak
target:
  entity_id: tts.home_assistant_cloud
data:
  media_player_entity_id: media_player.atmos_smart_clock_speaker
  message: "The dishwasher is done."
```

## Known limitations

- Only the Atmos Smart Clock is supported.
- The clock has no authentication on its local API, so anyone on your network can control it.
- The CO₂ equivalent and breath VOC values are estimates that the BME680 derives from its gas sensor, not measurements of CO₂ or of a specific compound. The sensor recalibrates after every restart; until then, **IAQ accuracy** is 0 and the values are unreliable.
- The speaker plays plain HTTP only. If Home Assistant is only reachable over HTTPS, or the clock cannot reach the internal URL of Home Assistant, media does not play. Pausing stops playback; the clock cannot resume where it stopped.
- The clock does not store custom screens or the custom message. Home Assistant sends the custom screens again whenever it reconnects, and clears them when the integration is unloaded.
- The **Timezone** select offers a fixed list of common timezones. Other timezones can be set in the HomeBrainz Hub app.
- The custom message and button events need recent firmware; on older firmware those entities are unavailable until you install the update.

## Troubleshooting

### The clock is not discovered

1. Make sure the clock is powered on and connected to Wi-Fi: its display shows the time.
2. Check that Home Assistant and the clock are on the same network and subnet. Discovery uses mDNS, which does not cross VLANs or subnets unless your router forwards it.
3. Add the clock by its IP address instead.

### The clock cannot be added

- **Failed to connect to the device**: Home Assistant cannot reach the address. Check the IP address in your router, make sure no firewall blocks Home Assistant from reaching the clock on port 80, and open `http://<address>/` in a browser; the clock's own settings page should appear.
- **The device at this address does not appear to be an inkVision device**: another device answers at that address. Check the IP address.
- **Device is already configured**: the clock is already set up; Home Assistant has updated its address.

### Entities are unavailable

The clock is not reachable. Check that it is powered and connected, and that its IP address has not changed; the reconfigure option of the entry lets you set a new address. If the clock is on, reload the integration entry.

### Values do not update

If the sensor values stay the same for minutes, the WebSocket connection has dropped and Home Assistant is reconnecting. Reconnection is automatic. If it never succeeds, restart the clock with the **Restart** button or by unplugging it.

### The speaker does not play

1. Check that the volume is not muted or very low.
2. Check the internal URL under {% my network title="**Settings** > **System** > **Network**" %}. It must start with `http://` and use an address the clock can reach, such as `http://192.168.1.10:8123`. If Home Assistant only has an HTTPS URL, playback fails.
3. Make sure the clock's firmware is up to date.
4. Enable debug logging and play something; the log shows the URL sent to the clock and any FFmpeg error.

### Buttons do nothing in Home Assistant

The event entities need firmware that reports button presses; install the update from the **Firmware** entity. If the clock itself reacts to the button but you only want the Home Assistant automation, set the button's action to **None (Home Assistant only)**.

### Some entities are missing

Entities are created for the hardware and firmware the clock has. Update the firmware and reload the integration. Diagnostic entities such as the IP address and uptime are disabled by default.

### Debug logging and diagnostics

Enable debug logging from the integration entry (three dots > **Enable debug logging**), reproduce the problem, then disable it again to download the log. Download diagnostics from the device page (three dots > **Download diagnostics**). Attach both to a bug report.

## Removing the integration

This integration follows standard integration removal. No extra steps are required. The clock keeps working on its own and keeps its settings; only its custom screens are cleared.

{% include integrations/remove_device_service.md %}

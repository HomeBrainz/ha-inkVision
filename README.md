# inkVision(R) Home Assistant Integration

<p align="center">
  <img src="custom_components/inkvision/icons/logo.svg" alt="inkVision logo" width="200">
</p>

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![hacs][hacsbadge]][hacs]

Connect your inkVision(R) devices to Home Assistant for local monitoring and control. No cloud account is needed: the integration talks to the device directly on your network and receives updates in real time.

HomeBrainz develops the inkVision(R) product line. Learn more at [homebrainz.eu][homebrainz-site] and [inkvision.eu][inkvision-site].

## Use cases

- Show the temperature, humidity and air quality of a room on your dashboard, and keep their history.
- Get a notification to open a window when the indoor air quality gets worse, or turn on a fan when the CO₂ equivalent rises.
- Use the two touch buttons of the clock to control lights or scenes.
- Show your own text on the clock: the outdoor temperature, the next calendar event, or a message when the washing machine is done.
- Play text-to-speech announcements and internet radio on the clock's speaker.
- Dim the display at night and turn screens on or off from automations.
- See when a firmware update is available and install it from Home Assistant.

## Supported devices

- **Atmos Smart Clock**: a desk clock with an LED matrix display, a Bosch BME680 environment sensor, two touch buttons and a speaker.

Other inkVision devices, such as the Frame and the Bento Bar, are set up with the HomeBrainz Hub app and are not supported by this integration yet.

The firmware of the clock determines which entities are available. Update it from the **Firmware** update entity to get all of them.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.][my-hacs-badge]][my-hacs]

1. Click the button above, or open **HACS** in Home Assistant and search for **inkVision**
2. Click **Download**
3. Restart Home Assistant

### Manual installation

1. Download the latest release from the [releases page][releases]
2. Copy the `custom_components/inkvision` folder into the `custom_components` folder of your Home Assistant configuration directory (create `custom_components` if it does not exist)
3. Restart Home Assistant

Home Assistant 2025.1 or newer is required.

## Configuration

### Prerequisites

- The clock is set up and connected to your Wi-Fi network, for example with the HomeBrainz Hub app.
- Home Assistant and the clock are on the same network, so Home Assistant can reach the clock and discover it.

### Adding a device

[![Open your Home Assistant instance and start setting up inkVision.][my-config-badge]][my-config]

Devices on your network are usually discovered automatically and appear under **Settings** > **Devices & services** as **Discovered**. Click **Add** and confirm. To add a device that was not discovered:

1. Go to **Settings** > **Devices & services**
2. Click **Add Integration** and search for **inkVision**
3. Enter the device's address

Setting | Description
-- | --
**Host** | The IP address or hostname of the clock, for example `192.168.1.50`. You can find it in your router's list of connected devices.

If the clock gets a new IP address later, Home Assistant picks it up when it rediscovers the clock. You can also change the address yourself: open the integration entry, click the three dots and choose **Reconfigure**.

### Options

Open **Settings** > **Devices & services** > **inkVision** > **Configure** to set the custom screens of the clock.

Setting | Description
-- | --
**Custom screen 1** to **Custom screen 4** | A [template](https://www.home-assistant.io/docs/configuration/templating/) whose result is shown as a screen in the display rotation, after the regular screens, for example `{{ states('sensor.outdoor_temperature') }}°C`. The screen updates whenever the template's result changes. Leave a field empty to remove that screen. Text longer than the display scrolls.

Custom screens need firmware with custom screen support; the **Firmware** entity tells you if an update is available.

### Upgrading from the HomeBrainz integration

This integration used to be called HomeBrainz (`homebrainz`). After you update it in HACS and restart Home Assistant, your devices move to inkVision automatically. Entity IDs, history, dashboards and device IDs stay the same.

- Actions were renamed from `homebrainz.*` to `inkvision.*`. The old names keep working; when an automation or script uses one, a notice under **Settings** > **Repairs** tells you which action to change.
- HACS leaves the old `custom_components/homebrainz` folder behind. A repair notice offers to delete it; restart Home Assistant afterwards. Until then the old entries stay under HomeBrainz as disabled, so the old code cannot add the devices again; they are removed on the first start without the folder.

## Supported functionality

Entities marked *disabled by default* can be enabled on the device page: click the entity, open its settings and turn on **Enabled**.

### Sensors

Entity | Description
-- | --
**Temperature** | Room temperature in °C. The sensor sits inside the clock, so it reads a little above the room temperature while the display is bright.
**Humidity** | Relative humidity in %.
**Atmospheric pressure** | Air pressure in hPa.
**Indoor air quality** | The IAQ index of the BME680 sensor, from 0 (clean air) to 500 (heavily polluted air). 0–50 is excellent, 51–100 good, 101–150 lightly polluted, 151–200 moderately polluted, above 200 heavily polluted.
**IAQ rating** | The index above as a word: Excellent, Good, Fair, Poor and so on, as the clock shows it.
**Static IAQ** | The IAQ index without the short-term adaptation to the room, for comparing rooms or days.
**IAQ accuracy** | How well the air quality sensor is calibrated, from 0 (not calibrated, values unreliable) to 3 (fully calibrated). Diagnostic.
**CO₂ equivalent** | An estimate of the CO₂ concentration in ppm, calculated from the volatile organic compounds the sensor measures. It is not a CO₂ measurement.
**Breath VOC** | An estimate of the concentration of volatile organic compounds in ppm.
**Gas resistance** | The raw reading of the gas sensor in kΩ, which the air quality values are calculated from.
**Wi-Fi signal** | Signal strength of the clock's Wi-Fi connection in dBm. Diagnostic.
**Uptime** | Seconds since the clock last started. Diagnostic, disabled by default.
**Reported brightness** | The display brightness the clock reports. Diagnostic, disabled by default.
**IP address**, **MAC address** | The clock's network addresses. Diagnostic, disabled by default.
**Firmware version**, **Firmware ID**, **Latest firmware version**, **Latest firmware ID** | Firmware details; the **Firmware** update entity shows the same in one place. Diagnostic, disabled by default.

After the clock starts, the air quality sensor calibrates itself, which takes a few hours. While **IAQ accuracy** is below 3, the air quality values are less reliable.

### Controls

Entity | Platform | Description
-- | -- | --
**Display brightness** | number | Brightness of the LED display, 0–15. 0 turns the display off.
**Clock screen**, **Temperature screen**, **Humidity screen**, **Pressure screen**, **Gas screen**, **Air quality screen** | switch | Whether the screen is in the display rotation. At least one screen stays on.
**Custom message** | text | Shows a message of up to 96 characters on the display. Longer text scrolls. The clock returns to its rotation after a while. Unavailable on firmware that cannot show messages.
**Timezone** | select | The timezone the clock shows the time in, from a list of common timezones.
**Button 1 tap action**, **Button 1 long press action**, **Button 2 tap action**, **Button 2 long press action** | select | What the clock itself does on the gesture: brightness up or down, next or previous screen, screen on/off, or **None (Home Assistant only)**. Configuration entities.
**Restart** | button | Restarts the clock. Configuration entity.
**Speaker** | media_player | Playback, volume and mute of the clock's speaker. See [Speaker](#speaker).
**Firmware** | update | Shows whether a firmware update is available, with its release notes, and installs it. The clock downloads the firmware, flashes it and restarts.

### Events

Entity | Description
-- | --
**Button 1**, **Button 2** | Fires on every `single_tap` and `long_press` of the touch button. The event's `action` attribute tells you what the clock did itself. Unavailable on firmware that does not report button presses.

### Actions

Action | Description
-- | --
`inkvision.set_screen_rotation` | Sets which screens the display rotates through, all at once. `screens` is a list of `clock`, `temp`, `humidity`, `pressure`, `gas` and `iaq`; the clock always shows them in that order.
`inkvision.set_custom_screen` | Sets the text of one of the 4 custom screens (`slot` 1–4); an empty `text` removes the screen.

Both target a device (`device_id`) and can be tried out under **Developer Tools** > **Actions**.

These actions were removed because an entity does the same. Change automations that use them:

Removed action | Use instead
-- | --
`inkvision.set_brightness` | `number.set_value` on the **Display brightness** entity
`inkvision.display_text` | `text.set_value` on the **Custom message** entity
`inkvision.restart_device` | `button.press` on the **Restart** entity
`inkvision.set_timezone` | `select.select_option` on the **Timezone** entity

The **Firmware update available** binary sensor and the **Check firmware update** / **Install firmware update** buttons are replaced by the **Firmware** update entity. Devices that already have them keep them for now; if an automation or script uses one, a notice under **Settings** > **Repairs** lists it.

### Speaker

The **Speaker** media player plays text-to-speech, internet radio, and media from your Home Assistant media library (**Media** in the sidebar, or the `media_player.play_media` action). You can select it as the output of an Assist voice pipeline for announcements.

The clock plays plain HTTP audio only. Home Assistant converts everything it sends with ffmpeg, so the source can be any format or stream, including `.pls` and `.m3u` radio playlists, and the clock fetches the converted audio from Home Assistant. For that, the clock must be able to reach the **Internal URL** of your Home Assistant (**Settings** > **System** > **Network**) over HTTP.

## Data updates

The clock pushes its sensor readings and status changes to Home Assistant over a WebSocket connection every few seconds, so the entities update as soon as a value changes. Button presses arrive the same way.

Every 5 minutes, Home Assistant also polls the clock over HTTP for what the WebSocket does not push: the firmware update check, the timezone and the screen rotation. If the WebSocket connection drops, Home Assistant reconnects within 10 to 30 seconds and polls right away, so the entities become unavailable within a minute when the clock is unplugged.

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

Each touch button has an event entity that you can use as an automation trigger. If you want a button to do nothing on the clock itself, set its action to **None (Home Assistant only)** with the button action selects on the device page or in the HomeBrainz Hub app.

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

### Show the outdoor temperature on the clock

Set **Custom screen 1** in the integration options to:

```jinja
Out {{ states('sensor.outdoor_temperature') | round(0) }}°C
```

The screen joins the rotation and updates whenever the outdoor temperature changes. From an automation, use the action instead:

```yaml
action: inkvision.set_custom_screen
target:
  device_id: 0123456789abcdef0123456789abcdef
data:
  slot: 2
  text: "Washing done"
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
          value: "{{ brightness }}"
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
- The clock has no authentication on its local API, so anyone on your network can control it. Keep the clock on a trusted network.
- The CO₂ equivalent and breath VOC values are estimates that the BME680 derives from its gas sensor; they are not measurements of CO₂ or of a specific compound. The sensor recalibrates after every restart, so **IAQ accuracy** drops to 0 and the values are unreliable for a few hours.
- The speaker plays plain HTTP only. If Home Assistant is only reachable over HTTPS, or the clock cannot reach the internal URL of Home Assistant, media does not play. Pausing stops playback; the clock cannot resume from where it stopped.
- The clock does not store custom screens or the custom message. Home Assistant sends the custom screens again whenever it reconnects, and clears them when the integration is unloaded or the clock is removed.
- The **Timezone** select offers a fixed list of common timezones. Other timezones can be set in the HomeBrainz Hub app.
- The custom message and button events need recent firmware; on older firmware those entities are unavailable until you install the update.
- Each clock is one config entry; you cannot add the same clock twice.

## Troubleshooting

### The clock is not discovered

1. Make sure the clock is powered on and connected to Wi-Fi: its display shows the time.
2. Check that Home Assistant and the clock are on the same network and subnet. Discovery uses mDNS, which does not cross VLANs or subnets unless your router forwards it.
3. Add the clock by its IP address instead, see [Adding a device](#adding-a-device).

### The clock cannot be added

- **"Failed to connect to the device"**: Home Assistant cannot reach the address. Check the IP address in your router, make sure no firewall blocks Home Assistant from reaching the clock on port 80, and open `http://<address>/` in a browser; the clock's own settings page should appear.
- **"The device at this address does not appear to be an inkVision device"**: another device answers at that address. Check the IP address.
- **"Device is already configured"**: the clock is already set up; Home Assistant has updated its address.
- **"The device at this address is a different inkVision device"** (when reconfiguring): the address belongs to another clock. Each clock has its own entry.

### Entities are unavailable

The clock is not reachable. Check that it is powered and connected, and that its IP address has not changed (the **Reconfigure** option of the entry lets you set a new address, and rediscovery updates it as well). If the clock is on, open **Settings** > **Devices & services** > **inkVision**, click the three dots and choose **Reload**.

### Values do not update

If the sensor values stay the same for minutes, the WebSocket connection has dropped and Home Assistant is reconnecting. Reconnection is automatic. If it never succeeds, restart the clock from the **Restart** button or by unplugging it.

### The speaker does not play

1. Check that the volume is not muted or very low, on the **Speaker** entity.
2. Open **Settings** > **System** > **Network** and check the **Internal URL**. It must start with `http://` and use an address the clock can reach, such as `http://192.168.1.10:8123`. If Home Assistant only has an HTTPS URL, playback fails.
3. Make sure the clock's firmware is up to date (the **Firmware** entity).
4. Enable debug logging (below) and play something; the log shows the URL sent to the clock and any ffmpeg error.

### Buttons do nothing in Home Assistant

The event entities need firmware that reports button presses. Install the update from the **Firmware** entity. If the clock itself reacts to the button (for example changes brightness) but you only want the Home Assistant automation, set the button's action to **None (Home Assistant only)**.

### Some entities are missing

Entities are created for the hardware and firmware the clock has. Update the firmware and reload the integration. Diagnostic entities such as the IP address and uptime are disabled by default; enable them on the device page.

### Debug logging

To see what the integration sends and receives, go to **Settings** > **Devices & services** > **inkVision**, click the three dots and choose **Enable debug logging**. Reproduce the problem, then disable debug logging the same way; Home Assistant downloads the log. Alternatively, add this to `configuration.yaml` and restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.inkvision: debug
    pyinkvision: debug
```

You can also download diagnostics from the device page (three dots > **Download diagnostics**). Attach both to a bug report.

## Removing the integration

Go to **Settings** > **Devices & services** > **inkVision**, click the three dots next to the entry and choose **Delete**. The clock keeps working on its own and keeps its settings; only its custom screens are cleared.

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
[my-config]: https://my.home-assistant.io/redirect/config_flow_start/?domain=inkvision
[my-config-badge]: https://my.home-assistant.io/badges/config_flow_start.svg

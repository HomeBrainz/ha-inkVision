## HomeBrainz Clock Integration

<p align="center">
  <img src="icon.svg" alt="HomeBrainz Logo" width="150">
</p>

Connect your HomeBrainz device to Home Assistant for local monitoring and control.

### Features

- **WebSocket-first updates** with HTTP fallback polling
- **Easy setup** through Home Assistant UI (auto-discovery + manual host fallback)
- **Multi-platform support**: sensors, controls, firmware actions, and media player
- **Device management** with direct link to the device web interface

### Supported Sensors

- **BME680 (+ BSEC2)**: IAQ, CO2 equivalent, breath VOC, gas resistance, temperature, humidity, pressure
- **Legacy fallback**: AHT20 and BMP280 endpoints
- **WiFi**: Signal strength monitoring

### Quick Setup

1. Ensure your HomeBrainz Clock device is connected to your WiFi network
2. Note the device's IP address
3. Add the integration through **Settings** > **Devices & Services** > **Add Integration**
4. Search for "HomeBrainz" and enter your device IP address

The integration will automatically discover all available sensors and create entities in Home Assistant.

### Device Web Interface

Access your device's configuration page directly through the device information panel in Home Assistant, or browse to `http://[device-ip]/` for:

- Real-time sensor readings
- WiFi configuration
- MQTT settings
- Device diagnostics

### Troubleshooting

If you experience connection issues:
- Verify the device IP address
- Ensure Home Assistant and the device are on the same network
- Check that the device's web interface is accessible
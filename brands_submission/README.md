# HomeBrainz Brands Submission

This directory contains the files needed to submit the HomeBrainz logo to the Home Assistant brands repository.

## Files Structure

```
custom_integrations/
├── homebrainz.json          # Brand manifest
└── homebrainz/
    ├── icon.png             # 512x512 main icon
    ├── icon@2x.png          # 1024x1024 high-res icon
    └── logo.png             # 512x512 logo
```

## Submission Steps

1. **Fork the Home Assistant brands repository**:
   ```
   https://github.com/home-assistant/brands
   ```

2. **Clone your fork locally**:
   ```bash
   git clone https://github.com/11ado33/brands.git
   cd brands
   ```

3. **Copy the files to the correct location**:
   ```bash
   # Copy the manifest file
   cp /path/to/this/custom_integrations/homebrainz.json custom_integrations/

   # Copy the logo directory
   cp -r /path/to/this/custom_integrations/homebrainz custom_integrations/
   ```

4. **Create a branch for your submission**:
   ```bash
   git checkout -b add-homebrainz-brand
   git add custom_integrations/homebrainz.json
   git add custom_integrations/homebrainz/
   git commit -m "Add HomeBrainz custom integration brand

   - Add HomeBrainz Clock integration logos
   - 512x512 and 1024x1024 PNG formats
   - Clean brain and house icon design
   - Domain: homebrainz"
   ```

5. **Push and create Pull Request**:
   ```bash
   git push origin add-homebrainz-brand
   ```
   Then create a PR on GitHub from your fork to the main brands repository.

## Brand Guidelines Compliance

✅ **Logo Format**: PNG format as required  
✅ **Logo Sizes**: 512x512 and 1024x1024 pixels  
✅ **File Names**: icon.png, icon@2x.png, logo.png  
✅ **Manifest**: Proper JSON structure with domain mapping  
✅ **Quality**: High-quality, clear logo design  

## Notes

- The HomeBrainz integration is a custom integration for HomeBrainz Clock devices
- Logo features brain and house icons representing the brand
- Colors: Green (#68D065) and white
- Professional design suitable for Home Assistant UI

## PR Description Template

```
Add HomeBrainz custom integration brand

This PR adds branding for the HomeBrainz custom integration:

- **Integration**: HomeBrainz Clock environmental monitoring
- **Domain**: homebrainz  
- **Repository**: https://github.com/HomeBrainz/ha-inkVision
- **Logo**: Clean brain and house icon design
- **Files**: icon.png (512x512), icon@2x.png (1024x1024), logo.png (512x512)

The HomeBrainz integration provides comprehensive environmental monitoring for HomeBrainz Clock devices with multiple sensors (temperature, humidity, pressure, air quality, CO2, TVOC, WiFi signal).
```
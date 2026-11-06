# Home Assistant core submission material

These files are not used by the HACS integration. They go into separate pull requests
when inkVision is submitted to Home Assistant core:

- `home-assistant.io/inkvision.markdown`: the full integration page, for
  [home-assistant/home-assistant.io](https://github.com/home-assistant/home-assistant.io)
  at `source/_integrations/inkvision.markdown` (against the `next` branch). The first core
  pull request only has the sensor platform, so its page is a subset of this one; with each
  follow-up platform pull request, copy the matching sections from here. Keep it in sync with
  README.md, which documents the HACS version.
- `brands/core_integrations/inkvision/`: the icons, for
  [home-assistant/brands](https://github.com/home-assistant/brands) at the same path. They are
  copies of `custom_components/inkvision/brand/`; keep the two in sync.

The device protocol is in the [pyinkvision](https://github.com/HomeBrainz/pyinkvision) library.

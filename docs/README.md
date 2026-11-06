# Home Assistant core submission material

These files are not used by the HACS integration. They go into separate pull requests
when inkVision is submitted to Home Assistant core:

- `home-assistant.io/inkvision.markdown`: the integration page, for
  [home-assistant/home-assistant.io](https://github.com/home-assistant/home-assistant.io)
  at `source/_integrations/inkvision.markdown` (against the `next` branch). Set
  `ha_release` when the core pull request is merged.
- `brands/core_integrations/inkvision/`: the icons, for
  [home-assistant/brands](https://github.com/home-assistant/brands) at the same path. They are
  copies of `custom_components/inkvision/brand/`; keep the two in sync.

The device protocol is in the [pyinkvision](https://github.com/HomeBrainz/pyinkvision) library.

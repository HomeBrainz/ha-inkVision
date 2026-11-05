"""Repair flows for inkVision.

HACS-only, like legacy_migration.py: delete before submitting to core.
"""
from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .legacy_migration import ISSUE_LEGACY_FOLDER, remove_legacy_folder


class LegacyFolderRepairFlow(ConfirmRepairFlow):
    """Delete custom_components/homebrainz, which HACS leaves behind after the rename."""

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Remove the folder once the user confirms."""
        if user_input is not None:
            await self.hass.async_add_executor_job(remove_legacy_folder, self.hass)
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Create the flow that fixes an issue."""
    if issue_id == ISSUE_LEGACY_FOLDER:
        return LegacyFolderRepairFlow()
    raise ValueError(f"Unknown issue {issue_id}")

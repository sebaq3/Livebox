"""Tests for setting up and unloading the tracker integration."""

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.livebox.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .helpers import MockRouterSession


async def test_setup_and_unload(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    router_session: MockRouterSession,
) -> None:
    """Set up the tracker and close its HTTP session when unloaded."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state == ConfigEntryState.LOADED
    assert config_entry.runtime_data.data["devices"]
    assert router_session.close_count == 0

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state == ConfigEntryState.NOT_LOADED
    assert router_session.close_count == 1


async def test_diagnostics_redact_client_identity(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    router_session: MockRouterSession,
) -> None:
    """Redact names, IP addresses, and MAC addresses from diagnostics."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    device = diagnostics["devices"][0]
    assert device["Key"] == "**REDACTED**"
    assert device["IPAddress"] == "**REDACTED**"
    assert device["Name"] == "**REDACTED**"

"""Diagnostics support for the Livebox device tracker."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

TO_REDACT = {
    "IPAddress",
    "Key",
    "MacAddress",
    "MACAddress",
    "Name",
    "PhysAddress",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted tracked-device data."""
    devices = entry.runtime_data.data.get("devices", {})
    return async_redact_data({"devices": list(devices.values())}, TO_REDACT)

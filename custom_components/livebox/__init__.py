"""Orange Livebox device tracker integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .coordinator import LiveboxDataUpdateCoordinator

type LiveboxConfigEntry = ConfigEntry[LiveboxDataUpdateCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LiveboxConfigEntry) -> bool:
    """Set up the Livebox device tracker."""
    coordinator = LiveboxDataUpdateCoordinator(
        hass,
        entry.data[CONF_HOST],
        entry.data[CONF_PASSWORD],
        port=entry.data[CONF_PORT],
        unique_id=entry.unique_id,
        config_entry=entry,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_logout()
        raise

    if entry.unique_id is None and coordinator.unique_id:
        hass.config_entries.async_update_entry(entry, unique_id=coordinator.unique_id)

    entry.runtime_data = coordinator

    async def async_logout_on_stop(event: Event) -> None:
        """Close the router session when Home Assistant stops."""
        await coordinator.async_logout()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_logout_on_stop)
    )

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **coordinator.device_info
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LiveboxConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_logout()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: LiveboxConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing tracked clients but not the router."""
    gateway = config_entry.runtime_data.get_parent_device_identifier(None)
    return gateway not in device_entry.identifiers

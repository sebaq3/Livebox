"""Support for the Livebox platform."""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, cast

from homeassistant.components.device_tracker.const import SourceType
from homeassistant.components.device_tracker.entity import ScannerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LiveboxConfigEntry
from .const import CONF_TRACKING_TIMEOUT, DEFAULT_TRACKING_TIMEOUT, DOMAIN
from .coordinator import LiveboxDataUpdateCoordinator
from .entity import LiveboxEntity

_LOGGER = logging.getLogger(__name__)

# Left side = Home Assistant attribute names, right side = raw Livebox keys.
_BASE_DEVICE_ATTRIBUTE_FIELDS: dict[str, str] = {
    "interface_name": "InterfaceName",
    "type": "DeviceType",
    "vendor": "VendorClassID",
    "manufacturer": "Manufacturer",
    "first_seen": "FirstSeen",
    "last_connection": "LastConnection",
    "last_changed": "LastChanged",
}

# Left side = Home Assistant attribute names, right side = raw Livebox keys.
_WIRELESS_DEVICE_ATTRIBUTE_FIELDS: dict[str, str] = {
    "frequency_band": "OperatingFrequencyBand",
}


_WIRED_INTERFACE_RE = re.compile(r"eth\d+", re.IGNORECASE)


def _is_wired_device(device: dict[str, Any]) -> bool:
    """Return whether a Livebox device looks like an Ethernet client."""
    interface_name = device.get("InterfaceName", "")
    tags = device.get("Tags", "")
    return (
        isinstance(interface_name, str)
        and _WIRED_INTERFACE_RE.fullmatch(interface_name) is not None
    ) or (isinstance(tags, str) and "eth" in tags.split())


def _is_wireless_device(device: dict[str, Any]) -> bool:
    """Return whether a Livebox device looks like a Wi-Fi client."""
    interface_name = device.get("InterfaceName", "")
    tags = device.get("Tags", "")
    return (
        isinstance(interface_name, str)
        and interface_name.startswith(("vap", "wlan", "wl"))
    ) or (isinstance(tags, str) and "wifi" in tags.split())


def _copy_selected_fields(
    source: dict[str, Any], fields: dict[str, str]
) -> dict[str, Any]:
    """Return non-empty values from a raw Livebox payload using normalized names."""
    return {
        attr: value
        for attr, key in fields.items()
        if (value := source.get(key)) is not None and value != ""
    }


def _get_signal_quality(signal_strength: Any) -> str:
    """Return a human-readable signal quality from the raw signal strength."""
    if not isinstance(signal_strength, (int, float)):
        return "unknown"

    match signal_strength * -1:
        case x if x > 90:
            return "very bad"
        case x if 80 <= x < 90:
            return "bad"
        case x if 70 <= x < 80:
            return "very low"
        case x if 67 <= x < 70:
            return "low"
        case x if 60 <= x < 67:
            return "good"
        case x if 50 <= x < 60:
            return "very good"
        case x if 30 <= x < 50:
            return "excellent"
        case _:
            return "unknown"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LiveboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker from config entry."""
    coordinator = entry.runtime_data
    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if (
            entity_entry.domain == "device_tracker"
            and entity_entry.platform == DOMAIN
        ):
            tracked_key = entity_entry.unique_id.removeprefix(
                f"{coordinator.unique_id}_"
            )
            object_id = re.sub(r"[^a-z0-9]+", "_", tracked_key.lower()).strip("_")
            new_entity_id = f"device_tracker.livebox_{object_id}"
            updates = {}
            existing_entry = entity_registry.async_get(new_entity_id)
            if (
                new_entity_id != entity_entry.entity_id
                and existing_entry is None
            ):
                updates["new_entity_id"] = new_entity_id
            if entity_entry.disabled_by is not None:
                updates["disabled_by"] = None
            if updates:
                entity_registry.async_update_entity(entity_entry.entity_id, **updates)

    tracked = set()

    @callback
    def async_update_router() -> None:
        """Update the values of the router."""
        async_add_new_tracked_entities(coordinator, async_add_entities, tracked)

    entry.async_on_unload(coordinator.async_add_listener(async_update_router))

    async_update_router()


@callback
def async_add_new_tracked_entities(
    coordinator: LiveboxDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    tracked: set[str],
) -> None:
    """Add new tracker entities from the router."""
    repeater_entities = []
    client_entities = []
    repeater_keys = coordinator.data.get("topology_repeaters", {})

    _LOGGER.debug("Adding device trackers entities")
    for mac, device in coordinator.data.get("devices", {}).items():
        if mac in tracked:
            continue
        _LOGGER.debug("New device tracker: %s", device.get("Name", "Unknown"))
        entity = LiveboxDeviceScannerEntity(
            coordinator,
            EntityDescription(
                key=f"{mac}_tracker",
                name="Tracking",
            ),
            device,
        )
        if mac in repeater_keys:
            repeater_entities.append(entity)
        else:
            client_entities.append(entity)
        tracked.add(mac)

    async_add_entities(repeater_entities + client_entities)


@callback
class LiveboxDeviceScannerEntity(  # pyrefly: ignore[inconsistent-inheritance]
    LiveboxEntity, ScannerEntity
):
    """Represent a tracked device."""

    _attr_name = None
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: LiveboxDataUpdateCoordinator,
        description: EntityDescription,
        device: dict[str, Any],
    ) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator, description)
        self._device = device
        self._device_key = cast(str | None, device.get("Key"))
        self._old_status = datetime.today()
        self._attr_is_connected = device.get("Active", False)
        self._attr_source_type = SourceType.ROUTER
        self._attr_mac_address = self._device_key
        self._attr_ip_address = device.get("IPAddress")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the device state attributes."""
        attrs = _copy_selected_fields(self._device, _BASE_DEVICE_ATTRIBUTE_FIELDS)

        if _is_wired_device(self._device):
            attrs.update({"connection": "ethernet", "frequency_band": "Wired"})

        if _is_wireless_device(self._device):
            iname = self._device.get("InterfaceName")
            signal_quality = _get_signal_quality(self._device.get("SignalStrength"))
            attrs.update(
                {
                    "frequency_band": self._device.get("OperatingFrequencyBand"),
                    "signal_quality": signal_quality,
                    "connection": "wifi"
                    if iname not in ["wlguest2", "wlguest5"]
                    else "guestwifi",
                }
            )
        return attrs

    @property
    def icon(self) -> str:
        """Return icon."""
        match self._device.get("DeviceType"):
            case "Computer" | "Desktop iOS" | "Desktop Windows" | "Desktop Linux":
                return "mdi:desktop-tower-monitor"
            case "Laptop" | "Laptop iOS" | "Laptop Windows" | "Laptop Linux":
                return "mdi:laptop"
            case "Switch4" | "Switch8" | "Switch":
                return "mdi:switch"
            case "Access Point":
                return "mdi:access-point-network"
            case "TV" | "TVKey" | "Apple TV":
                return "mdi:television"
            case "HomePlug":
                return "mdi:network"
            case "Printer":
                return "mdi:printer"
            case "Set-top Box TV UHD" | "Set-top Box":
                return "mdi:dlna"
            case "Mobile iOS" | "Mobile" | "Mobile Android":
                return "mdi:cellphone"
            case "Tablet iOS" | "Tablet" | "Tablet Android" | "Tablet Windows":
                return "mdi:cellphone"
            case "Game Console":
                return "mdi:gamepad-square"
            case "Homepoint":
                return "mdi:home-automation"
            case "Nas":
                return "mdi:nas"
            case _:
                return "mdi:devices"

    @property
    def is_connected(self) -> bool:
        """Return true if the device is connected to the network via router."""
        timeout_tracking = self.coordinator.config_entry.options.get(
            CONF_TRACKING_TIMEOUT, DEFAULT_TRACKING_TIMEOUT
        )
        status = self._device.get("Active", False)
        if status is True:
            self._old_status = datetime.today() + timedelta(seconds=timeout_tracking)
        if status is False and self._old_status > datetime.today():
            _LOGGER.debug("%s will be disconnected at %s", self.name, self._old_status)
            return True
        _LOGGER.debug("Is Connected: %s", status)
        return status

    @property
    def device_info(self) -> DeviceInfo | None:  # pyrefly: ignore
        """Return device info to link entity to the Livebox device."""
        if isinstance(self._device_key, str):
            device_identifier = self._device_key
        elif isinstance(self.name, str):
            device_identifier = self.name
        else:
            device_identifier = DOMAIN
        return DeviceInfo(
            name=f"Livebox {self._device.get('Name') or 'Unknown'}",
            identifiers={(DOMAIN, device_identifier)},
            via_device_id=self.coordinator.get_parent_device_id(self._device_key),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Respond to a DataUpdateCoordinator update."""
        self._device = self.coordinator.data.get("devices", {}).get(
            self._device_key, {}
        )
        self._attr_ip_address = self._device.get("IPAddress")
        self._async_update_via_device(self._device_key)
        self.async_write_ha_state()

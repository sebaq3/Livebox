"""Tests for Arcadyan device tracking."""

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.livebox.const import CONF_TRACKING_TIMEOUT, DOMAIN
from custom_components.livebox.device_tracker import (
    _BASE_DEVICE_ATTRIBUTE_FIELDS,
    _copy_selected_fields,
    _get_signal_quality,
    _is_wired_device,
    _is_wireless_device,
)

from .helpers import MockRouterSession


@pytest.mark.parametrize(
    ("strength", "expected"),
    [
        (-95, "very bad"),
        (-85, "bad"),
        (-75, "very low"),
        (-68, "low"),
        (-63, "good"),
        (-55, "very good"),
        (-40, "excellent"),
        (-20, "unknown"),
        (None, "unknown"),
    ],
)
def test_signal_quality(strength: float | None, expected: str) -> None:
    """Map router signal strength to a readable quality."""
    assert _get_signal_quality(strength) == expected


def test_device_connection_types() -> None:
    """Recognize Ethernet and Wi-Fi clients from their interface or tags."""
    assert _is_wired_device({"InterfaceName": "eth2"})
    assert _is_wired_device({"Tags": "lan eth"})
    assert not _is_wired_device({"InterfaceName": "vap5g0"})
    assert _is_wireless_device({"InterfaceName": "vap5g0"})
    assert _is_wireless_device({"Tags": "lan wifi"})
    assert not _is_wireless_device({"InterfaceName": "eth2"})


def test_copy_selected_device_attributes() -> None:
    """Normalize useful, non-empty client fields for entity attributes."""
    assert _copy_selected_fields(
        {"InterfaceName": "eth1", "DeviceType": "", "Manufacturer": "Orange"},
        _BASE_DEVICE_ATTRIBUTE_FIELDS,
    ) == {"interface_name": "eth1", "manufacturer": "Orange"}


def _entity_id(hass: HomeAssistant, config_entry: ConfigEntry, unique_id: str) -> str:
    """Resolve the tracker entity ID from the entity registry."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("device_tracker", DOMAIN, unique_id)
    assert entity_id is not None, [
        entry.unique_id
        for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id)
    ]
    return entity_id


async def test_tracks_router_clients(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    router_session: MockRouterSession,
) -> None:
    """Expose clients parsed from the router's online-client CGI."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _entity_id(
        hass,
        config_entry,
        "AA:BB:CC:DD:EE:01",
    )
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == STATE_HOME
    assert state.attributes["ip"] == "192.168.1.20"
    assert router_session.close_count == 0


async def test_adds_and_marks_devices_away(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    router_session: MockRouterSession,
) -> None:
    """Add clients discovered later and update their presence on refresh."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_TRACKING_TIMEOUT: 0}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    second_mac = "AA:BB:CC:DD:EE:02"
    router_session.client_js = MockRouterSession.client_response(
        [
            ("Test phone", "192.168.1.20", "AA:BB:CC:DD:EE:01", "Online"),
            ("New tablet", "192.168.1.21", second_mac, "Online"),
        ]
    )
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    second_entity_id = _entity_id(hass, config_entry, second_mac)
    state = hass.states.get(second_entity_id)
    assert state is not None
    assert state.state == STATE_HOME

    router_session.client_js = MockRouterSession.client_response(
        [("New tablet", "192.168.1.21", second_mac, "Offline")]
    )
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(second_entity_id)
    assert state is not None
    assert state.state == STATE_NOT_HOME


async def test_keeps_device_home_during_disconnect_timeout(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    router_session: MockRouterSession,
) -> None:
    """Keep a recently disconnected client home until its timeout expires."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_TRACKING_TIMEOUT: 60}
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    router_session.client_js = MockRouterSession.client_response(
        [("Test phone", "192.168.1.20", "AA:BB:CC:DD:EE:01", "Offline")]
    )
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, config_entry, "AA:BB:CC:DD:EE:01")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_HOME

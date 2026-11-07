"""Tests for Arcadyan response parsing and session recovery."""

import base64

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.livebox.coordinator import (
    _decode_login_material,
    _encode_login_field,
    _parse_client_devices,
)

from .helpers import MockRouterSession


def test_decode_login_material() -> None:
    """Extract the router login key, IV, and token from its data image."""
    material = "a" * 32 + "b" * 16 + "test-token"
    encoded = base64.b64encode(material.encode()).decode()
    page = f'<img src="data:{"x" * 73}{encoded}">'

    assert _decode_login_material(page) == ("a" * 32, "b" * 16, "test-token")


def test_decode_login_material_rejects_missing_image() -> None:
    """Reject pages that do not contain router authentication material."""
    with pytest.raises(ValueError, match="material de autenticación"):
        _decode_login_material("<html></html>")


def test_encode_login_field() -> None:
    """Encode an ASCII password and reject unsupported non-ASCII characters."""
    encoded = _encode_login_field(
        "router-password",
        "0123456789abcdef0123456789abcdef",
        "0123456789abcdef",
    )
    assert encoded.startswith("%")

    with pytest.raises(ValueError, match="caracteres ASCII"):
        _encode_login_field(
            "contraseña",
            "0123456789abcdef0123456789abcdef",
            "0123456789abcdef",
        )


def test_parse_online_dhcp_and_configured_clients() -> None:
    """Merge online, DHCP, and configured client records by MAC address."""
    online_row = ["Tablet", "192.168.1.20", "AA:BB:CC:DD:EE:01"] + [""] * 14
    online_row[9] = "Online"
    response = (
        f"var online_client = {[online_row, ['short']]!r}; "
        "var dhcp_client = "
        "['Printer', '192.168.1.30', 'AA:BB:CC:DD:EE:02', '', '']; "
        'addCfg("client_mac3", "3", "AA:BB:CC:DD:EE:03"); '
        'addCfg("client_hostname3", "3", "Smart TV"); '
        'addCfg("client_deviceType3", "3", "TV");'
    )

    devices = _parse_client_devices(response)

    assert devices["AA:BB:CC:DD:EE:01"] == {
        "Key": "AA:BB:CC:DD:EE:01",
        "Name": "Tablet",
        "DeviceType": "",
        "Active": True,
        "IPAddress": "192.168.1.20",
    }
    assert devices["AA:BB:CC:DD:EE:02"]["Active"] is False
    assert devices["AA:BB:CC:DD:EE:02"]["Name"] == "Printer"
    assert devices["AA:BB:CC:DD:EE:03"]["Name"] == "Smart TV"
    assert devices["AA:BB:CC:DD:EE:03"]["DeviceType"] == "TV"


async def test_refresh_reauthenticates_after_login_page(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    router_session: MockRouterSession,
) -> None:
    """Renew the session when the CGI returns a login page."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    router_session.cgi_responses = [
        (200, "<script>window.location = 'login.htm'</script>"),
        (200, router_session.client_js),
    ]
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert router_session.login_count == 2
    assert config_entry.runtime_data.data["devices"]


async def test_setup_retries_when_client_cgi_fails(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    router_session: MockRouterSession,
) -> None:
    """Keep the entry retryable when the clients endpoint is unavailable."""
    router_session.cgi_status = 503

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state == ConfigEntryState.SETUP_RETRY
    assert router_session.close_count > 0

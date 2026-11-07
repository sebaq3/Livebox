"""Shared fixtures for the Livebox integration tests."""

from collections.abc import Iterator
from unittest.mock import PropertyMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.livebox.const import (
    CONF_TRACKING_TIMEOUT,
    DEFAULT_TRACKING_TIMEOUT,
    DOMAIN,
)

from .const import MOCK_USER_INPUT
from .helpers import MockRouterSession


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for hass."""
    yield


@pytest.fixture(autouse=True)
def enable_sockets(socket_enabled):
    """Allow sockets required by Home Assistant during setup."""
    yield


@pytest.fixture
def entity_registry_enabled_by_default() -> Iterator[None]:
    """Enable entities that are disabled by default."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture
def router_session() -> Iterator[MockRouterSession]:
    """Mock the Arcadyan HTTP API used by the coordinator."""
    session = MockRouterSession()
    with (
        patch(
            "custom_components.livebox.coordinator.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "custom_components.livebox.config_flow._decode_login_material",
            return_value=(
                "0123456789abcdef0123456789abcdef",
                "0123456789abcdef",
                "mock-token",
            ),
        ),
        patch(
            "custom_components.livebox.coordinator._decode_login_material",
            return_value=(
                "0123456789abcdef0123456789abcdef",
                "0123456789abcdef",
                "mock-token",
            ),
        ),
        patch(
            "custom_components.livebox.config_flow._encode_login_field",
            return_value="%00",
        ),
        patch(
            "custom_components.livebox.coordinator._encode_login_field",
            return_value="%00",
        ),
    ):
        yield session


@pytest.fixture
def config_entry(hass: HomeAssistant) -> ConfigEntry:
    """Create and register a config entry for the mocked Arcadyan router."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_USER,
        data=MOCK_USER_INPUT,
        unique_id="192.168.1.1",
        options={CONF_TRACKING_TIMEOUT: DEFAULT_TRACKING_TIMEOUT},
        title="Livebox Arcadyan (192.168.1.1)",
    )
    config_entry.add_to_hass(hass)
    return config_entry

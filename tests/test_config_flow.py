"""Tests for the Livebox config flow."""

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.livebox.const import CONF_TRACKING_TIMEOUT, DOMAIN

from .const import MOCK_USER_INPUT
from .helpers import MockRouterSession


async def test_form_success(
    hass: HomeAssistant, router_session: MockRouterSession
) -> None:
    """Create an entry after the router accepts the login and CGI request."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], MOCK_USER_INPUT
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Livebox Arcadyan (192.168.1.1)"
    assert result["data"] == MOCK_USER_INPUT
    assert result["result"].unique_id == "192.168.1.1"
    assert router_session.close_count >= 1


async def test_form_rejects_failed_login(
    hass: HomeAssistant, router_session: MockRouterSession
) -> None:
    """Keep the form open when the router rejects authentication."""
    router_session.login_success = False

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=MOCK_USER_INPUT,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "login_incorrect"}


async def test_form_rejects_empty_host(hass: HomeAssistant) -> None:
    """Reject incomplete imported or manually submitted configuration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={**MOCK_USER_INPUT, "host": ""},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_import_flow(
    hass: HomeAssistant, router_session: MockRouterSession
) -> None:
    """Import a legacy host/password/port configuration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IMPORT},
        data=MOCK_USER_INPUT,
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == MOCK_USER_INPUT
    assert router_session.close_count >= 1


async def test_form_already_configured(
    hass: HomeAssistant, router_session: MockRouterSession
) -> None:
    """Abort if the host is already configured."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.1",
        data=MOCK_USER_INPUT,
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], MOCK_USER_INPUT
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert router_session.close_count >= 1


async def test_reconfigure_updates_entry(
    hass: HomeAssistant, router_session: MockRouterSession
) -> None:
    """Allow the password and port of an existing router to be changed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.1",
        data=MOCK_USER_INPUT,
    )
    entry.add_to_hass(hass)
    updated_data = {**MOCK_USER_INPUT, "password": "new-password"}

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], updated_data
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == updated_data
    assert router_session.close_count >= 1


async def test_reconfigure_rejects_failed_login(
    hass: HomeAssistant, router_session: MockRouterSession
) -> None:
    """Keep the reconfigure form open when credentials are rejected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.1",
        data=MOCK_USER_INPUT,
    )
    entry.add_to_hass(hass)
    router_session.login_success = False

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], MOCK_USER_INPUT
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "login_incorrect"}


async def test_options_only_configure_tracking_timeout(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Expose only the supported tracker timeout option."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    schema_keys = result["data_schema"].schema
    assert len(schema_keys) == 1
    assert next(iter(schema_keys)).schema == CONF_TRACKING_TIMEOUT


async def test_options_update_tracking_timeout(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Save the configured device-disconnect timeout."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TRACKING_TIMEOUT: 120}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_TRACKING_TIMEOUT: 120}

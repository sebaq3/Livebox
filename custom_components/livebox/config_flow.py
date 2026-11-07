"""Config flow to configure an Arcadyan Livebox."""

import logging
from collections.abc import Mapping
from time import time
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from yarl import URL

from .const import (
    CONF_TRACKING_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TRACKING_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .coordinator import _decode_login_material, _encode_login_field

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
    }
)

_LOGGER = logging.getLogger(__name__)


class LiveboxFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Livebox config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """Get the options flow."""
        return LiveboxOptionsFlowHandler()

    async def _async_validate_input(
        self, user_input: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Verify connectivity and credentials against the router."""
        host = user_input.get(CONF_HOST)
        password = user_input.get(CONF_PASSWORD)
        if not isinstance(host, str) or not host.strip() or not password:
            return None, {"base": "cannot_connect"}

        host = host.strip()
        data = {
            CONF_HOST: host,
            CONF_PASSWORD: password,
            CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
        }
        try:
            base_url = f"http://{host}:{data[CONF_PORT]}"
            login_url = f"{base_url}/login.htm"
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True)
            ) as session:
                session.cookie_jar.update_cookies(
                    {"lang": "sp", "display_err_msg": "yes", "menu_sel": "0"},
                    response_url=URL(base_url),
                )
                async with session.get(login_url, timeout=10) as response:
                    if response.status != 200:
                        return None, {"base": "cannot_connect"}
                    key, iv, token = _decode_login_material(await response.text())

                payload = {
                    "httoken": token,
                    "cnlvd2xh": _encode_login_field(password, key, iv),
                    "dfr597hf": _encode_login_field(DEFAULT_USERNAME, key, iv),
                    "login": "acceso",
                }
                async with session.post(
                    f"{base_url}/login.cgi",
                    data=payload,
                    headers={"Referer": login_url},
                    allow_redirects=False,
                    timeout=10,
                ) as response:
                    has_sid = any(
                        cookie.key.upper() == "SID" for cookie in session.cookie_jar
                    )
                    if response.status != 302 or not has_sid:
                        return None, {"base": "login_incorrect"}

                async with session.get(
                    f"{base_url}/network_connected.htm",
                    headers={"Referer": f"{base_url}/index.htm"},
                    timeout=10,
                ) as response:
                    if response.status != 200:
                        return None, {"base": "cannot_connect"}
                    _, _, token = _decode_login_material(await response.text())

                timestamp = int(time() * 1000)
                async with session.get(
                    f"{base_url}/cgi/cgi_clients.js",
                    params={"_tn": token, "_t": timestamp, "_": timestamp},
                    timeout=10,
                ) as response:
                    if response.status != 200:
                        return None, {"base": "cannot_connect"}
                    if "login" in (await response.text()).lower():
                        return None, {"base": "login_incorrect"}
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Unable to validate Livebox connection: %s", err)
            return None, {"base": "cannot_connect"}

        return data, {}

    def _get_data_schema(
        self, suggested_values: Mapping[str, Any] | None = None
    ) -> vol.Schema:
        """Build the config form schema."""
        schema = DATA_SCHEMA
        if suggested_values:
            schema = self.add_suggested_values_to_schema(schema, suggested_values)
        return schema

    async def async_step_import(
        self, import_config: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Import a config entry."""
        return await self.async_step_user(import_config)

    async def async_step_user(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await self._async_validate_input(user_input)
            if data:
                await self.async_set_unique_id(data[CONF_HOST].lower().rstrip("."))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Livebox Arcadyan ({data[CONF_HOST]})",
                    data=data,
                )

        return self.async_show_form(
            step_id="user", data_schema=self._get_data_schema(), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a reconfiguration flow."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data, errors = await self._async_validate_input(user_input)
            if data:
                await self.async_set_unique_id(data[CONF_HOST].lower().rstrip("."))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._get_data_schema(entry.data),
            errors=errors,
        )


class LiveboxOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Handle tracking options."""

    async def async_step_init(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the device tracker timeout."""
        if user_input:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_TRACKING_TIMEOUT,
                            default=DEFAULT_TRACKING_TIMEOUT,
                        ): vol.All(int, vol.Range(min=0)),
                    }
                ),
                self.config_entry.options,
            ),
        )

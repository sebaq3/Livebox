import ast
import base64
import hashlib
import logging
import re
import secrets
import string
import time
from datetime import timedelta
from html.parser import HTMLParser

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from yarl import URL

from .const import DEFAULT_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)
_CLIENT_FIELD_RE = re.compile(r"^client_(hostname|mac|deviceType)(\d+)$", re.IGNORECASE)


class _DataImageParser(HTMLParser):
    """Collect data-image sources used by the router to carry login tokens."""

    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            source = dict(attrs).get("src", "")
            if source.startswith("data:"):
                self.sources.append(source)


def _decode_login_material(page):
    """Extract the AES key, IV and httoken from the router's data image."""
    parser = _DataImageParser()
    parser.feed(page)
    for source in parser.sources:
        if len(source) <= 78:
            continue
        encoded = re.sub(r"[^A-Za-z0-9+/=]", "", source[78:])
        try:
            decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
                "utf-8"
            )
        except ValueError, UnicodeDecodeError:
            continue
        if len(decoded) >= 49:
            return decoded[:32], decoded[32:48], decoded[48:]
    raise ValueError("El router no incluyó el material de autenticación")


def _encode_login_field(value, key, iv):
    """Encode a login field using the router's AES-CBC form protocol."""
    if any(ord(char) < 21 or ord(char) > 122 for char in value):
        raise ValueError("El router solo admite caracteres ASCII en el login")
    encoded_value = base64.b64encode(value.encode("latin-1")).decode("ascii")
    digest = hashlib.md5(encoded_value.encode("ascii")).hexdigest()
    nonce = "".join(
        secrets.choice(string.digits + string.ascii_lowercase) for _ in range(32)
    )
    plaintext = base64.b64encode(f"{nonce} {digest}".encode("ascii"))
    plaintext += b"\x00" * (-len(plaintext) % 16)
    encryptor = Cipher(
        algorithms.AES(key.encode("latin-1")), modes.CBC(iv.encode("latin-1"))
    ).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    return "".join(f"%{byte:02x}" for byte in ciphertext)


def _parse_client_devices(response_text):
    """Build tracker records from the router's client, DHCP, and online arrays."""
    configured_clients = {}
    for property_name, _property_id, property_value in re.findall(
        r'addCfg\("([^"]+)",\s*"([^"]*)",\s*"([^"]*)"\);',
        response_text,
    ):
        match = _CLIENT_FIELD_RE.fullmatch(property_name)
        if match:
            field_name, index = match.groups()
            configured_clients.setdefault(index, {})[field_name.lower()] = (
                property_value.strip()
            )

    def extract_array(variable_name):
        assignment = re.search(
            rf"\bvar\s+{re.escape(variable_name)}\s*=\s*\[", response_text
        )
        if assignment is None:
            return []

        start = assignment.end() - 1
        depth = 0
        quote = None
        escaped = False
        for index in range(start, len(response_text)):
            character = response_text[index]
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in ("'", '"'):
                quote = character
            elif character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    try:
                        value = ast.literal_eval(response_text[start : index + 1])
                    except SyntaxError, ValueError:
                        return []
                    return value if isinstance(value, list) else []
        return []

    devices = {}

    def get_device(mac_address):
        if not isinstance(mac_address, str):
            return None
        mac_address = mac_address.strip().upper()
        if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac_address):
            return None
        return devices.setdefault(
            mac_address,
            {
                "Key": mac_address,
                "Name": mac_address,
                "DeviceType": "",
                "Active": False,
                "IPAddress": None,
            },
        )

    def update_device(device, name=None, ip_address=None, device_type=None):
        if name and name.strip() not in ("--", "unknown"):
            device["Name"] = name.strip()
        if ip_address and ip_address.strip():
            device["IPAddress"] = ip_address.strip()
        if device_type and device_type.strip():
            device["DeviceType"] = device_type.strip()

    for row in extract_array("online_client"):
        if not isinstance(row, (list, tuple)) or len(row) <= 16:
            continue
        device = get_device(row[2])
        if device is None:
            continue
        update_device(device, name=row[0], ip_address=row[1])
        device["Active"] = device["Active"] or row[9] == "Online"

    dhcp_clients = extract_array("dhcp_client")
    if isinstance(dhcp_clients, list):
        for offset in range(0, len(dhcp_clients) - 4, 5):
            name, ip_address, mac_address, _, _ = dhcp_clients[offset : offset + 5]
            device = get_device(mac_address)
            if device is not None:
                update_device(device, name=name, ip_address=ip_address)

    for client in configured_clients.values():
        device = get_device(client.get("mac"))
        if device is not None:
            update_device(
                device,
                name=client.get("hostname"),
                device_type=client.get("devicetype"),
            )
    return devices


class LiveboxDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(
        self, hass, host, password, *, port=80, unique_id=None, config_entry=None
    ):
        """Inicializa el coordinador y la sesión HTTP persistente."""
        self.config_entry = config_entry
        self.hass = hass
        self.host = host
        self.port = port
        self.password = password
        self.unique_id = unique_id or host
        self.httoken = None
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name="Livebox Arcadyan",
            manufacturer="Orange",
            model="Livebox Arcadyan",
        )
        # CookieJar con unsafe=True permite gestionar cookies en IPs locales
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))

        super().__init__(
            hass,
            _LOGGER,
            name="livebox",
            update_interval=timedelta(seconds=30),
            config_entry=config_entry,
        )

    def get_parent_device_identifier(self, device_key=None):
        """Return the router device identifier for tracked clients."""
        return (DOMAIN, self.unique_id)

    def get_parent_device_id(self, device_key=None):
        """Return the Home Assistant device ID for the router."""
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            self.get_parent_device_identifier(device_key), self.config_entry.entry_id
        )
        return device.id if device else None

    async def async_logout(self):
        """Cierra la sesión HTTP persistente."""
        await self.session.close()

    async def async_login(self):
        """Authenticate using the encrypted form submitted by the router UI."""
        try:
            base_url = f"http://{self.host}:{self.port}"
            login_url = f"{base_url}/login.htm"
            self.session.cookie_jar.update_cookies(
                {"lang": "sp", "display_err_msg": "yes", "menu_sel": "0"},
                response_url=URL(base_url),
            )

            async with self.session.get(login_url, timeout=10) as response:
                if response.status != 200:
                    _LOGGER.error("No se pudo cargar la página de login del Livebox.")
                    return False
                key, iv, self.httoken = _decode_login_material(await response.text())

            payload = {
                "httoken": self.httoken,
                "cnlvd2xh": _encode_login_field(self.password, key, iv),
                "dfr597hf": _encode_login_field(DEFAULT_USERNAME, key, iv),
                "login": "acceso",
            }

            async with self.session.post(
                f"{base_url}/login.cgi",
                data=payload,
                headers={"Referer": login_url},
                allow_redirects=False,
                timeout=10,
            ) as response:
                has_sid = any(
                    cookie.key.upper() == "SID" for cookie in self.session.cookie_jar
                )
                _LOGGER.info(
                    "Login Livebox: HTTP %s, cookie SID recibida: %s",
                    response.status,
                    has_sid,
                )
                if response.status == 302 and has_sid:
                    async with self.session.get(
                        f"{base_url}/network_connected.htm",
                        headers={"Referer": f"{base_url}/index.htm"},
                        timeout=10,
                    ) as page_response:
                        if page_response.status != 200:
                            _LOGGER.error(
                                "No se pudo cargar network_connected.htm (HTTP %s).",
                                page_response.status,
                            )
                            return False
                        _, _, self.httoken = _decode_login_material(
                            await page_response.text()
                        )
                    _LOGGER.info("Token de consulta obtenido de la página autenticada.")
                    return True
                _LOGGER.error(
                    "Falló el login del Livebox (HTTP %s, SID=%s).",
                    response.status,
                    has_sid,
                )
                return False

        except Exception as err:
            _LOGGER.error(
                "Error de conexión al intentar autenticar en Livebox: %s", err
            )
            return False

    async def _async_update_data(self):
        """Obtiene los dispositivos conectados usando el parámetro _tn dinámico y la cookie SID."""
        if not self.httoken and not await self.async_login():
            raise UpdateFailed("No se pudo autenticar en el Livebox.")

        timestamp = int(time.time() * 1000)
        url = f"http://{self.host}:{self.port}/cgi/cgi_clients.js"
        params = {"_tn": self.httoken, "_t": timestamp, "_": timestamp}
        headers = {
            "Accept": (
                "text/javascript, application/javascript, application/ecmascript, "
                "application/x-ecmascript, */*; q=0.01"
            ),
            "Accept-Language": "es-US,es;q=0.9,en-US;q=0.8,en;q=0.7,es-AR;q=0.6,es-419;q=0.5",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": f"http://{self.host}:{self.port}/network_connected.htm?t={timestamp}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/157.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }

        for attempt in range(2):
            try:
                async with self.session.get(
                    url, params=params, headers=headers, timeout=10
                ) as response:
                    if response.status == 200:
                        # Si devuelve texto plano/javascript con los clientes, lo procesamos o retornamos
                        text_data = await response.text()

                        # Si la sesión expiró, el router a veces responde con un script de redirección al login
                        if "login" in text_data.lower() and attempt == 0:
                            _LOGGER.warning(
                                "La sesión del Livebox ha expirado. Renovando..."
                            )
                            if await self.async_login():
                                params["_tn"] = self.httoken
                                continue
                            raise UpdateFailed("No se pudo autenticar en el Livebox.")

                        devices = _parse_client_devices(text_data)
                        property_names = re.findall(r'addCfg\("([^"]+)"', text_data)
                        client_property_names = [
                            name
                            for name in property_names
                            if re.search(r"client|device|host|mac", name, re.I)
                        ]
                        _LOGGER.info(
                            "El CGI devolvió %d dispositivos con MAC; "
                            "propiedades relacionadas: %s",
                            len(devices),
                            client_property_names,
                        )
                        return {"devices": devices, "topology_repeaters": {}}

                    if response.status in (401, 403) and attempt == 0:
                        _LOGGER.warning(
                            "La sesión del Livebox ha expirado (Código HTTP). Renovando..."
                        )
                        if await self.async_login():
                            params["_tn"] = self.httoken
                            continue

                    raise UpdateFailed(
                        f"Error al obtener datos, código HTTP: {response.status}"
                    )

            except aiohttp.ClientError as error:
                if attempt == 0:
                    _LOGGER.warning(
                        "Fallo de red temporal, reintentando login: %s", error
                    )
                    if await self.async_login():
                        params["_tn"] = self.httoken
                        continue
                    raise UpdateFailed(
                        "No se pudo renovar la sesión del Livebox."
                    ) from error
                raise UpdateFailed(f"Error de comunicación: {error}")

        raise UpdateFailed("No se pudo mantener la sesión activa en el Livebox.")

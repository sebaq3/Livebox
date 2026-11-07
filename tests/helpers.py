"""Shared helpers for the Livebox tests."""

from collections.abc import Iterator


class MockRouterSession:
    """HTTP responses for the Arcadyan router's login and client CGI."""

    def __init__(self) -> None:
        self.cookie_jar = _MockCookieJar()
        self.login_success = True
        self.cgi_status = 200
        self.close_count = 0
        self.login_count = 0
        self.cgi_responses: list[tuple[int, str]] = []
        self.client_js = self.client_response(
            [("Test phone", "192.168.1.20", "AA:BB:CC:DD:EE:01", "Online")]
        )

    async def __aenter__(self) -> MockRouterSession:
        """Enter the mocked aiohttp session context."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Close the mocked aiohttp session context."""
        await self.close()

    @staticmethod
    def client_response(
        devices: list[tuple[str, str, str, str]],
    ) -> str:
        """Create the online_client array consumed by the integration parser."""
        rows = []
        for name, ip_address, mac_address, status in devices:
            row = [""] * 17
            row[0] = name
            row[1] = ip_address
            row[2] = mac_address
            row[9] = status
            rows.append(row)
        return f"var online_client = {rows!r}; var dhcp_client = [];"

    def get(self, url: str, **kwargs: object) -> _MockResponse:
        """Return a response for a router GET request."""
        if url.endswith("/login.htm") or url.endswith("/network_connected.htm"):
            return _MockResponse(200, "<html></html>")
        if url.endswith("/cgi/cgi_clients.js"):
            if self.cgi_responses:
                status, body = self.cgi_responses.pop(0)
                return _MockResponse(status, body)
            return _MockResponse(self.cgi_status, self.client_js)
        return _MockResponse(404)

    def post(self, url: str, **kwargs: object) -> _MockResponse:
        """Return a response for the router login request."""
        if url.endswith("/login.cgi") and self.login_success:
            self.login_count += 1
            self.cookie_jar._cookies.append(type("Cookie", (), {"key": "SID"})())
            return _MockResponse(302)
        return _MockResponse(200)

    async def close(self) -> None:
        """Close the mocked session."""
        self.close_count += 1


class _MockCookieJar:
    """Cookie jar exposing the SID used by the login flow."""

    def __init__(self) -> None:
        self._cookies: list[object] = []

    def update_cookies(self, cookies: dict[str, str], **kwargs: object) -> None:
        """Accept initial router cookies."""

    def __iter__(self) -> Iterator[object]:
        return iter(self._cookies)


class _MockResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _MockResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def text(self) -> str:
        return self._body

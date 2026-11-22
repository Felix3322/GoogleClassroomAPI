"""A signed-in Google browser session, used by the reverse-engineered web clients.

The web clients talk to the same private endpoints as the Google web apps, so
they authenticate the same way a browser does: with the account's cookies.
You supply the cookies; this package never reads a browser profile itself.

Treat the cookies like a password. Anyone who holds them is signed in as you.
"""

from __future__ import annotations

import http.cookiejar
import json
from typing import Any, Mapping, Optional, Union

import httpx

from ..errors import ClassroomAPIError, ReauthRequired

XSSI_PREFIX = ")]}'"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class WebProtocolError(ClassroomAPIError):
    """A private endpoint did not answer the way it did when it was captured.

    Google changed something, or the request was rejected. The undocumented
    web protocol carries no compatibility promise, so expect this now and then.
    """


class WebSession:
    def __init__(
        self,
        cookies: Union[None, Mapping[str, str], http.cookiejar.CookieJar] = None,
        *,
        client: Optional[httpx.Client] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._client = client or httpx.Client(
            cookies=cookies,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": user_agent},
        )

    @classmethod
    def from_cookie_file(cls, path: str, **kwargs: Any) -> "WebSession":
        """Load cookies from a Netscape-format ``cookies.txt`` file."""
        jar = http.cookiejar.MozillaCookieJar(path)
        jar.load(ignore_discard=True, ignore_expires=True)
        return cls(jar, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, url, **kwargs)
        if response.url.host == "accounts.google.com":
            raise ReauthRequired("the Google web session has expired; sign in again and refresh the cookies")
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


def strip_xssi(text: str) -> str:
    """Drop the ``)]}'`` line Google puts in front of JSON answers."""
    stripped = text.lstrip()
    if stripped.startswith(XSSI_PREFIX):
        stripped = stripped[len(XSSI_PREFIX):]
    return stripped.lstrip()


def js_value(html: str, marker: str, opener: str) -> Any:
    """Parse the JSON value that follows ``marker`` in a page's inline script."""
    at = html.find(marker)
    if at < 0:
        raise WebProtocolError(f"{marker!r} not found in the page")
    start = html.find(opener, at + len(marker))
    if start < 0:
        raise WebProtocolError(f"no value after {marker!r}")
    try:
        value, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError as exc:
        raise WebProtocolError(f"cannot parse the value after {marker!r}: {exc}") from exc
    return value

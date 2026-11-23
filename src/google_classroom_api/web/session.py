"""A signed-in Google browser session, used by the reverse-engineered web clients.

The web clients talk to the same private endpoints as the Google web apps, so
they authenticate the same way a browser does: with the account's cookies.
You supply the cookies; this package never reads a browser profile itself.

Treat the cookies like a password. Anyone who holds them is signed in as you.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import time
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


ROTATE_URL = "https://accounts.google.com/RotateCookies"
ROTATE_BODY = '[000,"-0000000000000000000"]'
SESSION_TOKEN = "__Secure-1PSIDTS"


class WebSession:
    """A signed-in session built from exported cookies.

    Google keeps a browser signed in by handing it a fresh short-lived token
    (``__Secure-1PSIDTS``) every few minutes; an exported snapshot does not get
    those and was seen to stop working about two hours after export.
    :meth:`renew` asks for a fresh token the way the browser does. With
    ``renew_every`` (seconds) the session does that by itself before a request
    once the interval has passed, and with ``cookie_file`` it writes the updated
    cookies back so the next run starts from them.
    """

    def __init__(
        self,
        cookies: Union[None, Mapping[str, str], http.cookiejar.CookieJar] = None,
        *,
        client: Optional[httpx.Client] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        cookie_file: Optional[str] = None,
        renew_every: Optional[float] = None,
    ) -> None:
        self._client = client or httpx.Client(
            cookies=cookies,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": user_agent},
        )
        self._cookie_file = cookie_file
        self._renew_every = renew_every
        self._last_renewal = time.monotonic()

    @classmethod
    def from_cookie_file(cls, path: str, *, save_back: bool = False, **kwargs: Any) -> "WebSession":
        """Load cookies from a Netscape-format ``cookies.txt`` file.

        With ``save_back`` the file is rewritten whenever :meth:`renew` gets
        new cookies, so a long-running service keeps a usable file.
        """
        jar = http.cookiejar.MozillaCookieJar(path)
        jar.load(ignore_discard=True, ignore_expires=True)
        return cls(jar, cookie_file=path if save_back else None, **kwargs)

    # --- keeping the session alive ------------------------------------------------

    def _token(self) -> Optional[str]:
        for cookie in self._client.cookies.jar:
            if cookie.name == SESSION_TOKEN and cookie.domain.endswith("google.com"):
                return cookie.value
        return None

    def renew(self) -> bool:
        """Ask Google for a fresh session token, as the browser's sign-in helper does.

        Returns True when a new ``__Secure-1PSIDTS`` came back; Google only
        issues one when the current token is no longer brand new, so False right
        after an export is normal. Raises :class:`ReauthRequired` when the
        session is no longer accepted.
        """
        before = self._token()
        response = self._client.post(ROTATE_URL, content=ROTATE_BODY,
                                     headers={"Content-Type": "application/json"})
        self._last_renewal = time.monotonic()
        if response.status_code == 401:
            raise ReauthRequired("Google refused to renew the session; sign in again and export fresh cookies")
        if response.status_code != 200:
            raise WebProtocolError(f"RotateCookies answered HTTP {response.status_code}")
        self.save()
        after = self._token()
        return after is not None and after != before

    def save(self) -> None:
        """Write the current cookies back to the cookie file, if one was given."""
        jar = self._client.cookies.jar
        if not self._cookie_file or not isinstance(jar, http.cookiejar.MozillaCookieJar):
            return
        temporary = self._cookie_file + ".tmp"
        jar.save(temporary, ignore_discard=True, ignore_expires=True)
        os.replace(temporary, self._cookie_file)

    def _renew_if_due(self) -> None:
        if self._renew_every is None or time.monotonic() - self._last_renewal < self._renew_every:
            return
        try:
            self.renew()
        except ReauthRequired:
            raise
        except (httpx.HTTPError, WebProtocolError):
            self._last_renewal = time.monotonic()  # a failed renewal must not block the real request

    # --- requests -----------------------------------------------------------------

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._renew_if_due()
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

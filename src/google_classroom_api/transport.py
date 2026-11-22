"""Authenticated HTTP session for Google REST APIs with careful retry rules.

Safe methods are retried on transient failures. A POST that creates something
is never retried blindly: if it fails in transit, :class:`UncertainOutcome` is
raised so the caller can look for the object first. That is what keeps one
lesson from being published twice after a timeout.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator, Optional, Union

import httpx

from .errors import GoogleApiError, ReauthRequired, UncertainOutcome

_TRANSIENT = {429, 500, 502, 503, 504}
_SAFE_METHODS = {"GET", "PUT", "PATCH", "DELETE"}

TokenSource = Union[str, Callable[[], str], Any]


class GoogleSession:
    def __init__(
        self,
        token: TokenSource,
        *,
        client: Optional[httpx.Client] = None,
        max_retries: int = 3,
        backoff: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """``token`` is an access token, a callable returning one, or a ``TokenManager``."""
        self._token = token
        self._client = client or httpx.Client(timeout=30.0)
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep

    def _access_token(self) -> str:
        token = self._token
        if isinstance(token, str):
            return token
        getter = getattr(token, "access_token", None)
        if callable(getter):
            return getter()
        return token()

    def _refresh(self) -> bool:
        refresher = getattr(self._token, "force_refresh", None)
        if callable(refresher):
            refresher()
            return True
        return False

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        json: Any = None,
        content: Optional[bytes] = None,
        headers: Optional[dict] = None,
        idempotent: Optional[bool] = None,
    ) -> dict:
        method = method.upper()
        if idempotent is None:
            idempotent = method in _SAFE_METHODS
        attempt = 0
        refreshed = False
        while True:
            request_headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": "application/json",
            }
            if headers:
                request_headers.update(headers)
            try:
                response = self._client.request(
                    method, url, params=params, json=json, content=content, headers=request_headers
                )
            except httpx.TransportError as exc:
                if idempotent and attempt < self._max_retries:
                    attempt += 1
                    self._sleep(self._backoff * 2 ** (attempt - 1))
                    continue
                if idempotent:
                    raise GoogleApiError(
                        0, repr(exc), reason="TRANSPORT", method=method, url=url
                    ) from exc
                raise UncertainOutcome(f"{method} {url} failed in transit: {exc!r}") from exc

            status = response.status_code
            if status == 401:
                if not refreshed and self._refresh():
                    refreshed = True
                    continue
                raise ReauthRequired("Google rejected the access token; the teacher must sign in again")
            if status in _TRANSIENT:
                # 429 means the request was rejected, so it is safe to repeat for any method.
                if (idempotent or status == 429) and attempt < self._max_retries:
                    attempt += 1
                    self._sleep(self._backoff * 2 ** (attempt - 1))
                    continue
                if not idempotent and status != 429:
                    raise UncertainOutcome(f"{method} {url} returned {status}; it may have been applied")
            if status >= 400:
                raise _to_error(response, method, url)
            if not response.content:
                return {}
            return response.json()

    def paginate(self, url: str, items_key: str, *, params: Optional[dict] = None) -> Iterator[dict]:
        """Yield every item of a list endpoint, following ``nextPageToken``."""
        query = dict(params or {})
        while True:
            page = self.request("GET", url, params=query)
            yield from page.get(items_key, [])
            token = page.get("nextPageToken")
            if not token:
                return
            query["pageToken"] = token


def _to_error(response: httpx.Response, method: str, url: str) -> GoogleApiError:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = response.text[:300]
    reason = ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        message = body["error"].get("message", message)
        reason = body["error"].get("status", "")
    return GoogleApiError(
        response.status_code, message, reason=reason, method=method, url=url, body=body
    )

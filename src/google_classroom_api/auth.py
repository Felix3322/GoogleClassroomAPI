"""Google OAuth 2.0 for the teacher account (web server flow, offline access).

Only the teacher authorizes the site. Students never do.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import urlencode

import httpx

from .errors import ClassroomAPIError, ReauthRequired

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

_PREFIX = "https://www.googleapis.com/auth/"

#: Scopes for the full chain on official APIs.
DEFAULT_SCOPES = [
    _PREFIX + "classroom.courses.readonly",
    _PREFIX + "classroom.rosters.readonly",
    _PREFIX + "classroom.profile.emails",
    _PREFIX + "classroom.coursework.students",
    _PREFIX + "classroom.topics",
    _PREFIX + "forms.body",
    _PREFIX + "forms.responses.readonly",
    _PREFIX + "drive.file",
]

#: Extra scope needed only when the Apps Script helper is used (FormApp requires it).
APPS_SCRIPT_SCOPES = [_PREFIX + "forms"]

#: Extra scope for Classroom push notifications.
PUSH_SCOPES = [_PREFIX + "classroom.push-notifications"]


@dataclass
class TokenSet:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: float = 0.0
    scope: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TokenSet":
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=float(data.get("expires_at", 0.0)),
            scope=data.get("scope", ""),
        )


def build_auth_url(
    client_id: str,
    redirect_uri: str,
    *,
    scopes: Iterable[str] = DEFAULT_SCOPES,
    state: Optional[str] = None,
    login_hint: Optional[str] = None,
) -> str:
    """URL to send the teacher to. ``prompt=consent`` makes Google return a refresh token."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


class OAuthClient:
    """Exchanges authorization codes and refreshes access tokens."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: Optional[str] = None,
        *,
        client: Optional[httpx.Client] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._client = client or httpx.Client(timeout=30.0)
        self._clock = clock

    def auth_url(self, **kwargs) -> str:
        if not self.redirect_uri:
            raise ClassroomAPIError("redirect_uri is required to build the authorization URL")
        return build_auth_url(self.client_id, self.redirect_uri, **kwargs)

    def exchange_code(self, code: str) -> TokenSet:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri or "",
            }
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        tokens = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        if not tokens.refresh_token:
            tokens.refresh_token = refresh_token
        return tokens

    def _token_request(self, form: dict) -> TokenSet:
        data = {"client_id": self.client_id, "client_secret": self.client_secret, **form}
        response = self._client.post(TOKEN_URL, data=data)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            error = payload.get("error", "")
            description = payload.get("error_description", "")
            if error == "invalid_grant":
                raise ReauthRequired(
                    "Google no longer accepts this authorization (invalid_grant); "
                    "the teacher must sign in again"
                )
            raise ClassroomAPIError(f"token endpoint returned {response.status_code} {error}: {description}")
        return TokenSet(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=self._clock() + float(payload.get("expires_in", 3600)),
            scope=payload.get("scope", ""),
        )


class TokenManager:
    """Keeps one teacher's tokens fresh and tells the site when they change."""

    def __init__(
        self,
        oauth: OAuthClient,
        tokens: TokenSet,
        *,
        on_update: Optional[Callable[[TokenSet], None]] = None,
        clock: Callable[[], float] = time.time,
        leeway: float = 60.0,
    ) -> None:
        self._oauth = oauth
        self.tokens = tokens
        self._on_update = on_update
        self._clock = clock
        self._leeway = leeway

    def access_token(self) -> str:
        if not self.tokens.access_token or self._clock() >= self.tokens.expires_at - self._leeway:
            self.force_refresh()
        return self.tokens.access_token

    def force_refresh(self) -> str:
        if not self.tokens.refresh_token:
            raise ReauthRequired("no refresh token is stored; the teacher must sign in again")
        self.tokens = self._oauth.refresh(self.tokens.refresh_token)
        if self._on_update:
            self._on_update(self.tokens)
        return self.tokens.access_token

"""Exceptions raised by GoogleClassroomAPI."""

from __future__ import annotations

from typing import Any, Iterable


class ClassroomAPIError(Exception):
    """Base class for every error raised by this package."""


class ReauthRequired(ClassroomAPIError):
    """The teacher's Google authorization is no longer valid; they must sign in again."""


class UncertainOutcome(ClassroomAPIError):
    """A non-idempotent request failed in transit.

    Google may or may not have applied it, so the caller has to look for the
    object before trying to create it again.
    """


class GoogleApiError(ClassroomAPIError):
    """Google answered with an error status."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        reason: str = "",
        method: str = "",
        url: str = "",
        body: Any = None,
    ) -> None:
        super().__init__(f"{method} {url} -> {status} {reason}: {message}".strip())
        self.status = status
        self.message = message
        self.reason = reason
        self.method = method
        self.url = url
        self.body = body


class RecipientError(ClassroomAPIError):
    """The recipient list is empty or contains students that cannot be matched.

    Nothing is sent to Google when this is raised.
    """

    def __init__(self, message: str, *, unmatched: Iterable[str] = (), empty: bool = False) -> None:
        super().__init__(message)
        self.unmatched = list(unmatched)
        self.empty = empty


class AccessMismatch(ClassroomAPIError):
    """The access list read back from Google differs from the one that was requested."""

    def __init__(
        self,
        message: str,
        *,
        unexpected: Iterable[str] = (),
        missing: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.unexpected = list(unexpected)
        self.missing = list(missing)


class NotLinked(ClassroomAPIError):
    """The lesson has not been published through this library yet."""

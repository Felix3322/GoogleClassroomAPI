"""RFC 3339 helpers. Google returns timestamps with 0 to 9 fractional digits."""

from __future__ import annotations

import re
from datetime import datetime, timezone

_FRACTION = re.compile(r"\.(\d+)")


def parse_ts(value: str) -> datetime:
    """Parse a Google timestamp such as ``2026-11-23T02:16:31.123456789Z``."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_rfc3339(moment: datetime) -> str:
    """Format an aware datetime as UTC ``...Z``."""
    if moment.tzinfo is None:
        raise ValueError("a timezone-aware datetime is required")
    utc = moment.astimezone(timezone.utc)
    text = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        text += f".{utc.microsecond:06d}"
    return text + "Z"

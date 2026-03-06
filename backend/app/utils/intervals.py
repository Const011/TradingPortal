"""Common utilities for mapping Bybit interval strings to seconds."""

from typing import Final

INTERVAL_SECONDS: Final[dict[str, int]] = {
    "1": 60,
    "3": 180,
    "5": 300,
    "15": 900,
    "30": 1800,
    "60": 3600,
    "120": 7200,
    "240": 14400,
    "360": 21600,
    "720": 43200,
    "D": 86400,
    "W": 604800,
    "M": 2592000,
}


def interval_seconds(interval: str, *, default: int = 0) -> int:
    """Return bar duration in seconds for Bybit interval string."""
    return INTERVAL_SECONDS.get(interval, default)


from __future__ import annotations

from datetime import datetime
from typing import Literal


def ts_human(ts: int | float, *, unit: Literal["auto", "ms", "s"] = "auto") -> str:
    """
    Format a Unix timestamp into 'YYYY-MM-DD HH:MM:SS' in local time.

    - unit="ms": `ts` is milliseconds.
    - unit="s": `ts` is seconds.
    - unit="auto": treat values >= 1e12 as milliseconds; otherwise seconds.
    """
    if unit == "auto":
        unit = "ms" if ts >= 1e12 else "s"
    sec = (ts / 1000.0) if unit == "ms" else float(ts)
    return datetime.fromtimestamp(sec).strftime("%Y-%m-%d %H:%M:%S")


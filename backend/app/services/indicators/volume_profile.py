"""Volume profile indicator: distributes volume across price levels with recency weighting."""

from app.schemas.market import Candle

DEFAULT_BUCKETS = 500
DEFAULT_WINDOW = 2000


def build_volume_profile_from_candles(
    candles: list[Candle],
    time: int,
    width: int = 6,
    num_buckets: int = DEFAULT_BUCKETS,
    window_size: int = DEFAULT_WINDOW,
) -> dict | None:
    """Build volume profile from candles.

    Distributes each candle's volume across price levels it touched (high to low),
    with recency weighting: weight = (window_size - position_from_newest) / window_size.
    Older bars contribute less.

    Returns dict with keys: time, profile (list of {price, vol}), width.
    Time is in seconds for chart compatibility.
    """
    if not candles:
        return None

    window_candles = candles[-window_size:]
    if not window_candles:
        return None

    low = min(c.low for c in window_candles)
    high = max(c.high for c in window_candles)
    range_ = high - low
    if range_ <= 0:
        return None

    bucket_size = range_ / num_buckets
    buckets: dict[int, float] = {}

    for i, c in enumerate(window_candles):
        position_from_newest = len(window_candles) - 1 - i
        weight = (window_size - position_from_newest) / window_size

        c_low = max(c.low, low)
        c_high = min(c.high, high)
        c_range = c_high - c_low
        if c_range <= 0:
            continue

        start_idx = max(0, int((c_low - low) / bucket_size))
        end_idx = min(num_buckets - 1, int((c_high - low) / bucket_size))
        levels_touched = end_idx - start_idx + 1
        vol_per_level = (c.volume / levels_touched) * weight

        for idx in range(start_idx, end_idx + 1):
            buckets[idx] = buckets.get(idx, 0.0) + vol_per_level

    profile = [
        {"price": low + (idx + 0.5) * bucket_size, "vol": buckets.get(idx, 0.0)}
        for idx in range(num_buckets)
    ]
    profile.sort(key=lambda p: p["price"], reverse=True)

    if len(profile) < 2:
        return None

    return {
        "time": time,
        "profile": profile,
        "width": width,
    }

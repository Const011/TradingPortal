from __future__ import annotations

from typing import Dict, List

from app.schemas.market import Candle


# Default EMA length for CVD smoothing; must stay in sync with strategy docs.
DEFAULT_CVD_LENGTH = 14


def _ema(values: List[float], length: int) -> List[float]:
    """Simple EMA implementation matching Pine-style behavior reasonably closely.

    Initializes with the first value and then applies standard EMA recursion.
    """
    if not values or length <= 0:
        return [0.0 for _ in values]
    alpha = 2.0 / (length + 1.0)
    out: List[float] = []
    ema_val = values[0]
    out.append(ema_val)
    for v in values[1:]:
        ema_val = alpha * v + (1.0 - alpha) * ema_val
        out.append(ema_val)
    return out


def compute_cumulative_volume_delta(
    candles: List[Candle],
    length: int = DEFAULT_CVD_LENGTH,
) -> Dict[str, object]:
    """Compute cumulative volume delta-style metrics over the given candles.

    Port of the TradingView Pine script in docs/indicators-pine/cumulative_volume_delta.pine.

    For each bar we derive:
    - buying_volume, selling_volume based on wick/body proportions
    - EMA(buying_volume), EMA(selling_volume)
    - volume_strength_wave = max(ema_buy, ema_sell)
    - ema_volume_strength_wave = EMA(volume_strength_wave)
    - cumulative_volume_delta = ema_buy - ema_sell
    """
    n = len(candles)
    if n == 0:
        return {"length": length, "points": []}

    buying_raw: List[float] = []
    selling_raw: List[float] = []

    for c in candles:
        spread = c.high - c.low
        if spread <= 0:
            # Degenerate bar: assign half volume to each side.
            buying_raw.append(c.volume * 0.5)
            selling_raw.append(c.volume * 0.5)
            continue

        if c.close > c.open:
            upper_wick = c.high - c.close
            lower_wick = c.open - c.low
        else:
            upper_wick = c.high - c.open
            lower_wick = c.close - c.low

        body_length = spread - (upper_wick + lower_wick)
        percent_upper_wick = upper_wick / spread
        percent_lower_wick = lower_wick / spread
        percent_body_length = body_length / spread

        wick_avg = (percent_upper_wick + percent_lower_wick) / 2.0

        if c.close > c.open:
            buying_volume = (percent_body_length + wick_avg) * c.volume
            selling_volume = wick_avg * c.volume
        elif c.close < c.open:
            selling_volume = (percent_body_length + wick_avg) * c.volume
            buying_volume = wick_avg * c.volume
        else:
            # Doji: treat wicks as balanced participation.
            buying_volume = wick_avg * c.volume
            selling_volume = wick_avg * c.volume

        buying_raw.append(float(buying_volume))
        selling_raw.append(float(selling_volume))

    ema_buy = _ema(buying_raw, length)
    ema_sell = _ema(selling_raw, length)

    strength_wave: List[float] = [
        max(b, s) for b, s in zip(ema_buy, ema_sell, strict=False)
    ]
    ema_strength = _ema(strength_wave, length)
    cum_delta: List[float] = [b - s for b, s in zip(ema_buy, ema_sell, strict=False)]

    points: List[Dict[str, float]] = []
    for i, c in enumerate(candles):
        # Use seconds for time, consistent with other indicators.
        t_sec = c.time // 1000 if c.time >= 1_000_000_000_000 else c.time
        points.append(
            {
                "time": float(t_sec),
                "buy": float(ema_buy[i]),
                "sell": float(ema_sell[i]),
                "delta": float(cum_delta[i]),
                "strength": float(ema_strength[i]),
            }
        )

    return {"length": length, "points": points}


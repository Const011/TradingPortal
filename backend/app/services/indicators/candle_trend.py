"""Candle trend colors (swing × internal) for Phase 5. LuxAlgo inspired."""

from app.schemas.market import Candle
from app.services.indicators.smart_money_structure import (
    _leg,
    DEFAULT_SWING_LENGTH,
    INTERNAL_LENGTH,
    BULLISH,
    BEARISH,
)

# 4-way: swingTrend × internalTrend
SWING_BULL_INTERNAL_BULL = "#22c55e"  # bright green
SWING_BULL_INTERNAL_BEAR = "#15803d"  # dark green
SWING_BEAR_INTERNAL_BULL = "#dc2626"  # dark red
SWING_BEAR_INTERNAL_BEAR = "#b91c1c"  # bright red


def compute_candle_colors(
    candles: list[Candle],
    swing_length: int = DEFAULT_SWING_LENGTH,
) -> dict[int, str]:
    """
    Compute trend-based candle color per bar.
    Returns { time_ms: color } for each candle.
    """
    if len(candles) < max(swing_length, INTERNAL_LENGTH) + 2:
        return {}

    n = len(candles)
    result: dict[int, str] = {}
    swing_leg = 0
    internal_leg = 0
    swing_trend = BEARISH
    internal_trend = BEARISH

    for i in range(max(swing_length, INTERNAL_LENGTH) + 1, n):
        c = candles[i]
        close = c.close
        sw_leg = _leg(candles, swing_length, i, swing_leg)
        int_leg = _leg(candles, INTERNAL_LENGTH, i, internal_leg)

        if sw_leg != swing_leg:
            swing_leg = sw_leg
            if sw_leg == 0:
                swing_trend = BEARISH
            else:
                swing_trend = BULLISH
        if int_leg != internal_leg:
            internal_leg = int_leg
            if int_leg == 0:
                internal_trend = BEARISH
            else:
                internal_trend = BULLISH

        # BOS/CHoCH updates trend - we need to run structure crossover logic
        swing_high = candles[i - swing_length].high if i >= swing_length else 0.0
        swing_low = candles[i - swing_length].low if i >= swing_length else 0.0
        internal_high = candles[i - INTERNAL_LENGTH].high if i >= INTERNAL_LENGTH else 0.0
        internal_low = candles[i - INTERNAL_LENGTH].low if i >= INTERNAL_LENGTH else 0.0

        if swing_high > 0 and close > swing_high:
            swing_trend = BULLISH
        if swing_low > 0 and close < swing_low:
            swing_trend = BEARISH
        if internal_high > 0 and close > internal_high:
            internal_trend = BULLISH
        if internal_low > 0 and close < internal_low:
            internal_trend = BEARISH

        if swing_trend == BULLISH and internal_trend == BULLISH:
            color = SWING_BULL_INTERNAL_BULL
        elif swing_trend == BULLISH and internal_trend == BEARISH:
            color = SWING_BULL_INTERNAL_BEAR
        elif swing_trend == BEARISH and internal_trend == BULLISH:
            color = SWING_BEAR_INTERNAL_BULL
        else:
            color = SWING_BEAR_INTERNAL_BEAR

        result[c.time] = color

    return result

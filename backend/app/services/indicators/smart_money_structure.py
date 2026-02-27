"""Smart Money structure — BOS/CHoCH from swing and internal pivots. LuxAlgo inspired."""

from dataclasses import dataclass
from app.schemas.market import Candle

DEFAULT_SWING_LENGTH = 50
INTERNAL_LENGTH = 5
MAX_STRUCTURE_ELEMENTS = 20
MAX_LOOKBACK = 500

BULLISH = 1
BEARISH = -1

# Colors: bullish = green, bearish = red
SWING_BULL_COLOR = "rgba(34, 197, 94, 0.9)"
SWING_BEAR_COLOR = "rgba(239, 68, 68, 0.9)"
INTERNAL_BULL_COLOR = "rgba(34, 197, 94, 0.6)"
INTERNAL_BEAR_COLOR = "rgba(239, 68, 68, 0.6)"


@dataclass
class Pivot:
    price: float
    bar_idx: int
    crossed: bool

def _pivot_valid(p: Pivot, min_idx: int) -> bool:
    return p.bar_idx >= min_idx and p.price > 0


def _leg(candles: list[Candle], size: int, i: int, prev_leg: int) -> int:
    """
    Pine leg(): 0 = bearish leg (new swing high), 1 = bullish leg (new swing low).
    newLegHigh = high[size] > ta.highest(size); newLegLow = low[size] < ta.lowest(size).
    """
    if i < size + 1 or i >= len(candles):
        return prev_leg
    high_at = candles[i - size].high
    low_at = candles[i - size].low
    highest = max(candles[j].high for j in range(i - size + 1, i + 1))
    lowest = min(candles[j].low for j in range(i - size + 1, i + 1))
    if high_at > highest:
        return 0
    if low_at < lowest:
        return 1
    return prev_leg


def compute_structure(
    candles: list[Candle],
    swing_length: int = DEFAULT_SWING_LENGTH,
    show_structure: bool = True,
    show_internals: bool = True,
    show_swing_bull: str = "ALL",
    show_swing_bear: str = "ALL",
    show_internal_bull: str = "ALL",
    show_internal_bear: str = "ALL",
) -> dict:
    """
    Compute BOS/CHoCH structure lines and labels.
    Returns { "lines": [...], "labels": [...] }.
    show_*_bull/bear: "ALL" | "BOS" | "CHoCH"
    """
    if len(candles) < max(swing_length, INTERNAL_LENGTH) + 2:
        return {"lines": [], "labels": []}

    n = len(candles)
    lines: list[dict] = []
    labels: list[dict] = []

    swing_high = Pivot(price=0.0, bar_idx=-1, crossed=False)
    swing_low = Pivot(price=0.0, bar_idx=-1, crossed=False)
    internal_high = Pivot(price=0.0, bar_idx=-1, crossed=False)
    internal_low = Pivot(price=0.0, bar_idx=-1, crossed=False)
    swing_trend = BEARISH
    internal_trend = BEARISH
    swing_leg = 0
    internal_leg = 0

    last_bar = n - 1
    in_range = lambda idx: last_bar - idx <= MAX_LOOKBACK

    for i in range(max(swing_length, INTERNAL_LENGTH) + 1, n):
        c = candles[i]
        close = c.close
        bar_time_ms = c.time
        bar_time_s = bar_time_ms // 1000

        sw_leg = _leg(candles, swing_length, i, swing_leg)
        int_leg = _leg(candles, INTERNAL_LENGTH, i, internal_leg)

        sw_leg_changed = sw_leg != swing_leg
        int_leg_changed = int_leg != internal_leg

        swing_leg = sw_leg
        internal_leg = int_leg

        if sw_leg_changed:
            if sw_leg == 0:
                swing_high = Pivot(
                    price=candles[i - swing_length].high,
                    bar_idx=i - swing_length,
                    crossed=False,
                )
            else:
                swing_low = Pivot(
                    price=candles[i - swing_length].low,
                    bar_idx=i - swing_length,
                    crossed=False,
                )
        if int_leg_changed:
            if int_leg == 0:
                internal_high = Pivot(
                    price=candles[i - INTERNAL_LENGTH].high,
                    bar_idx=i - INTERNAL_LENGTH,
                    crossed=False,
                )
            else:
                internal_low = Pivot(
                    price=candles[i - INTERNAL_LENGTH].low,
                    bar_idx=i - INTERNAL_LENGTH,
                    crossed=False,
                )

        def emit_structure(
            pivot: Pivot,
            trend: int,
            is_bullish_signal: bool,
            internal: bool,
            show_bull: str,
            show_bear: str,
        ) -> None:
            nonlocal swing_trend, internal_trend
            if is_bullish_signal:
                tag = "CHoCH" if trend == BEARISH else "BOS"
                new_trend = BULLISH
                color = INTERNAL_BULL_COLOR if internal else SWING_BULL_COLOR
                show_opt = show_bull
            else:
                tag = "CHoCH" if trend == BULLISH else "BOS"
                new_trend = BEARISH
                color = INTERNAL_BEAR_COLOR if internal else SWING_BEAR_COLOR
                show_opt = show_bear

            pivot.crossed = True
            if internal:
                internal_trend = new_trend
            else:
                swing_trend = new_trend

            show_it = show_opt == "ALL" or (show_opt == "BOS" and tag == "BOS") or (show_opt == "CHoCH" and tag == "CHoCH")
            if not show_it or not in_range(pivot.bar_idx):
                return

            pivot_time_s = candles[pivot.bar_idx].time // 1000
            mid_bar = (pivot.bar_idx + i) // 2
            mid_time_s = candles[mid_bar].time // 1000

            lines.append({
                "type": "lineSegment",
                "from": {"time": pivot_time_s, "price": pivot.price},
                "to": {"time": bar_time_s, "price": pivot.price},
                "color": color,
                "style": "dashed" if internal else "solid",
            })
            labels.append({
                "type": "label",
                "time": mid_time_s,
                "price": pivot.price,
                "text": tag,
                "color": color,
                "style": "down" if is_bullish_signal else "up",
            })

        start_idx = max(swing_length, INTERNAL_LENGTH) + 1
        if show_structure and _pivot_valid(swing_high, start_idx) and not swing_high.crossed and close > swing_high.price:
            emit_structure(swing_high, swing_trend, True, False, show_swing_bull, show_swing_bear)
        if show_structure and _pivot_valid(swing_low, start_idx) and not swing_low.crossed and close < swing_low.price:
            emit_structure(swing_low, swing_trend, False, False, show_swing_bull, show_swing_bear)
        if show_internals and _pivot_valid(internal_high, start_idx) and not internal_high.crossed and close > internal_high.price:
            if not _pivot_valid(swing_high, start_idx) or internal_high.price != swing_high.price:
                emit_structure(internal_high, internal_trend, True, True, show_internal_bull, show_internal_bear)
        if show_internals and _pivot_valid(internal_low, start_idx) and not internal_low.crossed and close < internal_low.price:
            if not _pivot_valid(swing_low, start_idx) or internal_low.price != swing_low.price:
                emit_structure(internal_low, internal_trend, False, True, show_internal_bull, show_internal_bear)

    return {
        "lines": lines[-MAX_STRUCTURE_ELEMENTS:],
        "labels": labels[-MAX_STRUCTURE_ELEMENTS:],
    }

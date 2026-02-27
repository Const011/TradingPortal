"""Smart Money structure — BOS/CHoCH, swing labels (HH/HL/LH/LL), EQH/EQL. LuxAlgo inspired."""

from dataclasses import dataclass
from app.schemas.market import Candle

DEFAULT_SWING_LENGTH = 50
INTERNAL_LENGTH = 5
EQUAL_HL_LENGTH = 5
ATR_LENGTH = 200
DEFAULT_EQUAL_THRESHOLD = 0.1
MAX_STRUCTURE_ELEMENTS = 20
MAX_SWING_LABELS = 15
MAX_EQUAL_ELEMENTS = 10
MAX_LOOKBACK = 500

BULLISH = 1
BEARISH = -1

# Colors: bullish = green, bearish = red
SWING_BULL_COLOR = "rgba(34, 197, 94, 0.9)"
SWING_BEAR_COLOR = "rgba(239, 68, 68, 0.9)"
INTERNAL_BULL_COLOR = "rgba(34, 197, 94, 0.6)"
INTERNAL_BEAR_COLOR = "rgba(239, 68, 68, 0.6)"

# Candle trend colors (swing × internal): bright/dark green, bright/dark red
CANDLE_SWING_BULL_INTERNAL_BULL = "#22c55e"
CANDLE_SWING_BULL_INTERNAL_BEAR = "#15803d"
CANDLE_SWING_BEAR_INTERNAL_BULL = "#b91c1c"
CANDLE_SWING_BEAR_INTERNAL_BEAR = "#dc2626"


def _trend_to_color(swing: int, internal: int) -> str:
    if swing == BULLISH and internal == BULLISH:
        return CANDLE_SWING_BULL_INTERNAL_BULL
    if swing == BULLISH and internal == BEARISH:
        return CANDLE_SWING_BULL_INTERNAL_BEAR
    if swing == BEARISH and internal == BULLISH:
        return CANDLE_SWING_BEAR_INTERNAL_BULL
    return CANDLE_SWING_BEAR_INTERNAL_BEAR


@dataclass
class Pivot:
    price: float
    bar_idx: int
    crossed: bool

def _pivot_valid(p: Pivot, min_idx: int) -> bool:
    return p.bar_idx >= min_idx and p.price > 0


def _atr(candles: list[Candle], length: int, up_to: int) -> float:
    """RMA of true range, Pine ta.atr(length). Returns 0 if insufficient data."""
    if up_to < 1 or length < 1 or up_to >= len(candles):
        return 0.0
    alpha = 1.0 / length
    rma = 0.0
    for j in range(1, up_to + 1):
        c = candles[j]
        prev_c = candles[j - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev_c.close),
            abs(c.low - prev_c.close),
        )
        rma = rma + alpha * (tr - rma) if j > 1 else tr
    return rma


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
    show_swings: bool = True,
    show_equal_hl: bool = True,
    equal_threshold: float = DEFAULT_EQUAL_THRESHOLD,
    show_swing_bull: str = "ALL",
    show_swing_bear: str = "ALL",
    show_internal_bull: str = "ALL",
    show_internal_bear: str = "ALL",
    include_candle_colors: bool = False,
    max_swing_labels: int | None = None,
) -> dict:
    """
    Compute BOS/CHoCH structure, swing labels (HH/HL/LH/LL), EQH/EQL.
    Returns { "lines": [...], "labels": [...], "swingLabels": [...], "equalHighsLows": { "lines": [...], "labels": [...] } }.
    """
    min_len = max(swing_length, INTERNAL_LENGTH, EQUAL_HL_LENGTH) + 2
    empty = {"lines": [], "labels": [], "swingLabels": [], "equalHighsLows": {"lines": [], "labels": []}}
    if include_candle_colors:
        empty["candleColors"] = {}
    if len(candles) < min_len:
        return empty

    n = len(candles)
    lines: list[dict] = []
    labels: list[dict] = []
    swing_labels: list[dict] = []
    equal_lines: list[dict] = []
    equal_labels: list[dict] = []
    candle_colors: dict[int, str] = {}

    swing_high = Pivot(price=0.0, bar_idx=-1, crossed=False)
    swing_low = Pivot(price=0.0, bar_idx=-1, crossed=False)
    internal_high = Pivot(price=0.0, bar_idx=-1, crossed=False)
    internal_low = Pivot(price=0.0, bar_idx=-1, crossed=False)
    swing_trend = BEARISH
    internal_trend = BEARISH
    swing_leg = 0
    internal_leg = 0
    eq_leg = 0
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    last_equal_high: float | None = None
    last_equal_low: float | None = None
    last_equal_high_bar = -1
    last_equal_low_bar = -1

    last_bar = n - 1
    in_range = lambda idx: last_bar - idx <= MAX_LOOKBACK

    for i in range(max(swing_length, INTERNAL_LENGTH, EQUAL_HL_LENGTH) + 1, n):
        c = candles[i]
        close = c.close
        bar_time_ms = c.time
        bar_time_s = bar_time_ms // 1000

        sw_leg = _leg(candles, swing_length, i, swing_leg)
        int_leg = _leg(candles, INTERNAL_LENGTH, i, internal_leg)
        eq_leg_new = _leg(candles, EQUAL_HL_LENGTH, i, eq_leg)

        sw_leg_changed = sw_leg != swing_leg
        int_leg_changed = int_leg != internal_leg
        eq_leg_changed = eq_leg_new != eq_leg

        swing_leg = sw_leg
        internal_leg = int_leg
        eq_leg = eq_leg_new

        if sw_leg_changed:
            if sw_leg == 0:
                new_high = candles[i - swing_length].high
                swing_high = Pivot(price=new_high, bar_idx=i - swing_length, crossed=False)
                if show_swings and in_range(i - swing_length):
                    tag = "HH" if last_swing_high is None or new_high > last_swing_high else "LH"
                    t_s = candles[i - swing_length].time // 1000
                    swing_labels.append({
                        "type": "label",
                        "time": t_s,
                        "price": new_high,
                        "text": tag,
                        "color": SWING_BEAR_COLOR,
                        "style": "down",
                    })
                last_swing_high = new_high
            else:
                new_low = candles[i - swing_length].low
                swing_low = Pivot(price=new_low, bar_idx=i - swing_length, crossed=False)
                if show_swings and in_range(i - swing_length):
                    tag = "LL" if last_swing_low is None or new_low < last_swing_low else "HL"
                    t_s = candles[i - swing_length].time // 1000
                    swing_labels.append({
                        "type": "label",
                        "time": t_s,
                        "price": new_low,
                        "text": tag,
                        "color": SWING_BULL_COLOR,
                        "style": "up",
                    })
                last_swing_low = new_low
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

        if show_equal_hl and eq_leg_changed and in_range(i - EQUAL_HL_LENGTH):
            atr_val = _atr(candles, ATR_LENGTH, i) if i >= ATR_LENGTH else 0.0
            thresh = equal_threshold * atr_val if atr_val > 0 else 0.0
            if eq_leg == 0:
                new_high = candles[i - EQUAL_HL_LENGTH].high
                if last_equal_high is not None and thresh > 0 and abs(last_equal_high - new_high) < thresh:
                    t_old = candles[last_equal_high_bar].time // 1000
                    t_new = candles[i - EQUAL_HL_LENGTH].time // 1000
                    mid_bar = (last_equal_high_bar + i - EQUAL_HL_LENGTH) // 2
                    mid_t = candles[mid_bar].time // 1000
                    equal_lines.append({
                        "type": "lineSegment",
                        "from": {"time": t_old, "price": last_equal_high},
                        "to": {"time": t_new, "price": new_high},
                        "color": SWING_BEAR_COLOR,
                        "style": "dotted",
                    })
                    equal_labels.append({
                        "type": "label",
                        "time": mid_t,
                        "price": new_high,
                        "text": "EQH",
                        "color": SWING_BEAR_COLOR,
                        "style": "down",
                    })
                last_equal_high = new_high
                last_equal_high_bar = i - EQUAL_HL_LENGTH
            else:
                new_low = candles[i - EQUAL_HL_LENGTH].low
                if last_equal_low is not None and thresh > 0 and abs(last_equal_low - new_low) < thresh:
                    t_old = candles[last_equal_low_bar].time // 1000
                    t_new = candles[i - EQUAL_HL_LENGTH].time // 1000
                    mid_bar = (last_equal_low_bar + i - EQUAL_HL_LENGTH) // 2
                    mid_t = candles[mid_bar].time // 1000
                    equal_lines.append({
                        "type": "lineSegment",
                        "from": {"time": t_old, "price": last_equal_low},
                        "to": {"time": t_new, "price": new_low},
                        "color": SWING_BULL_COLOR,
                        "style": "dotted",
                    })
                    equal_labels.append({
                        "type": "label",
                        "time": mid_t,
                        "price": new_low,
                        "text": "EQL",
                        "color": SWING_BULL_COLOR,
                        "style": "up",
                    })
                last_equal_low = new_low
                last_equal_low_bar = i - EQUAL_HL_LENGTH

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

        if include_candle_colors:
            candle_colors[c.time] = _trend_to_color(swing_trend, internal_trend)

    swing_limit = max_swing_labels if max_swing_labels is not None else MAX_SWING_LABELS
    out = {
        "lines": lines[-MAX_STRUCTURE_ELEMENTS:],
        "labels": labels[-MAX_STRUCTURE_ELEMENTS:],
        "swingLabels": swing_labels[-swing_limit:],
        "equalHighsLows": {
            "lines": equal_lines[-MAX_EQUAL_ELEMENTS:],
            "labels": equal_labels[-MAX_EQUAL_ELEMENTS:],
        },
    }
    if include_candle_colors:
        out["candleColors"] = candle_colors
    return out

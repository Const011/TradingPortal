"""Order blocks from swing structure — LuxAlgo inspired."""

from dataclasses import dataclass
from app.schemas.market import Candle

DEFAULT_SWING_LENGTH = 10
DEFAULT_SHOW_BULL = 3
DEFAULT_SHOW_BEAR = 3
MAX_LOOKBACK = 380

BULL_FILL = "rgba(33, 87, 243, 0.2)"
BULL_BREAK = "rgba(212, 255, 0, 0.2)"
BEAR_FILL = "rgba(212, 255, 0, 0.2)"
BEAR_BREAK = "rgba(33, 87, 243, 0.2)"


@dataclass
class OrderBlock:
    top: float
    bottom: float
    loc: int
    breaker: bool
    break_loc: int | None
    fill_color: str
    break_color: str


def _swings(
    candles: list[Candle],
    length: int,
    i: int,
    os_prev: int,
) -> tuple[float | None, int | None, float | None, int | None, int]:
    """
    Pine: os=0 when high[len]>ta.highest(len), os=1 when low[len]<ta.lowest(len).
    Return (swing_high_price, swing_high_idx, swing_low_price, swing_low_idx, os_new).
    """
    if i < length + 1 or i >= len(candles):
        return None, None, None, None, os_prev

    high_at_len = candles[i - length].high
    low_at_len = candles[i - length].low
    highest = max(candles[j].high for j in range(i - length + 1, i + 1))
    lowest = min(candles[j].low for j in range(i - length + 1, i + 1))

    os_new = 0 if high_at_len > highest else (1 if low_at_len < lowest else os_prev)

    new_swing_high = os_new == 0 and os_prev != 0
    new_swing_low = os_new == 1 and os_prev != 1

    sh = (high_at_len, i - length) if new_swing_high else (None, None)
    sl = (low_at_len, i - length) if new_swing_low else (None, None)
    return sh[0], sh[1], sl[0], sl[1], os_new


def compute_order_blocks(
    candles: list[Candle],
    swing_length: int = DEFAULT_SWING_LENGTH,
    show_bull: int = DEFAULT_SHOW_BULL,
    show_bear: int = DEFAULT_SHOW_BEAR,
    use_body: bool = False,
) -> dict:
    """
    Compute bullish and bearish order blocks from candle data.
    Returns dict with bullish/bearish lists of OB primitives for graphics.
    """
    if len(candles) < swing_length + 2:
        return {"bullish": [], "bearish": []}

    n = len(candles)
    bullish_ob: list[OrderBlock] = []
    bearish_ob: list[OrderBlock] = []
    top_crossed = False
    btm_crossed = False
    swing_top_y: float | None = None
    swing_top_x: int | None = None
    swing_btm_y: float | None = None
    swing_btm_x: int | None = None
    os = 1  # Pine init: var os = 0, but first transition matters

    for i in range(swing_length + 1, n):
        c = candles[i]
        h_hi = max(c.open, c.close) if use_body else c.high
        h_lo = min(c.open, c.close) if use_body else c.low

        sh_y, sh_x, sl_y, sl_x, os = _swings(candles, swing_length, i, os)

        if sh_y is not None and sh_x is not None:
            swing_top_y = sh_y
            swing_top_x = sh_x
            top_crossed = False
        if sl_y is not None and sl_x is not None:
            swing_btm_y = sl_y
            swing_btm_x = sl_x
            btm_crossed = False

        close = c.close
        if swing_top_y is not None and swing_top_x is not None and close > swing_top_y and not top_crossed and len(bullish_ob) < show_bull:
            top_crossed = True
            start_idx = swing_top_x + 1
            end_idx = i - 1
            if end_idx >= start_idx:
                minima = candles[start_idx].low
                maxima = candles[start_idx].high
                loc_bar = start_idx
                for j in range(start_idx + 1, end_idx + 1):
                    if candles[j].low < minima:
                        minima = candles[j].low
                        maxima = candles[j].high
                        loc_bar = j
                bullish_ob.insert(0, OrderBlock(top=maxima, bottom=minima, loc=loc_bar, breaker=False, break_loc=None, fill_color=BULL_FILL, break_color=BULL_BREAK))

        if swing_btm_y is not None and swing_btm_x is not None and close < swing_btm_y and not btm_crossed and len(bearish_ob) < show_bear:
            btm_crossed = True
            start_idx = swing_btm_x + 1
            end_idx = i - 1
            if end_idx >= start_idx:
                maxima = candles[start_idx].high
                minima = candles[start_idx].low
                loc_bar = start_idx
                for j in range(start_idx + 1, end_idx + 1):
                    if candles[j].high > maxima:
                        maxima = candles[j].high
                        minima = candles[j].low
                        loc_bar = j
                bearish_ob.insert(0, OrderBlock(top=maxima, bottom=minima, loc=loc_bar, breaker=False, break_loc=None, fill_color=BEAR_FILL, break_color=BEAR_BREAK))

        for ob in list(bullish_ob):
            if not ob.breaker and ob.loc < i:
                if min(c.close, c.open) < ob.bottom:
                    ob.breaker = True
                    ob.break_loc = i
            else:
                if c.close > ob.top:
                    bullish_ob.remove(ob)

        for ob in list(bearish_ob):
            if not ob.breaker and ob.loc < i:
                if max(c.close, c.open) > ob.top:
                    ob.breaker = True
                    ob.break_loc = i
            else:
                if c.close < ob.bottom:
                    bearish_ob.remove(ob)

    last_bar = n - 1
    bullish_ob = [ob for ob in bullish_ob if last_bar - ob.loc <= MAX_LOOKBACK]
    bearish_ob = [ob for ob in bearish_ob if last_bar - ob.loc <= MAX_LOOKBACK]

    def ob_to_primitive(ob: OrderBlock) -> dict:
        loc_candle = candles[ob.loc]
        start_time = loc_candle.time // 1000
        end_time = candles[-1].time // 1000
        break_time = candles[ob.break_loc].time // 1000 if ob.breaker and ob.break_loc is not None else None
        return {
            "top": ob.top,
            "bottom": ob.bottom,
            "startTime": start_time,
            "endTime": end_time,
            "breakTime": break_time,
            "breaker": ob.breaker,
            "fillColor": ob.fill_color,
            "breakColor": ob.break_color if ob.breaker else None,
        }

    return {
        "bullish": [ob_to_primitive(ob) for ob in bullish_ob[:show_bull]],
        "bearish": [ob_to_primitive(ob) for ob in bearish_ob[:show_bear]],
    }

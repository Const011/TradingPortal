"""Order blocks from swing structure — LuxAlgo inspired.

This module only computes order blocks (OB creation and breaker marking for display).
Crossover detection and entry signals belong in the strategy layer.
"""

from dataclasses import dataclass
from typing import Any

from app.schemas.market import Candle

DEFAULT_SWING_LENGTH = 20
DEFAULT_SHOW_BULL = 5
DEFAULT_SHOW_BEAR = 5
MAX_LOOKBACK = 1000

# Valid order blocks: bullish = greenish, bearish = reddish
BULL_FILL = "rgba(34, 197, 94, 0.2)"
BEAR_FILL = "rgba(239, 68, 68, 0.15)"
# Breakers: bullish = violetish, bearish = yellowish
BULL_BREAKER_FILL = "rgba(139, 92, 246, 0.05)"
BEAR_BREAKER_FILL = "rgba(234, 179, 8, 0.05)"


@dataclass
class OrderBlock:
    top: float
    bottom: float
    loc: int
    formation_bar: int  # Bar index when OB was created (swing broken)
    breaker: bool
    break_loc: int | None
    fill_color: str


def _swings(
    candles: list[Candle],
    length: int,
    i: int,
    os_prev: int,
) -> tuple[float | None, int | None, float | None, int | None, int]:
    """
    Swing detection (Pine-inspired leg/oscillator logic).
    os=0 when bar[i-length] made a new high; os=1 when it made a new low.
    We emit a swing only when os *transitions* (e.g. new_swing_high = os_new==0 and os_prev!=0).
    Return (swing_high_price, swing_high_idx, swing_low_price, swing_low_idx, os_new).
    """
    if i < length + 1 or i >= len(candles):
        return None, None, None, None, os_prev

    # Lookback bar: bar at index (i - length) is checked against neighbors
    high_at_len = candles[i - length].high
    low_at_len = candles[i - length].low
    highest = max(candles[j].high for j in range(i - length + 1, i + 1))
    lowest = min(candles[j].low for j in range(i - length + 1, i + 1))

    os_new = 0 if high_at_len > highest else (1 if low_at_len < lowest else os_prev)

    # Only emit swing when os changes: new high requires prior low leg; new low requires prior high leg
    new_swing_high = os_new == 0 and os_prev != 0
    new_swing_low = os_new == 1 and os_prev != 1

    sh = (high_at_len, i - length) if new_swing_high else (None, None)
    sl = (low_at_len, i - length) if new_swing_low else (None, None)
    return sh[0], sh[1], sl[0], sl[1], os_new


def _iter_order_blocks(
    candles: list[Candle],
    swing_length: int = DEFAULT_SWING_LENGTH,
    use_body: bool = False,
    keep_breakers: bool = True,
) -> None:
    """
    Generator yielding per-bar OB state. Only computes OBs and marks breakers.
    Yields: (bar_index, candle, bullish_ob_list, bearish_ob_list).
    Crossover detection and entry signals belong in the strategy layer.
    """
    if len(candles) < swing_length + 2:
        return

    n = len(candles)
    bullish_ob: list[OrderBlock] = []
    bearish_ob: list[OrderBlock] = []
    top_crossed = False
    btm_crossed = False
    swing_top_y: float | None = None
    swing_top_x: int | None = None
    swing_btm_y: float | None = None
    swing_btm_x: int | None = None
    # os alternates: 0 = bearish leg (swing high), 1 = bullish leg (swing low). Start 1 so first swing can be high.
    os = 1

    for i in range(swing_length + 1, n):
        c = candles[i]
        h_hi = max(c.open, c.close) if use_body else c.high
        h_lo = min(c.open, c.close) if use_body else c.low

        sh_y, sh_x, sl_y, sl_x, os = _swings(candles, swing_length, i, os)

        # Update swing pivots when detected; reset crossed flags so we can form new OBs on next break
        if sh_y is not None and sh_x is not None:
            swing_top_y = sh_y
            swing_top_x = sh_x
            top_crossed = False
        if sl_y is not None and sl_x is not None:
            swing_btm_y = sl_y
            swing_btm_x = sl_x
            btm_crossed = False

        close = c.close

        # Bullish OB: form when price breaks *above* swing high. OB zone = (max high, min low) of bars between swing and current; we pick the bar with the lowest low.
        # No formation cap here — we form all, then take last show_bull within MAX_LOOKBACK at the end.
        if (swing_top_y is not None
          and swing_top_x is not None
          and close > swing_top_y
          and not top_crossed):
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
                bullish_ob.insert(0, OrderBlock(top=maxima, bottom=minima, loc=loc_bar, formation_bar=i, breaker=False, break_loc=None, fill_color=BULL_FILL))

        # Bearish OB: form when price breaks *below* swing low. OB zone = (max high, min low) of bars between swing and current; we pick the bar with the highest high.
        # No formation cap here — we form all, then take last show_bear within MAX_LOOKBACK at the end.
        if (swing_btm_y is not None
          and swing_btm_x is not None
          and close < swing_btm_y
          and not btm_crossed):
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
                bearish_ob.insert(0, OrderBlock(top=maxima, bottom=minima, loc=loc_bar, formation_bar=i, breaker=False, break_loc=None, fill_color=BEAR_FILL))

        # Mark bullish OBs as breakers when price crosses below (for display)
        for ob in list(bullish_ob):
            if not ob.breaker and ob.loc < i:
                if min(c.close, c.open) < ob.bottom:
                    ob.breaker = True
                    ob.break_loc = i
            elif not keep_breakers and c.close > ob.top:
                bullish_ob.remove(ob)

        # Mark bearish OBs as breakers when price crosses above (for display)
        for ob in list(bearish_ob):
            if not ob.breaker and ob.loc < i:
                if max(c.close, c.open) > ob.top:
                    ob.breaker = True
                    ob.break_loc = i
            elif not keep_breakers and c.close < ob.bottom:
                bearish_ob.remove(ob)

        yield (i, c, list(bullish_ob), list(bearish_ob))


def _iter_order_blocks_from_pivots(
    candles: list[Candle],
    swing_pivots: dict[str, list[dict[str, Any]]],
    *,
    use_body: bool = False,
    keep_breakers: bool = True,
) -> None:
    """
    Generator version of pivot-based OB computation.

    Yields the same per-bar state as `_iter_order_blocks`, but uses swing
    pivots from Smart Money structure instead of running its own swing
    detection. This is the single source of truth for OB formation logic;
    `compute_order_blocks` and strategy code should both rely on it.
    """
    n = len(candles)
    if n < 2:
        return

    highs_by_bar: dict[int, list[float]] = {}
    lows_by_bar: dict[int, list[float]] = {}

    # Swing highs/lows from structure
    for p in swing_pivots.get("highs", []):
        idx = int(p.get("bar_index", -1))
        price = float(p.get("price", 0.0))
        if 0 <= idx < n:
            highs_by_bar.setdefault(idx, []).append(price)
    for p in swing_pivots.get("lows", []):
        idx = int(p.get("bar_index", -1))
        price = float(p.get("price", 0.0))
        if 0 <= idx < n:
            lows_by_bar.setdefault(idx, []).append(price)

    # Internal highs/lows from structure (treated as additional pivots to form OBs)
    for p in swing_pivots.get("internalHighs", []):
        idx = int(p.get("bar_index", -1))
        price = float(p.get("price", 0.0))
        if 0 <= idx < n:
            highs_by_bar.setdefault(idx, []).append(price)
    for p in swing_pivots.get("internalLows", []):
        idx = int(p.get("bar_index", -1))
        price = float(p.get("price", 0.0))
        if 0 <= idx < n:
            lows_by_bar.setdefault(idx, []).append(price)

    bullish_ob: list[OrderBlock] = []
    bearish_ob: list[OrderBlock] = []
    swing_top_y: float | None = None
    swing_top_x: int | None = None
    swing_btm_y: float | None = None
    swing_btm_x: int | None = None
    top_crossed = False
    btm_crossed = False

    for i in range(n):
        c = candles[i]
        h_hi = max(c.open, c.close) if use_body else c.high
        h_lo = min(c.open, c.close) if use_body else c.low

        # Update active swing pivots from structure at this bar (may be none, one, or many)
        if i in highs_by_bar:
            # If multiple highs fall on the same bar, use the most recent one
            swing_top_y = highs_by_bar[i][-1]
            swing_top_x = i
            top_crossed = False
        if i in lows_by_bar:
            swing_btm_y = lows_by_bar[i][-1]
            swing_btm_x = i
            btm_crossed = False

        close = c.close

        # Bullish OB: form when price breaks *above* swing high (from structure pivots).
        if (
            swing_top_y is not None
            and swing_top_x is not None
            and close > swing_top_y
            and not top_crossed
        ):
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
                bullish_ob.insert(
                    0,
                    OrderBlock(
                        top=maxima,
                        bottom=minima,
                        loc=loc_bar,
                        formation_bar=i,
                        breaker=False,
                        break_loc=None,
                        fill_color=BULL_FILL,
                    ),
                )

        # Bearish OB: form when price breaks *below* swing low (from structure pivots).
        if (
            swing_btm_y is not None
            and swing_btm_x is not None
            and close < swing_btm_y
            and not btm_crossed
        ):
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
                bearish_ob.insert(
                    0,
                    OrderBlock(
                        top=maxima,
                        bottom=minima,
                        loc=loc_bar,
                        formation_bar=i,
                        breaker=False,
                        break_loc=None,
                        fill_color=BEAR_FILL,
                    ),
                )

        # Mark bullish OBs as breakers when price crosses below (for display)
        for ob in list(bullish_ob):
            if not ob.breaker and ob.loc < i:
                if min(close, c.open) < ob.bottom:
                    ob.breaker = True
                    ob.break_loc = i
            elif not keep_breakers and close > ob.top:
                bullish_ob.remove(ob)

        # Mark bearish OBs as breakers when price crosses above (for display)
        for ob in list(bearish_ob):
            if not ob.breaker and ob.loc < i:
                if max(close, c.open) > ob.top:
                    ob.breaker = True
                    ob.break_loc = i
            elif not keep_breakers and close < ob.bottom:
                bearish_ob.remove(ob)

        yield (i, c, list(bullish_ob), list(bearish_ob))


def _compute_order_blocks_from_pivots(
    candles: list[Candle],
    swing_pivots: dict[str, list[dict[str, Any]]],
    *,
    use_body: bool = False,
    keep_breakers: bool = True,
) -> tuple[list[OrderBlock], list[OrderBlock]]:
    """
    Build order blocks from precomputed swing pivots (from Smart Money structure).

    This is a thin wrapper around `_iter_order_blocks_from_pivots` that
    returns the final OB lists for graphics. All formation/breaker logic
    lives in the iterator above so strategy code can reuse it exactly.
    """
    bullish_ob: list[OrderBlock] = []
    bearish_ob: list[OrderBlock] = []

    for _i, _c, bull, bear in _iter_order_blocks_from_pivots(
        candles,
        swing_pivots,
        use_body=use_body,
        keep_breakers=keep_breakers,
    ):
        bullish_ob = bull
        bearish_ob = bear

    return bullish_ob, bearish_ob


def compute_order_blocks(
    candles: list[Candle],
    swing_length: int = DEFAULT_SWING_LENGTH,
    show_bull: int = DEFAULT_SHOW_BULL,
    show_bear: int = DEFAULT_SHOW_BEAR,
    use_body: bool = False,
    keep_breakers: bool = False,
    swing_pivots: dict[str, list[dict[str, Any]]] | None = None,
) -> dict:
    """
    Compute bullish and bearish order blocks from candle data.
    Returns dict with bullish/bearish lists of OB primitives for graphics.
    keep_breakers: if True (default), breaker OBs stay visible; if False, they are removed when price closes beyond.
    """
    if len(candles) < swing_length + 2:
        return {"bullish": [], "bearish": [], "bullishBreakers": [], "bearishBreakers": []}

    if swing_pivots:
        bullish_ob, bearish_ob = _compute_order_blocks_from_pivots(
            candles,
            swing_pivots,
            use_body=use_body,
            keep_breakers=keep_breakers,
        )
    else:
    # bullish_ob: list[OrderBlock] = []
    # bearish_ob: list[OrderBlock] = []
    # for _i, _c, bull, bear in _iter_order_blocks(
    #     candles, swing_length=swing_length, use_body=use_body, keep_breakers=keep_breakers
    # ):
    #     bullish_ob = bull
    #     bearish_ob = bear
        print ('ERROR: no swings')
        return {};

    n = len(candles)
    # Split into active (not crossed) vs breakers (crossed); keep within MAX_LOOKBACK
    # show_bull/show_bear=0 means return all; otherwise take most recent N
    last_bar = n - 1
    in_range = lambda ob: last_bar - ob.loc <= MAX_LOOKBACK

    bullish_in_range = [ob for ob in bullish_ob if in_range(ob) and not ob.breaker]
    bullish_breakers_in_range = [ob for ob in bullish_ob if in_range(ob) and ob.breaker]
    bearish_in_range = [ob for ob in bearish_ob if in_range(ob) and not ob.breaker]
    bearish_breakers_in_range = [ob for ob in bearish_ob if in_range(ob) and ob.breaker]

    bullish_active = bullish_in_range if show_bull <= 0 else bullish_in_range[:show_bull]
    bullish_breakers = bullish_breakers_in_range if show_bull <= 0 else bullish_breakers_in_range[:show_bull]
    bearish_active = bearish_in_range if show_bear <= 0 else bearish_in_range[:show_bear]
    bearish_breakers = bearish_breakers_in_range if show_bear <= 0 else bearish_breakers_in_range[:show_bear]

    def ob_to_primitive(ob: OrderBlock, fill: str) -> dict:
        loc_candle = candles[ob.loc]
        formation_candle = candles[ob.formation_bar]
        start_time = loc_candle.time // 1000
        end_time = candles[-1].time // 1000
        initiation_time = loc_candle.time // 1000  # Candle whose size defines OB top/bottom
        structure_break_time = formation_candle.time // 1000  # Bar that broke the swing
        breaker_time = candles[ob.break_loc].time // 1000 if ob.breaker and ob.break_loc is not None else None
        return {
            "top": ob.top,
            "bottom": ob.bottom,
            "startTime": start_time,
            "endTime": end_time,
            "initiationTime": initiation_time,
            "structureBreakTime": structure_break_time,
            "breakerTime": breaker_time,
            "breaker": ob.breaker,
            "fillColor": fill,
        }

    return {
        "bullish": [ob_to_primitive(ob, BULL_FILL) for ob in bullish_active],
        "bearish": [ob_to_primitive(ob, BEAR_FILL) for ob in bearish_active],
        "bullishBreakers": [ob_to_primitive(ob, BULL_BREAKER_FILL) for ob in bullish_breakers],
        "bearishBreakers": [ob_to_primitive(ob, BEAR_BREAKER_FILL) for ob in bearish_breakers],
    }

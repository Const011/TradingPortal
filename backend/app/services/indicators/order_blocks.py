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

# Order block strength parameters:
# N = bars in the "impulse window" including the structure-breaking candle.
# Strength index is the **raw sum of volumes** over this window.
DEFAULT_OB_STRENGTH_N = 2

# Valid order blocks: bullish = greenish, bearish = reddish
BULL_FILL = "rgba(34, 197, 94, 0.2)"
BEAR_FILL = "rgba(239, 68, 68, 0.15)"
# Breakers: bullish = violetish, bearish = yellowish
BULL_BREAKER_FILL = "rgba(139, 92, 246, 0.05)"
BEAR_BREAKER_FILL = "rgba(234, 179, 8, 0.05)"


DEFAULT_KEEP_BREAKERS = False

@dataclass
class OrderBlock:
    top: float
    bottom: float
    loc: int
    formation_bar: int  # Bar index when OB was created (swing broken)
    breaker: bool
    break_loc: int | None
    fill_color: str
    strength_index: float = 0.0


def _compute_ob_strength(
    candles: list[Candle],
    pivot_bar: int,
    n_window: int = DEFAULT_OB_STRENGTH_N,
) -> float:
    """
    Strength index for an order block, based on **raw impulse volume**:
    - Window A: N-bar window ending at the pivot bar (inclusive).
    Strength index = sum(volume over window A).
    Returns 0.0 when insufficient history.

    NOTE: `m_window` is kept for backward compatibility but no longer used.
    """
    n = len(candles)
    if n == 0 or pivot_bar < 0 or pivot_bar >= n:
        return 0.0
    n_window = max(1, n_window)

    # Window A: [start_a, pivot_bar]
    start_a = max(0, pivot_bar - n_window + 1)
    end_a = pivot_bar
    if start_a > end_a:
        return 0.0
    vol_sum = sum(candles[i].volume for i in range(start_a, end_a + 1))
    return float(vol_sum)


def _iter_order_blocks_from_pivots(
    candles: list[Candle],
    swing_pivots: dict[str, list[dict[str, Any]]],
    *,
    use_body: bool = False,
    keep_breakers: bool = DEFAULT_KEEP_BREAKERS,
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
                # Strength should be tied to the initiation candle (loc_bar), not the break candle.
                strength = _compute_ob_strength(candles, loc_bar)
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
                        strength_index=strength,
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
                # Strength should be tied to the initiation candle (loc_bar), not the break candle.
                strength = _compute_ob_strength(candles, loc_bar)
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
                        strength_index=strength,
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
    keep_breakers: bool = DEFAULT_KEEP_BREAKERS,
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
    keep_breakers: bool = DEFAULT_KEEP_BREAKERS,
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
            "strengthIndex": ob.strength_index,
        }

    return {
        "bullish": [ob_to_primitive(ob, BULL_FILL) for ob in bullish_active],
        "bearish": [ob_to_primitive(ob, BEAR_FILL) for ob in bearish_active],
        "bullishBreakers": [ob_to_primitive(ob, BULL_BREAKER_FILL) for ob in bullish_breakers],
        "bearishBreakers": [ob_to_primitive(ob, BEAR_BREAKER_FILL) for ob in bearish_breakers],
    }

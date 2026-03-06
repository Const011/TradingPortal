"""Order Block Trend-Following strategy. See docs/strategy-order-block-trend-following.md."""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from app.schemas.market import Candle

logger = logging.getLogger(__name__)

# Temporary debug: log bullish signal steps for bars around 2026-03-02 17:00
_DEBUG_TS_START = int(datetime(2025, 3, 2, 15, 0).timestamp() * 1000)
_DEBUG_TS_END = int(datetime(2025, 3, 2, 19, 0).timestamp() * 1000)

from app.utils.timefmt import ts_human

from app.services.indicators.order_blocks import (
    OrderBlock,
    _iter_order_blocks_from_pivots,
    _compute_order_blocks_from_pivots,
)
from app.services.trading_strategy.types import TradeEvent, StopSegment

logger = logging.getLogger(__name__)

# Candle colors from smart_money_structure: only when BOTH swing AND internal agree
# #22c55e = swing bullish + internal bullish; #dc2626 = swing bearish + internal bearish
#BULLISH_COLORS = {"#22c55e"} # both in bullish
#BEARISH_COLORS = {"#dc2626"} # both in bearish
BULLISH_COLORS = {"#15803d","#22c55e", "#b91c1c",}  # when swing trend bullish
BEARISH_COLORS = {"#b91c1c","#dc2626", "#15803d",}  # when swing trend bearish


# Default parameters
DEFAULT_ENTRY_ZONE_MULT = 1.0  # Used by strategy for crossover detection
DEFAULT_MAX_OB_ENTRY_SIGNALS = 2  # Used by strategy to cap actual trade entries per OB (not boundary crosses)
DEFAULT_VOLUME_SPIKE_MULT = 1.5
DEFAULT_VOLUME_CONFIRMATION_LOOKBACK = 10  # Bars for volume avg in confirmation (volume > mult × avg)
DEFAULT_CONSECUTIVE_CLOSES = 2
DEFAULT_TRAIL_CONSECUTIVE_CLOSES = 2
DEFAULT_BLOCK_OB_DISTANCE_MULT = 1.0
DEFAULT_BLOCK_SR_DISTANCE_MULT = 1.0
DEFAULT_MIN_SR_STRENGTH = 4.0
DEFAULT_TRAIL_SR_MIN_STRENGTH = 0.0  # Include all S/R for trailing; min_sr_strength only for blocking
DEFAULT_TRAIL_PARAM = 0.8
DEFAULT_ATR_LENGTH = 14
DEFAULT_ATR_STOP_MULT = 2.0
DEFAULT_BREAKEVEN_BODY_FRAC = 0.1  # Trail toward open + N*(close-open); 0 = disabled
DEFAULT_WARMUP_BARS = 1000

DEFAULT_MIN_OB_STRENGTH = 0.75


@dataclass
class _ActivePosition:
    """In-position state for trailing stop."""

    side: str
    entry_price: float
    entry_bar: int
    stop_price: float
    trigger_ob_top: float
    trigger_ob_bottom: float


@dataclass
class _EntryCandidate:
    """Pending entry signal for the current bar (used to support reversals)."""

    side: str
    ob_top: float
    ob_bottom: float
    ob_formation_bar: int
    stop: float
    ob_key: tuple[float, float, int]


def _is_bullish_trend(candle_colors: dict[int, str] | None, time_ms: int) -> bool:
    if not candle_colors:
        return True  # Default to allow if no colors
    c = candle_colors.get(time_ms, "")
    return c in BULLISH_COLORS


def _is_bearish_trend(candle_colors: dict[int, str] | None, time_ms: int) -> bool:
    if not candle_colors:
        return True
    c = candle_colors.get(time_ms, "")
    return c in BEARISH_COLORS


def _atr(candles: list[Candle], length: int, up_to: int) -> float:
    """RMA of true range. Returns 0 if insufficient data."""
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


def _volume_average(candles: list[Candle], lookback: int = 20, up_to: int | None = None) -> float:
    end = up_to if up_to is not None else len(candles)
    start = max(0, end - lookback)
    if start >= end:
        return 0.0
    return sum(candles[i].volume for i in range(start, end)) / (end - start)


def _get_closest_support_below(
    sr_lines: list[dict],
    price: float,
    min_strength: float,
) -> tuple[float, float] | None:
    """Return (price, width) of closest support below price with strength >= min_strength, or None."""
    candidates = [
        (line["price"], line.get("width", 1.0))
        for line in sr_lines
        if line["price"] < price and line.get("width", 0) >= min_strength
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])  # Closest = highest below price


def _get_closest_resistance_above(
    sr_lines: list[dict],
    price: float,
    min_strength: float,
) -> tuple[float, float] | None:
    candidates = [
        (line["price"], line.get("width", 1.0))
        for line in sr_lines
        if line["price"] > price and line.get("width", 0) >= min_strength
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])  # Closest = lowest above price


def _get_closest_bearish_ob_below(
    bearish_ob: list[OrderBlock],
    price: float,
    *,
    active_only: bool = True,
) -> float | None:
    """Closest bearish OB top below price. active_only=True excludes breakers (only block on strong bearish OB)."""
    obs = [ob for ob in bearish_ob if not ob.breaker] if active_only else bearish_ob
    tops_below = [ob.top for ob in obs if ob.top < price]
    return max(tops_below) if tops_below else None


def _get_closest_bullish_ob_above(
    bullish_ob: list[OrderBlock],
    price: float,
    *,
    active_only: bool = True,
) -> float | None:
    """Closest bullish OB bottom above price. active_only=True excludes breakers (only block on strong bullish OB)."""
    obs = [ob for ob in bullish_ob if not ob.breaker] if active_only else bullish_ob
    bottoms_above = [ob.bottom for ob in obs if ob.bottom > price]
    return min(bottoms_above) if bottoms_above else None


def _compute_initial_stop_long(
    ob_bottom: float,
    sr_lines: list[dict],
    entry_price: float,
    min_strength: float,
    candles: list[Candle] | None = None,
    bar_index: int = 0,
    atr_length: int = DEFAULT_ATR_LENGTH,
    atr_stop_mult: float = DEFAULT_ATR_STOP_MULT,
) -> float:
    """Higher of OB bottom or support-gap (tighter = better for long). Optionally cap by ATR."""
    support = _get_closest_support_below(sr_lines, entry_price, min_strength)
    if support is None:
        structural = ob_bottom
    else:
        support_price = support[0]
        gap = (entry_price - support_price) / 2
        stop_below_support = support_price - gap
        structural = max(ob_bottom, stop_below_support)
    if atr_stop_mult > 0 and candles and bar_index >= atr_length:
        atr_val = _atr(candles, atr_length, bar_index)
        if atr_val > 0:
            atr_cap = entry_price - atr_stop_mult * atr_val
            structural = max(structural, atr_cap)
    # Mandatory: stop cannot be higher than entry candle low - 1 (must be below the bar's low)
    if candles and 0 <= bar_index < len(candles):
        max_stop = candles[bar_index].low - 1.0
        structural = min(structural, max_stop)
    return structural


def _compute_initial_stop_short(
    ob_top: float,
    sr_lines: list[dict],
    entry_price: float,
    min_strength: float,
    candles: list[Candle] | None = None,
    bar_index: int = 0,
    atr_length: int = DEFAULT_ATR_LENGTH,
    atr_stop_mult: float = DEFAULT_ATR_STOP_MULT,
) -> float:
    """Lower of OB top or resistance+gap (tighter = better for short). Optionally cap by ATR."""
    resistance = _get_closest_resistance_above(sr_lines, entry_price, min_strength)
    if resistance is None:
        structural = ob_top
    else:
        res_price = resistance[0]
        gap = (res_price - entry_price) / 2
        stop_above_res = res_price + gap
        structural = min(ob_top, stop_above_res)
    if atr_stop_mult > 0 and candles and bar_index >= atr_length:
        atr_val = _atr(candles, atr_length, bar_index)
        if atr_val > 0:
            atr_cap = entry_price + atr_stop_mult * atr_val
            structural = min(structural, atr_cap)
    # Mandatory: stop cannot be lower than entry candle high + 1 (must be above the bar's high)
    if candles and 0 <= bar_index < len(candles):
        min_stop = candles[bar_index].high + 1.0
        structural = max(structural, min_stop)
    return structural


def _confirmed_level_cross_long(
    candles: list[Candle],
    bar_index: int,
    prev_candle: Candle | None,
    levels: list[float],
    current_stop: float,
    volume_spike_mult: float,
    consecutive_closes: int,
    vol_lookback: int,
) -> float | None:
    """
    Return highest level above current_stop that is confirmed by either:
    - Option A: One bar with close above level AND volume spike.
    - Option B: N consecutive bars closed above the level.
    """
    if bar_index < 0:
        return None
    c = candles[bar_index]
    vol_avg = _volume_average(candles, vol_lookback, bar_index + 1)
    has_vol_spike = vol_avg > 0 and c.volume >= volume_spike_mult * vol_avg

    candidates: list[float] = []
    for L in levels:
        if L <= current_stop:
            continue
        # Must be above level: close > L for long
        if c.close <= L:
            continue

        # Option A: one bar with close above level and volume spike
        if has_vol_spike:
            candidates.append(L)
            continue

        # Option B: N consecutive bars closed above the level
        start = max(0, bar_index - consecutive_closes + 1)
        if bar_index - start + 1 < consecutive_closes:
            continue
        all_above = all(candles[j].close > L for j in range(start, bar_index + 1))
        if all_above:
            candidates.append(L)

    return max(candidates) if candidates else None


def _confirmed_level_cross_short(
    candles: list[Candle],
    bar_index: int,
    prev_candle: Candle | None,
    levels: list[float],
    current_stop: float,
    volume_spike_mult: float,
    consecutive_closes: int,
    vol_lookback: int,
) -> float | None:
    """
    Return lowest level below current_stop that is confirmed by either:
    - Option A: One bar with close below level AND volume spike.
    - Option B: N consecutive bars closed below the level.
    """
    if bar_index < 0:
        return None
    c = candles[bar_index]
    vol_avg = _volume_average(candles, vol_lookback, bar_index + 1)
    has_vol_spike = vol_avg > 0 and c.volume >= volume_spike_mult * vol_avg

    candidates: list[float] = []
    for L in levels:
        if L >= current_stop:
            continue
        if c.close >= L:
            continue

        # Option A: one bar with close below level and volume spike
        if has_vol_spike:
            candidates.append(L)
            continue

        # Option B: N consecutive bars closed below the level
        start = max(0, bar_index - consecutive_closes + 1)
        if bar_index - start + 1 < consecutive_closes:
            continue
        all_below = all(candles[j].close < L for j in range(start, bar_index + 1))
        if all_below:
            candidates.append(L)

    return min(candidates) if candidates else None


def _detect_ob_events(
    i: int,
    c: Candle,
    candles: list[Candle],
    bullish_ob: list[OrderBlock],
    bearish_ob: list[OrderBlock],
    entry_zone_mult: float,
) -> list[dict]:
    """
    Detect boundary crosses and breaker-created events for strategy triggers.
    All crossover/entry logic lives in the strategy layer.
    Event emission is not capped; the strategy caps actual trade entries per OB.
    """
    events: list[dict] = []
    emitted_boundary: set[tuple[float, float, int]] = set()
    _debug = _DEBUG_TS_START <= c.time <= _DEBUG_TS_END

    def ob_key(ob: OrderBlock) -> tuple[float, float, int]:
        return (ob.top, ob.bottom, ob.formation_bar)

    # Bullish: boundary cross and breaker created
    for ob in bullish_ob:
        if not ob.breaker and ob.loc < i:
            wick_below = min(c.close, c.open) < ob.bottom
            if _debug:
                logger.info(
                    "[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | current_bar c: min(co,cl)=%.1f ob.bottom=%.1f wick_below=%s",
                    i, ob.top, ob.bottom, min(c.close, c.open), ob.bottom, wick_below,
                )
            if wick_below:
                if ob.formation_bar == i:
                    emitted_boundary.add(ob_key(ob))  # Prevent second loop from emitting boundary for this OB
                events.append({"type": "bullish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar})
                if _debug:
                    logger.info("[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | EMIT breaker_created (current bar)", i, ob.top, ob.bottom)
            else:
                ob_height = ob.top - ob.bottom
                zone_top = ob.bottom + entry_zone_mult * ob_height
                touched_zone = c.low <= zone_top and c.high >= ob.bottom
                close_above = c.close > ob.top and c.close > c.open
                if _debug:
                    logger.info(
                        "[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | current_bar: zone_top=%.1f c.low=%.1f c.high=%.1f ob.bottom=%.1f "
                        "touched_zone=%s | c.close=%.1f ob.top=%.1f c.open=%.1f close_above=%s",
                        i, ob.top, ob.bottom, zone_top, c.low, c.high, ob.bottom, touched_zone,
                        c.close, ob.top, c.open, close_above,
                    )
                if touched_zone and close_above:
                    emitted_boundary.add(ob_key(ob))
                    events.append({"type": "bullish_boundary_crossed", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar})
                    if _debug:
                        logger.info("[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | EMIT boundary_crossed (current bar i)", i, ob.top, ob.bottom)
                elif _debug and not (touched_zone and close_above):
                    logger.info(
                        "[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | NO EMIT: touched_zone=%s close_above=%s",
                        i, ob.top, ob.bottom, touched_zone, close_above,
                    )
        elif ob.breaker and ob.break_loc == i:
            events.append({"type": "bullish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar})
            if _debug:
                logger.info("[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | EMIT breaker_created (break_loc==i)", i, ob.top, ob.bottom)
        elif _debug and (ob.loc >= i or (ob.breaker and ob.break_loc != i)):
            logger.info(
                "[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | SKIP first loop: ob.loc>=i or (breaker and break_loc!=i)",
                i, ob.top, ob.bottom,
            )

    # When OB is newly created on bar i, the current bar is the structure-breaking bar — emit for bar i.
    # (No previous-bar workaround: structure runs first, so OB forms on same bar as break.)
    for ob in bullish_ob:
        if ob.formation_bar == i and ob_key(ob) not in emitted_boundary:
            emitted_boundary.add(ob_key(ob))
            events.append({
                "type": "bullish_boundary_crossed",
                "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar,
                "trigger_bar": i,
            })
            if _debug:
                logger.info(
                    "[OB_EVENTS] bar=%d BULL ob=[%.1f,%.1f] | EMIT boundary_crossed (newly formed, trigger_bar=i)",
                    i, ob.top, ob.bottom,
                )

    emitted_bearish_boundary: set[tuple[float, float, int]] = set()

    # Bearish: boundary cross and breaker created
    for ob in bearish_ob:
        if not ob.breaker and ob.loc < i:
            wick_above = max(c.close, c.open) > ob.top
            if _debug:
                logger.info(
                    "[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | current_bar c: max(co,cl)=%.1f ob.top=%.1f wick_above=%s",
                    i, ob.top, ob.bottom, max(c.close, c.open), ob.top, wick_above,
                )
            if wick_above:
                if ob.formation_bar == i:
                    emitted_bearish_boundary.add(ob_key(ob))  # Prevent second loop from emitting boundary for this OB
                events.append({"type": "bearish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar})
                if _debug:
                    logger.info("[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | EMIT breaker_created (current bar)", i, ob.top, ob.bottom)
            else:
                ob_height = ob.top - ob.bottom
                zone_bottom = ob.top - entry_zone_mult * ob_height
                touched_zone = c.high >= zone_bottom and c.low <= ob.top
                close_below = c.close < ob.bottom and c.close < c.open
                if _debug:
                    logger.info(
                        "[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | current_bar: zone_bottom=%.1f c.high=%.1f c.low=%.1f ob.top=%.1f "
                        "touched_zone=%s | c.close=%.1f ob.bottom=%.1f c.open=%.1f close_below=%s",
                        i, ob.top, ob.bottom, zone_bottom, c.high, c.low, ob.top, touched_zone,
                        c.close, ob.bottom, c.open, close_below,
                    )
                if touched_zone and close_below:
                    emitted_bearish_boundary.add(ob_key(ob))
                    events.append({"type": "bearish_boundary_crossed", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar})
                    if _debug:
                        logger.info("[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | EMIT boundary_crossed (current bar i)", i, ob.top, ob.bottom)
                elif _debug and not (touched_zone and close_below):
                    logger.info(
                        "[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | NO EMIT: touched_zone=%s close_below=%s",
                        i, ob.top, ob.bottom, touched_zone, close_below,
                    )
        elif ob.breaker and ob.break_loc == i:
            events.append({"type": "bearish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar})
            if _debug:
                logger.info("[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | EMIT breaker_created (break_loc==i)", i, ob.top, ob.bottom)
        elif _debug and (ob.loc >= i or (ob.breaker and ob.break_loc != i)):
            logger.info(
                "[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | SKIP first loop: ob.loc>=i or (breaker and break_loc!=i)",
                i, ob.top, ob.bottom,
            )

    # When bearish OB newly created, emit for current bar (structure-breaking bar).
    for ob in bearish_ob:
        if ob.formation_bar == i and ob_key(ob) not in emitted_bearish_boundary:
            emitted_bearish_boundary.add(ob_key(ob))
            events.append({
                "type": "bearish_boundary_crossed",
                "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc, "ob_formation_bar": ob.formation_bar,
                "trigger_bar": i,
            })
            if _debug:
                logger.info(
                    "[OB_EVENTS] bar=%d BEAR ob=[%.1f,%.1f] | EMIT boundary_crossed (newly formed, trigger_bar=i)",
                    i, ob.top, ob.bottom,
                )

    if _debug and events:
        for ev in events:
            trigger_bar = ev.get("trigger_bar", i)
            logger.info(
                "[OB_EVENTS] bar=%d time=%s | RESULT: %s ob=[%.1f,%.1f] trigger_bar=%s (i=%d)",
                i, ts_human(c.time), ev["type"], ev["ob_top"], ev["ob_bottom"],
                trigger_bar, i,
            )

    return events


def compute_order_block_trend_following(
    candles: list[Candle],
    swing_pivots: dict[str, list[dict]],
    candle_colors: dict[int, str] | None = None,
    sr_lines: list[dict] | None = None,
    *,
    entry_zone_mult: float = DEFAULT_ENTRY_ZONE_MULT,  # from order_blocks
    volume_spike_mult: float = DEFAULT_VOLUME_SPIKE_MULT,
    volume_confirmation_lookback: int = DEFAULT_VOLUME_CONFIRMATION_LOOKBACK,
    consecutive_closes: int = DEFAULT_CONSECUTIVE_CLOSES,
    trail_consecutive_closes: int = DEFAULT_TRAIL_CONSECUTIVE_CLOSES,
    min_sr_strength: float = DEFAULT_MIN_SR_STRENGTH,
    trail_sr_min_strength: float = DEFAULT_TRAIL_SR_MIN_STRENGTH,
    trail_param: float = DEFAULT_TRAIL_PARAM,
    max_ob_entry_signals: int = DEFAULT_MAX_OB_ENTRY_SIGNALS,
    atr_length: int = DEFAULT_ATR_LENGTH,
    atr_stop_mult: float = DEFAULT_ATR_STOP_MULT,
    breakeven_body_frac: float = DEFAULT_BREAKEVEN_BODY_FRAC,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    min_ob_strength: float = DEFAULT_MIN_OB_STRENGTH,
) -> tuple[list[TradeEvent], list[StopSegment]]:
    """
    Run Order Block Trend-Following strategy.
    Returns (trade_events, stop_segments).
    """
    if not candles or len(candles) < 25:
        return [], []

    sr_lines = sr_lines or []
    events: list[TradeEvent] = []
    stop_segments: list[StopSegment] = []
    position: _ActivePosition | None = None
    vol_lookback = 20
    ob_entry_counts: dict[tuple[float, float, int], int] = {}  # Count actual trades per OB, not crosses
    events_history: deque[tuple[int, list[dict]]] = deque(maxlen=consecutive_closes)

    # Precompute global average OB strength (bullish + bearish) for relative filtering.
    all_bull, all_bear = _compute_order_blocks_from_pivots(
        candles,
        swing_pivots,
        keep_breakers=True,
    )
    all_obs: list[OrderBlock] = [*all_bull, *all_bear]
    avg_strength = (
        sum(ob.strength_index for ob in all_obs) / len(all_obs)
        if all_obs
        else 0.0
    )
    strength_threshold = (
        min_ob_strength * avg_strength if avg_strength > 0.0 and min_ob_strength > 0.0 else 0.0
    )

    # Use the same pivot-based OB engine as the indicator, driven by Smart Money
    # structure pivots passed in from `compute_structure`. This ensures the
    # strategy sees exactly the same OB topology as the graphics layer.
    for i, c, bullish_ob, bearish_ob in _iter_order_blocks_from_pivots(
        candles,
        swing_pivots,
        keep_breakers=True,
    ):
        # Relative strength filter: keep only OBs whose strength is above
        # (min_ob_strength × average strength) across all identified blocks.
        if strength_threshold > 0.0:
            bullish_ob = [ob for ob in bullish_ob if ob.strength_index >= strength_threshold]
            bearish_ob = [ob for ob in bearish_ob if ob.strength_index >= strength_threshold]

        raw_events = _detect_ob_events(
            i, c, candles, bullish_ob, bearish_ob,
            entry_zone_mult=entry_zone_mult,
        )
        events_history.append((i, raw_events))
        time_s = c.time // 1000
        vol_avg = _volume_average(candles, vol_lookback, i + 1)
        is_bull = _is_bullish_trend(candle_colors, c.time)
        is_bear = _is_bearish_trend(candle_colors, c.time)
        _debug = _DEBUG_TS_START <= c.time <= _DEBUG_TS_END
        if _debug and raw_events:
            logger.info(
                "[OB_STRAT] bar=%d time=%s | raw_events=%d types=%s",
                i, ts_human(c.time), len(raw_events), [e["type"] for e in raw_events],
            )

        # --- Stop-hit check for existing position (uses stop defined on previous bar, before new entries/reversals). ---
        # Position open price = entry bar close (we enter on bar close when conditions met)
        if position and prev_candle is not None:
            if position.side == "long":
                if _debug and c.low <= position.stop_price:
                    logger.info(
                        "[OB_STOP_HIT_LONG] bar=%d time=%s | low=%.1f stop=%.1f entry=%.1f",
                        i,
                        ts_human(c.time),
                        c.low,
                        position.stop_price,
                        position.entry_price,
                    )
                if c.low <= position.stop_price:
                    if stop_segments and stop_segments[-1].side == "long":
                        last = stop_segments[-1]
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            price=last.price,
                            side="long",
                        )
                    position = None
            else:
                if _debug and c.high >= position.stop_price:
                    logger.info(
                        "[OB_STOP_HIT_SHORT] bar=%d time=%s | high=%.1f stop=%.1f entry=%.1f",
                        i,
                        ts_human(c.time),
                        c.high,
                        position.stop_price,
                        position.entry_price,
                    )
                if c.high >= position.stop_price:
                    if stop_segments and stop_segments[-1].side == "short":
                        last = stop_segments[-1]
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            price=last.price,
                            side="short",
                        )
                    position = None

        # --- Entry window: OB + volume over last N bars; allow reversal (close + open opposite). ---
        if _debug and len(events_history) < consecutive_closes:
            logger.info(
                "[OB_STRAT] bar=%d time=%s | SKIP entry window: len_history=%d < consecutive_closes=%d",
                i, ts_human(c.time), len(events_history), consecutive_closes,
            )
        # Do not generate new entries during warmup period (first warmup_bars indices).
        if len(events_history) >= consecutive_closes and i >= warmup_bars:
            if _debug:
                logger.info(
                    "[OB_STRAT] bar=%d time=%s | entry window check: len_history=%d position=%s warmup_bars=%d",
                    i, ts_human(c.time), len(events_history), position, warmup_bars,
                )
            # Collect OBs that had events in the last N bars
            bullish_obs: set[tuple[float, float, int]] = set()
            bearish_obs: set[tuple[float, float, int]] = set()
            for bar_idx, ev_list in events_history:
                for ev in ev_list:
                    t = ev["type"]
                    ob_top, ob_bottom = ev["ob_top"], ev["ob_bottom"]
                    ob_formation_bar = ev.get("ob_formation_bar", bar_idx)
                    ob_key = (ob_top, ob_bottom, ob_formation_bar)
                    if t in ("bullish_boundary_crossed", "bullish_breaker_created"):
                        bullish_obs.add(ob_key)
                    elif t in ("bearish_boundary_crossed", "bearish_breaker_created"):
                        bearish_obs.add(ob_key)
            if _debug:
                logger.info(
                    "[OB_STRAT] bar=%d time=%s | OBs in history: bullish=%d bearish=%d",
                    i, ts_human(c.time), len(bullish_obs), len(bearish_obs),
                )

            def _cond2_vol_spike(bar_idx: int, side: str) -> bool:
                cj = candles[bar_idx]
                vol_avg_j = _volume_average(candles, volume_confirmation_lookback, bar_idx + 1)
                if vol_avg_j <= 0:
                    return False
                if side == "long":
                    return cj.close > cj.open and cj.volume >= volume_spike_mult * vol_avg_j
                return cj.close < cj.open and cj.volume >= volume_spike_mult * vol_avg_j

            long_candidate: _EntryCandidate | None = None
            short_candidate: _EntryCandidate | None = None

            for ob_top, ob_bottom, ob_formation_bar in bullish_obs:
                ob_key = (ob_top, ob_bottom, ob_formation_bar)
                if ob_entry_counts.get(ob_key, 0) >= max_ob_entry_signals:
                    if _debug:
                        logger.info(
                            "[OB_STRAT_LONG] bar=%d time=%s ob=[%.1f,%.1f] | SKIP: entry cap (count=%d >= %d)",
                            i, ts_human(c.time), ob_top, ob_bottom,
                            ob_entry_counts.get(ob_key, 0), max_ob_entry_signals,
                        )
                    continue
                ob_width = ob_top - ob_bottom
                c1 = True  # OB event in history
                c2_bars = [bar_idx for bar_idx, _ in events_history if _cond2_vol_spike(bar_idx, "long")]
                c2 = len(c2_bars) > 0
                if _debug:
                    logger.info(
                        "[OB_STRAT_LONG] bar=%d time=%s ob=[%.1f,%.1f] | c1=%s c2=%s (vol_spike_bars=%s) close=%.1f",
                        i, ts_human(c.time), ob_top, ob_bottom,
                        c1, c2, c2_bars, c.close,
                    )
                if not (c1 and c2):
                    continue
                entry = c.close
                stop = _compute_initial_stop_long(
                    ob_bottom, sr_lines, entry, min_sr_strength,
                    candles=candles, bar_index=i, atr_length=atr_length, atr_stop_mult=atr_stop_mult,
                )
                long_candidate = _EntryCandidate(
                    side="long",
                    ob_top=ob_top,
                    ob_bottom=ob_bottom,
                    ob_formation_bar=ob_formation_bar,
                    stop=stop,
                    ob_key=ob_key,
                )
                break

            for ob_top, ob_bottom, ob_formation_bar in bearish_obs:
                ob_key = (ob_top, ob_bottom, ob_formation_bar)
                if ob_entry_counts.get(ob_key, 0) >= max_ob_entry_signals:
                    if _debug:
                        logger.info(
                            "[OB_STRAT_SHORT] bar=%d time=%s ob=[%.1f,%.1f] | SKIP: entry cap (count=%d >= %d)",
                            i, ts_human(c.time), ob_top, ob_bottom,
                            ob_entry_counts.get(ob_key, 0), max_ob_entry_signals,
                        )
                    continue
                ob_width = ob_top - ob_bottom
                c1 = True
                c2_bars = [bar_idx for bar_idx, _ in events_history if _cond2_vol_spike(bar_idx, "short")]
                c2 = len(c2_bars) > 0
                if _debug:
                    logger.info(
                        "[OB_STRAT_SHORT] bar=%d time=%s ob=[%.1f,%.1f] | c1=%s c2=%s (vol_spike_bars=%s) close=%.1f",
                        i, ts_human(c.time), ob_top, ob_bottom,
                        c1, c2, c2_bars, c.close,
                    )
                if not (c1 and c2):
                    continue
                entry = c.close
                stop = _compute_initial_stop_short(
                    ob_top, sr_lines, entry, min_sr_strength,
                    candles=candles, bar_index=i, atr_length=atr_length, atr_stop_mult=atr_stop_mult,
                )
                short_candidate = _EntryCandidate(
                    side="short",
                    ob_top=ob_top,
                    ob_bottom=ob_bottom,
                    ob_formation_bar=ob_formation_bar,
                    stop=stop,
                    ob_key=ob_key,
                )
                break

            # Apply entry or reversal: flat -> open one side; in position -> reverse if opposite signal.
            current_side = position.side if position is not None else None

            def _open_from_candidate(candidate: _EntryCandidate) -> None:
                nonlocal position
                entry_price = c.close
                if candidate.side == "long":
                    if _debug:
                        logger.info(
                            "[OB_STRAT_LONG] bar=%d time=%s | ENTRY LONG ob=[%.1f,%.1f] price=%.1f (reversal_from=%s)",
                            i, ts_human(c.time), candidate.ob_top, candidate.ob_bottom, entry_price, current_side,
                        )
                    events.append(
                        TradeEvent(
                            time=time_s,
                            bar_index=i,
                            type="OB_TREND_BUY",
                            side="long",
                            price=entry_price,
                            target_price=None,
                            initial_stop_price=candidate.stop,
                            context={
                                "ob_top": candidate.ob_top,
                                "ob_bottom": candidate.ob_bottom,
                                "trigger": "entry_window",
                                "reversal_from": current_side,
                            },
                        )
                    )
                    position = _ActivePosition(
                        side="long",
                        entry_price=entry_price,
                        entry_bar=i,
                        stop_price=candidate.stop,
                        trigger_ob_top=candidate.ob_top,
                        trigger_ob_bottom=candidate.ob_bottom,
                    )
                    stop_segments.append(
                        StopSegment(start_time=time_s, end_time=time_s, price=candidate.stop, side="long")
                    )
                    if _debug:
                        entry_candle = candles[position.entry_bar]
                        guard_low = entry_candle.low - 1.0
                        violates_guard = position.stop_price > guard_low
                        logger.info(
                            "[OB_STOP_INIT_LONG] bar=%d time=%s | entry=%.1f stop=%.1f low=%.1f guard_low=%.1f violates_guard=%s",
                            i,
                            ts_human(c.time),
                            position.entry_price,
                            position.stop_price,
                            entry_candle.low,
                            guard_low,
                            violates_guard,
                        )
                else:
                    if _debug:
                        logger.info(
                            "[OB_STRAT_SHORT] bar=%d time=%s | ENTRY SHORT ob=[%.1f,%.1f] price=%.1f (reversal_from=%s)",
                            i, ts_human(c.time), candidate.ob_top, candidate.ob_bottom, entry_price, current_side,
                        )
                    events.append(
                        TradeEvent(
                            time=time_s,
                            bar_index=i,
                            type="OB_TREND_SELL",
                            side="short",
                            price=entry_price,
                            target_price=None,
                            initial_stop_price=candidate.stop,
                            context={
                                "ob_top": candidate.ob_top,
                                "ob_bottom": candidate.ob_bottom,
                                "trigger": "entry_window",
                                "reversal_from": current_side,
                            },
                        )
                    )
                    position = _ActivePosition(
                        side="short",
                        entry_price=entry_price,
                        entry_bar=i,
                        stop_price=candidate.stop,
                        trigger_ob_top=candidate.ob_top,
                        trigger_ob_bottom=candidate.ob_bottom,
                    )
                    stop_segments.append(
                        StopSegment(start_time=time_s, end_time=time_s, price=candidate.stop, side="short")
                    )
                    if _debug:
                        entry_candle = candles[position.entry_bar]
                        guard_high = entry_candle.high + 1.0
                        violates_guard = position.stop_price < guard_high
                        logger.info(
                            "[OB_STOP_INIT_SHORT] bar=%d time=%s | entry=%.1f stop=%.1f high=%.1f guard_high=%.1f violates_guard=%s",
                            i,
                            ts_human(c.time),
                            position.entry_price,
                            position.stop_price,
                            entry_candle.high,
                            guard_high,
                            violates_guard,
                        )
                ob_entry_counts[candidate.ob_key] = ob_entry_counts.get(candidate.ob_key, 0) + 1

            if current_side is None:
                chosen: _EntryCandidate | None = None
                if long_candidate and not short_candidate:
                    chosen = long_candidate
                elif short_candidate and not long_candidate:
                    chosen = short_candidate
                elif long_candidate and short_candidate:
                    if is_bull and not is_bear:
                        chosen = long_candidate
                    elif is_bear and not is_bull:
                        chosen = short_candidate
                    else:
                        chosen = long_candidate

                # New blocking condition: swing trend must align with entry direction.
                if chosen is not None:
                    if chosen.side == "long" and not is_bull:
                        if _debug:
                            logger.info(
                                "[OB_STRAT_LONG] bar=%d time=%s | BLOCKED by trend filter (is_bull=%s)",
                                i,
                                ts_human(c.time),
                                is_bull,
                            )
                    elif chosen.side == "short" and not is_bear:
                        if _debug:
                            logger.info(
                                "[OB_STRAT_SHORT] bar=%d time=%s | BLOCKED by trend filter (is_bear=%s)",
                                i,
                                ts_human(c.time),
                                is_bear,
                            )
                    else:
                        _open_from_candidate(chosen)
            elif current_side == "long" and short_candidate is not None:
                # Reversal long→short only if swing trend is bearish.
                if is_bear:
                    if _debug:
                        logger.info("[OB_STRAT] bar=%d time=%s | REVERSAL long→short", i, ts_human(c.time))
                    position = None
                    _open_from_candidate(short_candidate)
                elif _debug:
                    logger.info(
                        "[OB_STRAT] bar=%d time=%s | REVERSAL long→short BLOCKED by trend filter (is_bear=%s)",
                        i,
                        ts_human(c.time),
                        is_bear,
                    )
            elif current_side == "short" and long_candidate is not None:
                # Reversal short→long only if swing trend is bullish.
                if is_bull:
                    if _debug:
                        logger.info("[OB_STRAT] bar=%d time=%s | REVERSAL short→long", i, ts_human(c.time))
                    position = None
                    _open_from_candidate(long_candidate)
                elif _debug:
                    logger.info(
                        "[OB_STRAT] bar=%d time=%s | REVERSAL short→long BLOCKED by trend filter (is_bull=%s)",
                        i,
                        ts_human(c.time),
                        is_bull,
                    )

        # --- Trailing stop for active position (define stop level for next bar) ---
        # Position open price = entry bar close (we enter on bar close when conditions met)
        if position and prev_candle is not None and position.entry_bar < i:
            if position.side == "long":
                # Breakeven: trail toward entry + 0.1×entry_bar_body when close above that level
                breakeven_target_long = position.entry_price
                if 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target_long = position.entry_price + breakeven_body_frac * abs(
                        ec.close - ec.open
                    )
                if position.entry_price > position.stop_price and c.close > breakeven_target_long:
                    new_stop = breakeven_target_long - trail_param * (
                        breakeven_target_long - position.stop_price
                    )
                    if new_stop > position.stop_price:
                        if _debug:
                            logger.info(
                                "[OB_STOP_BREAKEVEN_LONG] bar=%d time=%s | old_stop=%.1f new_stop=%.1f breakeven_target=%.1f",
                                i,
                                ts_human(c.time),
                                position.stop_price,
                                new_stop,
                                breakeven_target_long,
                            )
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "long":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                price=last.price,
                                side="long",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                price=new_stop,
                                side="long",
                            )
                        )
                # S/R support + bullish OB tops + bearish breaker bottoms (act as support when broken)
                # + entry price + optional breakeven target (entry + frac*body)
                # Use trail_sr_min_strength for trailing (include more levels); min_sr_strength is for blocking only
                levels = [l["price"] for l in sr_lines if l.get("width", 0) >= trail_sr_min_strength]
                levels.extend([ob.top for ob in bullish_ob])
                levels.extend([ob.bottom for ob in bearish_ob if ob.breaker])
                levels.append(position.entry_price)  # Position open = entry bar close
                if breakeven_body_frac > 0 and 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target = position.entry_price + breakeven_body_frac * (
                        ec.close - ec.open
                    )
                    levels.append(breakeven_target)
                crossed = _confirmed_level_cross_long(
                    candles,
                    i,
                    prev_candle,
                    levels,
                    position.stop_price,
                    volume_spike_mult,
                    trail_consecutive_closes,
                    vol_lookback,
                )
                if crossed is not None:
                    new_stop = crossed - trail_param * (crossed - position.stop_price)
                    if new_stop > position.stop_price:
                        if _debug:
                            logger.info(
                                "[OB_STOP_TRAIL_LONG] bar=%d time=%s | level=%.1f old_stop=%.1f new_stop=%.1f",
                                i,
                                ts_human(c.time),
                                crossed,
                                position.stop_price,
                                new_stop,
                            )
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "long":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                price=last.price,
                                side="long",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                price=new_stop,
                                side="long",
                            )
                        )
                elif stop_segments and stop_segments[-1].side == "long":
                    last = stop_segments[-1]
                    stop_segments[-1] = StopSegment(
                        start_time=last.start_time,
                        end_time=time_s,
                        price=position.stop_price,
                        side="long",
                    )
            else:
                # Breakeven: trail toward entry - 0.1×entry_bar_body when close below that level
                breakeven_target_short = position.entry_price
                if 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target_short = position.entry_price - breakeven_body_frac * abs(
                        ec.close - ec.open
                    )
                if position.entry_price < position.stop_price and c.close < breakeven_target_short:
                    new_stop = breakeven_target_short + trail_param * (
                        position.stop_price - breakeven_target_short
                    )
                    if new_stop < position.stop_price:
                        if _debug:
                            logger.info(
                                "[OB_STOP_BREAKEVEN_SHORT] bar=%d time=%s | old_stop=%.1f new_stop=%.1f breakeven_target=%.1f",
                                i,
                                ts_human(c.time),
                                position.stop_price,
                                new_stop,
                                breakeven_target_short,
                            )
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "short":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                price=last.price,
                                side="short",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                price=new_stop,
                                side="short",
                            )
                        )
                # S/R resistance + bearish OB bottoms + bullish breaker tops (act as resistance when broken)
                # + entry price + optional breakeven target (entry + frac*body)
                levels = [l["price"] for l in sr_lines if l.get("width", 0) >= trail_sr_min_strength]
                levels.extend([ob.bottom for ob in bearish_ob])
                levels.extend([ob.top for ob in bullish_ob if ob.breaker])
                levels.append(position.entry_price)  # Position open = entry bar close
                if breakeven_body_frac > 0 and 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target = position.entry_price + breakeven_body_frac * (
                        ec.close - ec.open
                    )
                    levels.append(breakeven_target)
                crossed = _confirmed_level_cross_short(
                    candles,
                    i,
                    prev_candle,
                    levels,
                    position.stop_price,
                    volume_spike_mult,
                    trail_consecutive_closes,
                    vol_lookback,
                )
                if crossed is not None:
                    new_stop = crossed + trail_param * (position.stop_price - crossed)
                    if new_stop < position.stop_price:
                        if _debug:
                            logger.info(
                                "[OB_STOP_TRAIL_SHORT] bar=%d time=%s | level=%.1f old_stop=%.1f new_stop=%.1f",
                                i,
                                ts_human(c.time),
                                crossed,
                                position.stop_price,
                                new_stop,
                            )
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "short":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                price=last.price,
                                side="short",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                price=new_stop,
                                side="short",
                            )
                        )
                elif stop_segments and stop_segments[-1].side == "short":
                    last = stop_segments[-1]
                    stop_segments[-1] = StopSegment(
                        start_time=last.start_time,
                        end_time=time_s,
                        price=position.stop_price,
                        side="short",
                    )

        prev_candle = c

    return events, stop_segments

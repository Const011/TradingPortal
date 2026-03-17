"""Order Block Trend-Following strategy. See docs/strategy-order-block-trend-following.md."""

import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from app.schemas.market import Candle

logger = logging.getLogger(__name__)

# Temporary debug: log bullish signal steps for bars around 2026-03-02 17:00
now = datetime.now(timezone.utc)
current_hour = now.replace(minute=0, second=0, microsecond=0)
#_DEBUG_TS_START = int(datetime(2025, 3, 16, 0, 0).timestamp() * 1000)
#_DEBUG_TS_END = int(datetime(2025, 3, 16, 1, 0).timestamp() * 1000)

# last bar
_DEBUG_TS_START = int((current_hour - timedelta(hours=1)).timestamp() * 1000)
_DEBUG_TS_END = int((current_hour + timedelta(hours=1)).timestamp() * 1000)

from app.utils.timefmt import ts_human

from app.services.indicators.order_blocks import (
    OrderBlock,
    _iter_order_blocks_from_pivots,
    _compute_order_blocks_from_pivots,
)
from app.services.indicators.cumulative_volume_delta import (
    compute_cumulative_volume_delta,
    DEFAULT_CVD_LENGTH,
)
from app.services.trading_strategy.types import (
    StrategySeedPosition,
    TradeEvent,
    StopSegment,
)
from app.services.indicators.order_blocks import DEFAULT_KEEP_BREAKERS

logger = logging.getLogger(__name__)

# Candle colors from smart_money_structure: only when BOTH swing AND internal agree
# #22c55e = swing bullish + internal bullish; #dc2626 = swing bearish + internal bearish
#BULLISH_COLORS = {"#22c55e"} # both in bullish
#BEARISH_COLORS = {"#dc2626"} # both in bearish

# excluding internal in the opposite direction
#BULLISH_COLORS = {"#15803d","#22c55e", "#b91c1c" }  # when swing trend bullish
#BEARISH_COLORS = {"#b91c1c","#dc2626", "#15803d" }  # when swing trend bearish

# all colors - ignore rule
BULLISH_COLORS = {"#15803d","#22c55e", "#b91c1c", "#dc2626"}  # when swing trend bullish
BEARISH_COLORS = {"#b91c1c","#dc2626", "#15803d",  "#22c55e"}  # when swing trend bearish


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
DEFAULT_TARGET_SR_MIN_STRENGTH = 2.5
DEFAULT_TRAIL_SR_MIN_STRENGTH = 0.0  # Include all S/R for trailing; min_sr_strength only for blocking
DEFAULT_TRAIL_PARAM = 0.75
# More relaxed trail when using previous bar low/high as level (test alternative to level-based trailing).
DEFAULT_TRAIL_PARAM_PREV_BAR = 0.85
DEFAULT_ATR_LENGTH = 14
DEFAULT_ATR_STOP_MULT = 2.0
DEFAULT_BREAKEVEN_BODY_FRAC = 0.1  # Trail toward open + N*(close-open); 0 = disabled
DEFAULT_WARMUP_BARS = 1000

DEFAULT_MIN_OB_STRENGTH = 0.75

DEFAULT_CVD_SEQUENCE_BARS = 1

DEFAULT_MIN_RR_RATIO = 2.5

@dataclass
class _ActivePosition:
    """In-position state for trailing stop."""

    side: str
    trade_id: str
    entry_price: float
    entry_bar: int
    stop_price: float
    target_price: float | None
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
    target_price: float | None = None
    target_source: str | None = None


def _last_segment_for_trade(
    stop_segments: list[StopSegment],
    trade_id: str,
) -> StopSegment | None:
    if not stop_segments:
        return None
    last = stop_segments[-1]
    if last.trade_id != trade_id:
        return None
    return last


def _candle_time_sec(candle: Candle) -> int:
    return candle.time // 1000


def _find_bar_index_by_time(candles: list[Candle], time_sec: int) -> int | None:
    for idx, candle in enumerate(candles):
        if _candle_time_sec(candle) == time_sec:
            return idx
    return None


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


def _compute_initial_stop_long(
    ob_bottom: float,
    sr_lines: list[dict],
    entry_price: float,
    min_strength: float,
    candles: list[Candle] | None = None,
    bar_index: int = 0,
    atr_length: int = DEFAULT_ATR_LENGTH,
    atr_stop_mult: float = DEFAULT_ATR_STOP_MULT,
    guard_eps: float | None = None,
) -> float:
    """Higher of OB bottom or support-gap (tighter = better for long). Optionally cap by ATR."""
    support = _get_closest_support_below(sr_lines, entry_price, min_strength)
    rule = "OB_ONLY"
    support_price = None
    stop_below_support = None
    if support is None:
        structural = ob_bottom
    else:
        support_price = support[0]
        gap = (entry_price - support_price) / 2
        stop_below_support = support_price - gap
        structural = max(ob_bottom, stop_below_support)
        rule = "SUPPORT_GAP"
    atr_cap = None
    if atr_stop_mult > 0 and candles and bar_index >= atr_length:
        atr_val = _atr(candles, atr_length, bar_index)
        if atr_val > 0:
            atr_cap = entry_price - atr_stop_mult * atr_val
            structural = max(structural, atr_cap)
    # Mandatory guard: stop must sit *below* the entry bar's low.
    max_stop = None
    if candles and 0 <= bar_index < len(candles):
        bar = candles[bar_index]
        eps = guard_eps
        if eps is None:
            base_price = bar.low or entry_price
            if base_price <= 0:
                eps = 0.0
            else:
                # Fallback: price‑scaled epsilon when exchange tick size is unknown.
                scale = 10.0 ** math.floor(math.log10(base_price))
                eps = 0.001 * scale
        max_stop = bar.low - eps if eps and eps > 0 else bar.low
        structural = min(structural, max_stop)

    # Debug: explain which rule determined the initial long stop.
    if candles and 0 <= bar_index < len(candles):
        c = candles[bar_index]
        _debug = _DEBUG_TS_START <= c.time <= _DEBUG_TS_END
        if _debug:
            logger.info(
                "[OB_STOP_RULE_LONG] bar=%d time=%s | entry=%.4f ob_bottom=%.4f rule=%s "
                "support_price=%s stop_below_support=%s atr_cap=%s max_stop_guard=%s final_stop=%.4f",
                bar_index,
                ts_human(c.time),
                entry_price,
                ob_bottom,
                rule,
                f"{support_price:.4f}" if support_price is not None else "None",
                f"{stop_below_support:.4f}" if stop_below_support is not None else "None",
                f"{atr_cap:.4f}" if atr_cap is not None else "None",
                f"{max_stop:.4f}" if max_stop is not None else "None",
                structural,
            )

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
    guard_eps: float | None = None,
) -> float:
    """Lower of OB top or resistance+gap (tighter = better for short). Optionally cap by ATR."""
    resistance = _get_closest_resistance_above(sr_lines, entry_price, min_strength)
    rule = "OB_ONLY"
    res_price = None
    stop_above_res = None
    if resistance is None:
        structural = ob_top
    else:
        res_price = resistance[0]
        gap = (res_price - entry_price) / 2
        stop_above_res = res_price + gap
        structural = min(ob_top, stop_above_res)
        rule = "RESISTANCE_GAP"
    atr_cap = None
    if atr_stop_mult > 0 and candles and bar_index >= atr_length:
        atr_val = _atr(candles, atr_length, bar_index)
        if atr_val > 0:
            atr_cap = entry_price + atr_stop_mult * atr_val
            structural = min(structural, atr_cap)
    # Mandatory guard: stop must sit *above* the entry bar's high.
    min_stop = None
    if candles and 0 <= bar_index < len(candles):
        bar = candles[bar_index]
        eps = guard_eps
        if eps is None:
            base_price = bar.high or entry_price
            if base_price <= 0:
                eps = 0.0
            else:
                scale = 10.0 ** math.floor(math.log10(base_price))
                eps = 0.001 * scale
        min_stop = bar.high + eps if eps and eps > 0 else bar.high
        structural = max(structural, min_stop)

    # Debug: explain which rule determined the initial short stop.
    if candles and 0 <= bar_index < len(candles):
        c = candles[bar_index]
        _debug = _DEBUG_TS_START <= c.time <= _DEBUG_TS_END
        if _debug:
            logger.info(
                "[OB_STOP_RULE_SHORT] bar=%d time=%s | entry=%.4f ob_top=%.4f rule=%s "
                "resistance_price=%s stop_above_res=%s atr_cap=%s min_stop_guard=%s final_stop=%.4f",
                bar_index,
                ts_human(c.time),
                entry_price,
                ob_top,
                rule,
                f"{res_price:.4f}" if res_price is not None else "None",
                f"{stop_above_res:.4f}" if stop_above_res is not None else "None",
                f"{atr_cap:.4f}" if atr_cap is not None else "None",
                f"{min_stop:.4f}" if min_stop is not None else "None",
                structural,
            )

    return structural


def _select_target_long(
    *,
    entry_price: float,
    trigger_ob: OrderBlock,
    bearish_ob: list[OrderBlock],
    sr_lines: list[dict],
    min_ob_strength: float,
    target_sr_min_strength: float,
    bar_index: int,
    time_ms: int,
    debug_enabled: bool,
) -> tuple[float | None, str | None]:
    ob_strength_threshold = max(0.0, min_ob_strength * trigger_ob.strength_index)
    ob_candidates = [
        (ob.bottom, ob.strength_index, "bearish_ob")
        for ob in bearish_ob
        if ob.bottom > entry_price and ob.strength_index >= ob_strength_threshold
    ]
    sr_candidates = [
        (float(line["price"]), float(line.get("width", 1.0)), "resistance")
        for line in sr_lines
        if line["price"] > entry_price and line.get("width", 0.0) >= target_sr_min_strength
    ]
    all_candidates = [*ob_candidates, *sr_candidates]
    selected_price: float | None = None
    selected_source: str | None = None
    if all_candidates:
        selected_price, _selected_strength, selected_source = min(
            all_candidates,
            key=lambda item: item[0],
        )

    if debug_enabled:
        logger.info(
            "[OB_TARGET_LONG] bar=%d time=%s | entry=%.1f trigger_ob_strength=%.2f "
            "ob_strength_threshold=%.2f target_sr_min_strength=%.2f "
            "ob_candidates=%s sr_candidates=%s selected=%s@%s",
            bar_index,
            ts_human(time_ms),
            entry_price,
            trigger_ob.strength_index,
            ob_strength_threshold,
            target_sr_min_strength,
            [(round(price, 1), round(strength, 2)) for price, strength, _src in ob_candidates],
            [(round(price, 1), round(strength, 2)) for price, strength, _src in sr_candidates],
            selected_source or "None",
            f"{selected_price:.1f}" if selected_price is not None else "None",
        )
    return selected_price, selected_source


def _select_target_short(
    *,
    entry_price: float,
    trigger_ob: OrderBlock,
    bullish_ob: list[OrderBlock],
    sr_lines: list[dict],
    min_ob_strength: float,
    target_sr_min_strength: float,
    bar_index: int,
    time_ms: int,
    debug_enabled: bool,
) -> tuple[float | None, str | None]:
    ob_strength_threshold = max(0.0, min_ob_strength * trigger_ob.strength_index)
    ob_candidates = [
        (ob.top, ob.strength_index, "bullish_ob")
        for ob in bullish_ob
        if ob.top < entry_price and ob.strength_index >= ob_strength_threshold
    ]
    sr_candidates = [
        (float(line["price"]), float(line.get("width", 1.0)), "support")
        for line in sr_lines
        if line["price"] < entry_price and line.get("width", 0.0) >= target_sr_min_strength
    ]
    all_candidates = [*ob_candidates, *sr_candidates]
    selected_price: float | None = None
    selected_source: str | None = None
    if all_candidates:
        selected_price, _selected_strength, selected_source = max(
            all_candidates,
            key=lambda item: item[0],
        )

    if debug_enabled:
        logger.info(
            "[OB_TARGET_SHORT] bar=%d time=%s | entry=%.1f trigger_ob_strength=%.2f "
            "ob_strength_threshold=%.2f target_sr_min_strength=%.2f "
            "ob_candidates=%s sr_candidates=%s selected=%s@%s",
            bar_index,
            ts_human(time_ms),
            entry_price,
            trigger_ob.strength_index,
            ob_strength_threshold,
            target_sr_min_strength,
            [(round(price, 1), round(strength, 2)) for price, strength, _src in ob_candidates],
            [(round(price, 1), round(strength, 2)) for price, strength, _src in sr_candidates],
            selected_source or "None",
            f"{selected_price:.1f}" if selected_price is not None else "None",
        )
    return selected_price, selected_source


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
    target_sr_min_strength: float = DEFAULT_TARGET_SR_MIN_STRENGTH,
    trail_sr_min_strength: float = DEFAULT_TRAIL_SR_MIN_STRENGTH,
    trail_param: float = DEFAULT_TRAIL_PARAM,
    trail_param_prev_bar: float = DEFAULT_TRAIL_PARAM_PREV_BAR,
    max_ob_entry_signals: int = DEFAULT_MAX_OB_ENTRY_SIGNALS,
    atr_length: int = DEFAULT_ATR_LENGTH,
    atr_stop_mult: float = DEFAULT_ATR_STOP_MULT,
    breakeven_body_frac: float = DEFAULT_BREAKEVEN_BODY_FRAC,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    min_ob_strength: float = DEFAULT_MIN_OB_STRENGTH,
    keep_breakers: bool = DEFAULT_KEEP_BREAKERS,
    cvd_length: int = DEFAULT_CVD_LENGTH,
    cvd_sequence_bars: int = DEFAULT_CVD_SEQUENCE_BARS,
    tick_size: float | None = None,
    seed_position: StrategySeedPosition | None = None,
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
    seeded_position_activated = False
    vol_lookback = 20
    ob_entry_counts: dict[tuple[float, float, int], int] = {}  # Count actual trades per OB, not crosses
    events_history: deque[tuple[int, list[dict]]] = deque(maxlen=consecutive_closes)

    seed_entry_bar: int | None = None
    seed_active_bar: int | None = None
    if seed_position is not None:
        seed_entry_bar = _find_bar_index_by_time(candles, seed_position.entry_time)
        seed_active_bar = _find_bar_index_by_time(candles, seed_position.active_stop_time)
        if seed_entry_bar is None:
            logger.warning(
                "[OB_STRAT_SEED] ignored trade_id=%s because entry_time=%s was not found in candles",
                seed_position.trade_id,
                seed_position.entry_time,
            )
            seed_position = None
        else:
            if seed_active_bar is None or seed_active_bar < seed_entry_bar:
                seed_active_bar = seed_entry_bar
            logger.info(
                "[OB_STRAT_SEED] trade_id=%s side=%s entry_time=%s seed_stop_time=%s ref_stop_time=%s entry_bar=%s seed_bar=%s stop=%.4f ref_stop=%.4f target=%s",
                seed_position.trade_id,
                seed_position.side,
                seed_position.entry_time,
                seed_position.active_stop_time,
                seed_position.reference_stop_time,
                seed_entry_bar,
                seed_active_bar,
                seed_position.stop_price,
                seed_position.reference_stop_price,
                f"{seed_position.target_price:.4f}" if seed_position.target_price is not None else "None",
            )

    # Precompute global average OB strength (bullish + bearish) for relative filtering.
    all_bull, all_bear = _compute_order_blocks_from_pivots(
        candles,
        swing_pivots,
        keep_breakers=keep_breakers,
    )
    all_obs: list[OrderBlock] = [*all_bull, *all_bear]
    # Map OB key -> negation bar index so we can ignore negated blocks
    # once their negation time is in the past.
    ob_negated_bar: dict[tuple[float, float, int], int] = {}
    for ob in all_obs:
        if ob.negated_bar is not None:
            key = (ob.top, ob.bottom, ob.formation_bar)
            ob_negated_bar[key] = ob.negated_bar
    avg_strength = (
        sum(ob.strength_index for ob in all_obs) / len(all_obs)
        if all_obs
        else 0.0
    )
    strength_threshold = (
        min_ob_strength * avg_strength if avg_strength > 0.0 and min_ob_strength > 0.0 else 0.0
    )

    # Precompute CVD deltas for CVD-based entry blocking. The indicator returns
    # one point per candle with a `delta` field; we align by bar index.
    cvd_result = compute_cumulative_volume_delta(candles, length=cvd_length)
    cvd_points = cvd_result.get("points") or []
    cvd_delta: list[float] = [
        float(p.get("delta", 0.0)) for p in cvd_points  # type: ignore[assignment]
    ]

    # Use the same pivot-based OB engine as the indicator, driven by Smart Money
    # structure pivots passed in from `compute_structure`. This ensures the
    # strategy sees exactly the same OB topology as the graphics layer.
    for i, c, bullish_ob, bearish_ob in _iter_order_blocks_from_pivots(
        candles,
        swing_pivots,
        keep_breakers=keep_breakers,
    ):
        _debug_bar = _DEBUG_TS_START <= c.time <= _DEBUG_TS_END
        if _debug_bar and (bullish_ob or bearish_ob):
            for ob in bullish_ob:
                logger.info(
                    "[OB_STRAT] bar=%d time=%s | BULL ob raw: [%.2f, %.2f] formation_bar=%d loc=%d strength=%.1f threshold=%.1f",
                    i, ts_human(c.time), ob.top, ob.bottom, ob.formation_bar, ob.loc,
                    ob.strength_index, strength_threshold,
                )
        # Relative strength filter: keep only OBs whose strength is above
        # (min_ob_strength × average strength) across all identified blocks.
        if strength_threshold > 0.0:
            bullish_before = len(bullish_ob)
            bearish_before = len(bearish_ob)
            bullish_ob = [ob for ob in bullish_ob if ob.strength_index >= strength_threshold]
            bearish_ob = [ob for ob in bearish_ob if ob.strength_index >= strength_threshold]
            if _debug_bar and (bullish_before != len(bullish_ob) or bearish_before != len(bearish_ob)):
                logger.info(
                    "[OB_STRAT] bar=%d time=%s | strength filter: BULL %d->%d BEAR %d->%d",
                    i, ts_human(c.time), bullish_before, len(bullish_ob), bearish_before, len(bearish_ob),
                )
        # Filter out OBs that have been fully negated on or before the current bar.
        bullish_ob = [
            ob
            for ob in bullish_ob
            if ob.negated_bar is None or ob.negated_bar > i
        ]
        bearish_ob = [
            ob
            for ob in bearish_ob
            if ob.negated_bar is None or ob.negated_bar > i
        ]

        bullish_by_key: dict[tuple[float, float, int], OrderBlock] = {
            (ob.top, ob.bottom, ob.formation_bar): ob for ob in bullish_ob
        }
        bearish_by_key: dict[tuple[float, float, int], OrderBlock] = {
            (ob.top, ob.bottom, ob.formation_bar): ob for ob in bearish_ob
        }

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
        if (
            seed_position is not None
            and not seeded_position_activated
            and seed_entry_bar is not None
            and seed_active_bar is not None
            and i == seed_active_bar
        ):
            position = _ActivePosition(
                side=seed_position.side,
                trade_id=seed_position.trade_id,
                entry_price=seed_position.entry_price,
                entry_bar=seed_entry_bar,
                stop_price=seed_position.stop_price,
                target_price=seed_position.target_price,
                trigger_ob_top=0.0,
                trigger_ob_bottom=0.0,
            )
            stop_segments.append(
                StopSegment(
                    start_time=seed_position.active_stop_time,
                    end_time=time_s,
                    trade_id=seed_position.trade_id,
                    price=seed_position.stop_price,
                    side=seed_position.side,
                )
            )
            seeded_position_activated = True
            if _debug:
                logger.info(
                    "[OB_STRAT_SEED] activated trade_id=%s bar=%d time=%s entry_bar=%d stop=%.4f",
                    seed_position.trade_id,
                    i,
                    ts_human(c.time),
                    seed_entry_bar,
                    seed_position.stop_price,
                )
        if _debug and raw_events:
            logger.info(
                "[OB_STRAT] bar=%d time=%s | raw_events=%d types=%s",
                i, ts_human(c.time), len(raw_events), [e["type"] for e in raw_events],
            )

        # --- Stop/target-hit check for existing position (uses stop defined on previous bar, before new entries/reversals). ---
        # Position open price = entry bar close (we enter on bar close when conditions met)
        if position and prev_candle is not None:
            if position.side == "long":
                stop_hit = c.low <= position.stop_price
                tp_hit = (
                    position.target_price is not None
                    and c.high >= position.target_price
                )
                if _debug and stop_hit:
                    logger.info(
                        "[OB_STOP_HIT_LONG] bar=%d time=%s | low=%.1f stop=%.1f entry=%.1f",
                        i,
                        ts_human(c.time),
                        c.low,
                        position.stop_price,
                        position.entry_price,
                    )
                if _debug and tp_hit:
                    logger.info(
                        "[OB_TP_HIT_LONG] bar=%d time=%s | high=%.1f target=%.1f entry=%.1f",
                        i,
                        ts_human(c.time),
                        c.high,
                        position.target_price,
                        position.entry_price,
                    )
                if stop_hit:
                    last = _last_segment_for_trade(stop_segments, position.trade_id)
                    if last is not None:
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            trade_id=last.trade_id,
                            price=last.price,
                            side="long",
                        )
                    position = None
                elif tp_hit:
                    last = _last_segment_for_trade(stop_segments, position.trade_id)
                    if last is not None:
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            trade_id=last.trade_id,
                            price=last.price,
                            side="long",
                        )
                    position = None
            else:
                stop_hit = c.high >= position.stop_price
                tp_hit = (
                    position.target_price is not None
                    and c.low <= position.target_price
                )
                if _debug and stop_hit:
                    logger.info(
                        "[OB_STOP_HIT_SHORT] bar=%d time=%s | high=%.1f stop=%.1f entry=%.1f",
                        i,
                        ts_human(c.time),
                        c.high,
                        position.stop_price,
                        position.entry_price,
                    )
                if _debug and tp_hit:
                    logger.info(
                        "[OB_TP_HIT_SHORT] bar=%d time=%s | low=%.1f target=%.1f entry=%.1f",
                        i,
                        ts_human(c.time),
                        c.low,
                        position.target_price,
                        position.entry_price,
                    )
                if stop_hit:
                    last = _last_segment_for_trade(stop_segments, position.trade_id)
                    if last is not None:
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            trade_id=last.trade_id,
                            price=last.price,
                            side="short",
                        )
                    position = None
                elif tp_hit:
                    last = _last_segment_for_trade(stop_segments, position.trade_id)
                    if last is not None:
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            trade_id=last.trade_id,
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
                    # Skip OBs that have been negated on or before the current bar.
                    neg_bar = ob_negated_bar.get(ob_key)
                    if neg_bar is not None and neg_bar <= i:
                        continue
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
                trigger_ob = bullish_by_key.get(ob_key)
                if trigger_ob is None:
                    continue
                stop = _compute_initial_stop_long(
                    ob_bottom,
                    sr_lines,
                    entry,
                    min_sr_strength,
                    candles=candles,
                    bar_index=i,
                    atr_length=atr_length,
                    atr_stop_mult=atr_stop_mult,
                    guard_eps=tick_size,
                )
                target_price, target_source = _select_target_long(
                    entry_price=entry,
                    trigger_ob=trigger_ob,
                    bearish_ob=bearish_ob,
                    sr_lines=sr_lines,
                    min_ob_strength=min_ob_strength,
                    target_sr_min_strength=target_sr_min_strength,
                    bar_index=i,
                    time_ms=c.time,
                    debug_enabled=_debug,
                )
                long_candidate = _EntryCandidate(
                    side="long",
                    ob_top=ob_top,
                    ob_bottom=ob_bottom,
                    ob_formation_bar=ob_formation_bar,
                    stop=stop,
                    ob_key=ob_key,
                    target_price=target_price,
                    target_source=target_source,
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
                trigger_ob = bearish_by_key.get(ob_key)
                if trigger_ob is None:
                    continue
                stop = _compute_initial_stop_short(
                    ob_top,
                    sr_lines,
                    entry,
                    min_sr_strength,
                    candles=candles,
                    bar_index=i,
                    atr_length=atr_length,
                    atr_stop_mult=atr_stop_mult,
                    guard_eps=tick_size,
                )
                target_price, target_source = _select_target_short(
                    entry_price=entry,
                    trigger_ob=trigger_ob,
                    bullish_ob=bullish_ob,
                    sr_lines=sr_lines,
                    min_ob_strength=min_ob_strength,
                    target_sr_min_strength=target_sr_min_strength,
                    bar_index=i,
                    time_ms=c.time,
                    debug_enabled=_debug,
                )
                short_candidate = _EntryCandidate(
                    side="short",
                    ob_top=ob_top,
                    ob_bottom=ob_bottom,
                    ob_formation_bar=ob_formation_bar,
                    stop=stop,
                    ob_key=ob_key,
                    target_price=target_price,
                    target_source=target_source,
                )
                break

            # Apply entry or reversal: flat -> open one side; in position -> reverse if opposite signal.
            current_side = position.side if position is not None else None

            def _open_from_candidate(candidate: _EntryCandidate) -> None:
                nonlocal position
                entry_price = c.close
                trade_id = str(time_s)
                # CVD-based anti-chop filter: require last `cvd_sequence_bars` deltas
                # to be consistently in the direction of the candidate.
                if cvd_sequence_bars > 0 and cvd_delta and 0 <= i < len(cvd_delta):
                    start_cvd = max(0, i - cvd_sequence_bars + 1)
                    seq = cvd_delta[start_cvd : i + 1]
                    seq_len = len(seq)
                    seq_min = min(seq) if seq else 0.0
                    seq_max = max(seq) if seq else 0.0
                    current_delta = seq[-1] if seq else 0.0
                    if candidate.side == "long":
                        if not all(d >= 0 for d in seq):
                            if _debug:
                                logger.info(
                                    "[OB_STRAT_LONG] bar=%d time=%s | BLOCKED by CVD filter "
                                    "(len=%d min=%.2f max=%.2f current=%.2f seq=%s)",
                                    i,
                                    ts_human(c.time),
                                    seq_len,
                                    seq_min,
                                    seq_max,
                                    current_delta,
                                    [round(d, 2) for d in seq],
                                )
                            return
                        elif _debug:
                            logger.info(
                                "[OB_STRAT_LONG] bar=%d time=%s | CVD filter PASSED "
                                "(len=%d min=%.2f max=%.2f current=%.2f seq=%s)",
                                i,
                                ts_human(c.time),
                                seq_len,
                                seq_min,
                                seq_max,
                                current_delta,
                                [round(d, 2) for d in seq],
                            )
                    else:
                        if not all(d <= 0 for d in seq):
                            if _debug:
                                logger.info(
                                    "[OB_STRAT_SHORT] bar=%d time=%s | BLOCKED by CVD filter "
                                    "(len=%d min=%.2f max=%.2f current=%.2f seq=%s)",
                                    i,
                                    ts_human(c.time),
                                    seq_len,
                                    seq_min,
                                    seq_max,
                                    current_delta,
                                    [round(d, 2) for d in seq],
                                )
                            return
                        elif _debug:
                            logger.info(
                                "[OB_STRAT_SHORT] bar=%d time=%s | CVD filter PASSED "
                                "(len=%d min=%.2f max=%.2f current=%.2f seq=%s)",
                                i,
                                ts_human(c.time),
                                seq_len,
                                seq_min,
                                seq_max,
                                current_delta,
                                [round(d, 2) for d in seq],
                            )
                # Risk-reward filter using candidate stop vs. target_price.
                if candidate.target_price is not None:
                    if candidate.side == "long":
                        risk = entry_price - candidate.stop
                        reward = candidate.target_price - entry_price
                    else:
                        risk = candidate.stop - entry_price
                        reward = entry_price - candidate.target_price
                    rr = (reward / risk) if risk > 0 else 0.0
                    rr_ok = risk > 0 and reward > 0 and rr >= DEFAULT_MIN_RR_RATIO
                    if not rr_ok:
                        if _debug:
                            logger.info(
                                "[OB_STRAT_%s] bar=%d time=%s | BLOCKED by RR filter "
                                "(entry=%.1f stop=%.1f target=%.1f target_source=%s risk=%.1f reward=%.1f rr=%.2f)",
                                "LONG" if candidate.side == "long" else "SHORT",
                                i,
                                ts_human(c.time),
                                entry_price,
                                candidate.stop,
                                candidate.target_price,
                                candidate.target_source,
                                risk,
                                reward,
                                rr,
                            )
                        return
                    elif _debug:
                        logger.info(
                            "[OB_STRAT_%s] bar=%d time=%s | RR filter PASSED "
                            "(entry=%.1f stop=%.1f target=%.1f target_source=%s risk=%.1f reward=%.1f rr=%.2f)",
                            "LONG" if candidate.side == "long" else "SHORT",
                            i,
                            ts_human(c.time),
                            entry_price,
                            candidate.stop,
                            candidate.target_price,
                            candidate.target_source,
                            risk,
                            reward,
                            rr,
                        )
                if candidate.side == "long":
                    if _debug:
                        logger.info(
                            "[OB_STRAT_LONG] bar=%d time=%s | ENTRY LONG ob=[%.1f,%.1f] price=%.1f target=%s target_source=%s (reversal_from=%s)",
                            i, ts_human(c.time), candidate.ob_top, candidate.ob_bottom, entry_price,
                            f"{candidate.target_price:.1f}" if candidate.target_price is not None else "None",
                            candidate.target_source,
                            current_side,
                        )
                    events.append(
                        TradeEvent(
                            time=time_s,
                            trade_id=trade_id,
                            bar_index=i,
                            type="OB_TREND_BUY",
                            side="long",
                            price=entry_price,
                            target_price=candidate.target_price,
                            initial_stop_price=candidate.stop,
                            context={
                                "ob_top": candidate.ob_top,
                                "ob_bottom": candidate.ob_bottom,
                                "trigger": "entry_window",
                                "target_source": candidate.target_source,
                                "reversal_from": current_side,
                            },
                        )
                    )
                    position = _ActivePosition(
                        side="long",
                        trade_id=trade_id,
                        entry_price=entry_price,
                        entry_bar=i,
                        stop_price=candidate.stop,
                        target_price=candidate.target_price,
                        trigger_ob_top=candidate.ob_top,
                        trigger_ob_bottom=candidate.ob_bottom,
                    )
                    stop_segments.append(
                        StopSegment(
                            start_time=time_s,
                            end_time=time_s,
                            trade_id=trade_id,
                            price=candidate.stop,
                            side="long",
                        )
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
                            "[OB_STRAT_SHORT] bar=%d time=%s | ENTRY SHORT ob=[%.1f,%.1f] price=%.1f target=%s target_source=%s (reversal_from=%s)",
                            i, ts_human(c.time), candidate.ob_top, candidate.ob_bottom, entry_price,
                            f"{candidate.target_price:.1f}" if candidate.target_price is not None else "None",
                            candidate.target_source,
                            current_side,
                        )
                    events.append(
                        TradeEvent(
                            time=time_s,
                            trade_id=trade_id,
                            bar_index=i,
                            type="OB_TREND_SELL",
                            side="short",
                            price=entry_price,
                            target_price=candidate.target_price,
                            initial_stop_price=candidate.stop,
                            context={
                                "ob_top": candidate.ob_top,
                                "ob_bottom": candidate.ob_bottom,
                                "trigger": "entry_window",
                                "target_source": candidate.target_source,
                                "reversal_from": current_side,
                            },
                        )
                    )
                    position = _ActivePosition(
                        side="short",
                        trade_id=trade_id,
                        entry_price=entry_price,
                        entry_bar=i,
                        stop_price=candidate.stop,
                        target_price=candidate.target_price,
                        trigger_ob_top=candidate.ob_top,
                        trigger_ob_bottom=candidate.ob_bottom,
                    )
                    stop_segments.append(
                        StopSegment(
                            start_time=time_s,
                            end_time=time_s,
                            trade_id=trade_id,
                            price=candidate.stop,
                            side="short",
                        )
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

            seed_guard_active = (
                seed_position is not None
                and seed_active_bar is not None
                and i <= seed_active_bar
            )
            if seed_guard_active:
                if _debug and (long_candidate is not None or short_candidate is not None):
                    logger.info(
                        "[OB_STRAT_SEED] bar=%d time=%s | skipping entry/reversal evaluation until after seed bar=%d",
                        i,
                        ts_human(c.time),
                        seed_active_bar,
                    )
            elif current_side is None:
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
        # Each appended StopSegment has price=position.stop_price (after update). The strategy
        # can emit multiple segments per bar (e.g. breakeven then level_cross). For execution
        # and logging, use get_effective_stop_segments_for_bar() to get the single best stop
        # per trade (long: max price, short: min price among segments for that bar).
        if position and prev_candle is not None and position.entry_bar <= i:
            bar_reference_stop_price = position.stop_price
            if (
                seed_position is not None
                and seed_active_bar is not None
                and i == seed_active_bar
            ):
                bar_reference_stop_price = seed_position.reference_stop_price
            if position.side == "long":
                # Breakeven: trail toward entry + 0.1×entry_bar_body when close above that level
                breakeven_target_long = position.entry_price
                if 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target_long = position.entry_price + breakeven_body_frac * abs(
                        ec.close - ec.open
                    )
                if position.entry_price > bar_reference_stop_price and c.close > breakeven_target_long:
                    new_stop = breakeven_target_long - trail_param * (
                        breakeven_target_long - bar_reference_stop_price
                    )
                    if new_stop > position.stop_price:
                        if _debug:
                            entry_body = abs(candles[position.entry_bar].close - candles[position.entry_bar].open) if 0 <= position.entry_bar < len(candles) else 0.0
                            logger.info(
                                "[OB_STOP_BREAKEVEN_LONG] bar=%d time=%s | rule=breakeven | ref_stop=%.1f active_stop=%.1f new_stop=%.1f breakeven_target=%.1f | params: trail_param=%.2f breakeven_body_frac=%.2f entry_bar_body=%.1f",
                                i,
                                ts_human(c.time),
                                bar_reference_stop_price,
                                position.stop_price,
                                new_stop,
                                breakeven_target_long,
                                trail_param,
                                breakeven_body_frac,
                                entry_body,
                            )
                        position.stop_price = new_stop
                        last = _last_segment_for_trade(stop_segments, position.trade_id)
                        if last is not None:
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                trade_id=last.trade_id,
                                price=last.price,
                                side="long",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                trade_id=position.trade_id,
                                price=new_stop,
                                side="long",
                            )
                        )
                    elif _debug:
                        logger.info(
                            "[OB_STOP_BREAKEVEN_LONG] bar=%d time=%s | candidate rejected (<= active stop) ref_stop=%.1f active_stop=%.1f candidate=%.1f breakeven_target=%.1f",
                            i,
                            ts_human(c.time),
                            bar_reference_stop_price,
                            position.stop_price,
                            new_stop,
                            breakeven_target_long,
                        )
                # S/R support + bullish OB tops + bearish breaker bottoms (act as support when broken)
                # + entry price + optional breakeven target (entry + frac*body)
                # Use trail_sr_min_strength for trailing (include more levels); min_sr_strength is for blocking only
                level_tuples_long: list[tuple[float, str]] = []
                for l in sr_lines:
                    if l.get("width", 0) >= trail_sr_min_strength:
                        level_tuples_long.append((l["price"], "sr"))
                for ob in bullish_ob:
                    level_tuples_long.append((ob.top, "ob_bull_top"))
                for ob in bearish_ob:
                    if ob.breaker:
                        level_tuples_long.append((ob.bottom, "ob_bear_breaker_bottom"))
                level_tuples_long.append((position.entry_price, "entry"))
                if breakeven_body_frac > 0 and 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target = position.entry_price + breakeven_body_frac * (
                        ec.close - ec.open
                    )
                    level_tuples_long.append((breakeven_target, "breakeven_target"))
                # Alternative: previous bar's low as support when above current stop (higher low = support).
                if prev_candle.low > bar_reference_stop_price:
                    level_tuples_long.append((prev_candle.low, "prev_bar_low"))
                levels_long = [p for p, _ in level_tuples_long]
                crossed = _confirmed_level_cross_long(
                    candles,
                    i,
                    prev_candle,
                    levels_long,
                    bar_reference_stop_price,
                    volume_spike_mult,
                    trail_consecutive_closes,
                    vol_lookback,
                )
                if crossed is not None:
                    level_source = next((s for (p, s) in level_tuples_long if p == crossed), "unknown")
                    param_long = trail_param_prev_bar if level_source == "prev_bar_low" else trail_param
                    new_stop = crossed - param_long * (crossed - bar_reference_stop_price)
                    if new_stop > position.stop_price:
                        if _debug:
                            logger.info(
                                "[OB_STOP_TRAIL_LONG] bar=%d time=%s | rule=level_cross level=%.1f source=%s ref_stop=%.1f active_stop=%.1f new_stop=%.1f | params: trail_param=%.2f trail_sr_min_strength=%.0f volume_spike_mult=%.2f trail_consecutive_closes=%d vol_lookback=%d",
                                i,
                                ts_human(c.time),
                                crossed,
                                level_source,
                                bar_reference_stop_price,
                                position.stop_price,
                                new_stop,
                                param_long,
                                trail_sr_min_strength,
                                volume_spike_mult,
                                trail_consecutive_closes,
                                vol_lookback,
                            )
                        position.stop_price = new_stop
                        last = _last_segment_for_trade(stop_segments, position.trade_id)
                        if last is not None:
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                trade_id=last.trade_id,
                                price=last.price,
                                side="long",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                trade_id=position.trade_id,
                                price=new_stop,
                                side="long",
                            )
                        )
                    elif _debug:
                        logger.info(
                            "[OB_STOP_TRAIL_LONG] bar=%d time=%s | candidate rejected (<= active stop) level=%.1f source=%s ref_stop=%.1f active_stop=%.1f candidate=%.1f",
                            i,
                            ts_human(c.time),
                            crossed,
                            level_source,
                            bar_reference_stop_price,
                            position.stop_price,
                            new_stop,
                        )
                else:
                    last = _last_segment_for_trade(stop_segments, position.trade_id)
                    if last is not None:
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            trade_id=last.trade_id,
                            price=position.stop_price,
                            side="long",
                        )
                        if _debug:
                            logger.info(
                                "[OB_STOP_LONG] bar=%d time=%s | rule=no_move (extend segment) stop=%.1f",
                                i, ts_human(c.time), position.stop_price,
                            )
            else:
                # Breakeven: trail toward entry - 0.1×entry_bar_body when close below that level
                breakeven_target_short = position.entry_price
                if 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target_short = position.entry_price - breakeven_body_frac * abs(
                        ec.close - ec.open
                    )
                if position.entry_price < bar_reference_stop_price and c.close < breakeven_target_short:
                    new_stop = breakeven_target_short + trail_param * (
                        bar_reference_stop_price - breakeven_target_short
                    )
                    if new_stop < position.stop_price:
                        if _debug:
                            entry_body = abs(candles[position.entry_bar].close - candles[position.entry_bar].open) if 0 <= position.entry_bar < len(candles) else 0.0
                            logger.info(
                                "[OB_STOP_BREAKEVEN_SHORT] bar=%d time=%s | rule=breakeven | ref_stop=%.1f active_stop=%.1f new_stop=%.1f breakeven_target=%.1f | params: trail_param=%.2f breakeven_body_frac=%.2f entry_bar_body=%.1f",
                                i,
                                ts_human(c.time),
                                bar_reference_stop_price,
                                position.stop_price,
                                new_stop,
                                breakeven_target_short,
                                trail_param,
                                breakeven_body_frac,
                                entry_body,
                            )
                        position.stop_price = new_stop
                        last = _last_segment_for_trade(stop_segments, position.trade_id)
                        if last is not None:
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                trade_id=last.trade_id,
                                price=last.price,
                                side="short",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                trade_id=position.trade_id,
                                price=new_stop,
                                side="short",
                            )
                        )
                    elif _debug:
                        logger.info(
                            "[OB_STOP_BREAKEVEN_SHORT] bar=%d time=%s | candidate rejected (>= active stop) ref_stop=%.1f active_stop=%.1f candidate=%.1f breakeven_target=%.1f",
                            i,
                            ts_human(c.time),
                            bar_reference_stop_price,
                            position.stop_price,
                            new_stop,
                            breakeven_target_short,
                        )
                # S/R resistance + bearish OB bottoms + bullish breaker tops (act as resistance when broken)
                # + entry price + optional breakeven target (entry + frac*body)
                level_tuples_short: list[tuple[float, str]] = []
                for l in sr_lines:
                    if l.get("width", 0) >= trail_sr_min_strength:
                        level_tuples_short.append((l["price"], "sr"))
                for ob in bearish_ob:
                    level_tuples_short.append((ob.bottom, "ob_bear_bottom"))
                for ob in bullish_ob:
                    if ob.breaker:
                        level_tuples_short.append((ob.top, "ob_bull_breaker_top"))
                level_tuples_short.append((position.entry_price, "entry"))
                if breakeven_body_frac > 0 and 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target = position.entry_price + breakeven_body_frac * (
                        ec.close - ec.open
                    )
                    level_tuples_short.append((breakeven_target, "breakeven_target"))
                # Alternative: previous bar's high as resistance when below current stop (lower high = resistance).
                if prev_candle.high < bar_reference_stop_price:
                    level_tuples_short.append((prev_candle.high, "prev_bar_high"))
                levels_short = [p for p, _ in level_tuples_short]
                crossed = _confirmed_level_cross_short(
                    candles,
                    i,
                    prev_candle,
                    levels_short,
                    bar_reference_stop_price,
                    volume_spike_mult,
                    trail_consecutive_closes,
                    vol_lookback,
                )
                if crossed is not None:
                    level_source = next((s for (p, s) in level_tuples_short if p == crossed), "unknown")
                    param_short = trail_param_prev_bar if level_source == "prev_bar_high" else trail_param
                    new_stop = crossed + param_short * (bar_reference_stop_price - crossed)
                    if new_stop < position.stop_price:
                        if _debug:
                            logger.info(
                                "[OB_STOP_TRAIL_SHORT] bar=%d time=%s | rule=level_cross level=%.1f source=%s ref_stop=%.1f active_stop=%.1f new_stop=%.1f | params: trail_param=%.2f trail_sr_min_strength=%.0f volume_spike_mult=%.2f trail_consecutive_closes=%d vol_lookback=%d",
                                i,
                                ts_human(c.time),
                                crossed,
                                level_source,
                                bar_reference_stop_price,
                                position.stop_price,
                                new_stop,
                                param_short,
                                trail_sr_min_strength,
                                volume_spike_mult,
                                trail_consecutive_closes,
                                vol_lookback,
                            )
                        position.stop_price = new_stop
                        last = _last_segment_for_trade(stop_segments, position.trade_id)
                        if last is not None:
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time,
                                end_time=time_s,
                                trade_id=last.trade_id,
                                price=last.price,
                                side="short",
                            )
                        stop_segments.append(
                            StopSegment(
                                start_time=time_s,
                                end_time=time_s,
                                trade_id=position.trade_id,
                                price=new_stop,
                                side="short",
                            )
                        )
                    elif _debug:
                        logger.info(
                            "[OB_STOP_TRAIL_SHORT] bar=%d time=%s | candidate rejected (>= active stop) level=%.1f source=%s ref_stop=%.1f active_stop=%.1f candidate=%.1f",
                            i,
                            ts_human(c.time),
                            crossed,
                            level_source,
                            bar_reference_stop_price,
                            position.stop_price,
                            new_stop,
                        )
                else:
                    last = _last_segment_for_trade(stop_segments, position.trade_id)
                    if last is not None:
                        stop_segments[-1] = StopSegment(
                            start_time=last.start_time,
                            end_time=time_s,
                            trade_id=last.trade_id,
                            price=position.stop_price,
                            side="short",
                        )
                        if _debug:
                            logger.info(
                                "[OB_STOP_SHORT] bar=%d time=%s | rule=no_move (extend segment) stop=%.1f",
                                i, ts_human(c.time), position.stop_price,
                            )

        prev_candle = c

    return events, stop_segments

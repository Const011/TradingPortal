"""Order Block Trend-Following strategy. See docs/strategy-order-block-trend-following.md."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.schemas.market import Candle

logger = logging.getLogger(__name__)

# Temporary debug: log bullish signal steps for bars around 2026-03-02 17:00
_DEBUG_TS_START = int(datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
_DEBUG_TS_END = int(datetime(2026, 3, 2, 19, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _ts_human(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
from app.services.indicators.order_blocks import (
    DEFAULT_ENTRY_ZONE_MULT,
    DEFAULT_MAX_OB_ENTRY_SIGNALS,
    OrderBlock,
    _iter_order_blocks,
)
from app.services.trading_strategy.types import TradeEvent, StopSegment

logger = logging.getLogger(__name__)

# Candle colors from smart_money_structure (green = bullish, red = bearish)
BULLISH_COLORS = {"#22c55e", "#15803d"}
BEARISH_COLORS = {"#dc2626", "#b91c1c"}

# Default parameters
DEFAULT_VOLUME_SPIKE_MULT = 1.5
DEFAULT_CONSECUTIVE_CLOSES = 2
DEFAULT_TRAIL_CONSECUTIVE_CLOSES = 2
DEFAULT_BLOCK_OB_DISTANCE_MULT = 2.0
DEFAULT_BLOCK_SR_DISTANCE_MULT = 2.0
DEFAULT_MIN_SR_STRENGTH = 4.0
DEFAULT_TRAIL_SR_MIN_STRENGTH = 0.0  # Include all S/R for trailing; min_sr_strength only for blocking
DEFAULT_TRAIL_PARAM = 0.75
DEFAULT_ATR_LENGTH = 14
DEFAULT_ATR_STOP_MULT = 2.0
DEFAULT_BREAKEVEN_BODY_FRAC = 0.1  # Trail toward open + N*(close-open); 0 = disabled


@dataclass
class _PendingSignal:
    """A trigger awaiting confirmation."""

    bar_index: int
    event_type: str
    ob_top: float
    ob_bottom: float
    ob_width: float
    side: str


@dataclass
class _ActivePosition:
    """In-position state for trailing stop."""

    side: str
    entry_price: float
    entry_bar: int
    stop_price: float
    trigger_ob_top: float
    trigger_ob_bottom: float


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
    max_ob_entry_signals: int,
    ob_signal_counts: dict[tuple[float, float, int], int],
) -> list[dict]:
    """
    Detect boundary crosses and breaker-created events for strategy triggers.
    All crossover/entry logic lives in the strategy layer.
    """
    events: list[dict] = []
    emitted_boundary: set[tuple[float, float, int]] = set()

    def ob_key(ob: OrderBlock) -> tuple[float, float, int]:
        return (ob.top, ob.bottom, ob.formation_bar)

    # Bullish: boundary cross and breaker created
    for ob in bullish_ob:
        if not ob.breaker and ob.loc < i:
            if min(c.close, c.open) < ob.bottom:
                events.append({"type": "bullish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc})
            else:
                ob_height = ob.top - ob.bottom
                zone_top = ob.bottom + entry_zone_mult * ob_height
                touched_zone = c.low <= zone_top and c.high >= ob.bottom
                close_above = c.close > ob.top and c.close > c.open
                if touched_zone and close_above:
                    count = ob_signal_counts.get(ob_key(ob), 0)
                    if count < max_ob_entry_signals:
                        ob_signal_counts[ob_key(ob)] = count + 1
                        emitted_boundary.add(ob_key(ob))
                        events.append({"type": "bullish_boundary_crossed", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc})
        elif ob.breaker and ob.break_loc == i:
            events.append({"type": "bullish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc})

    # When OB is newly created on bar i, check if bar i-1 crossed the zone (the bar that actually crossed before structure break)
    for ob in bullish_ob:
        if ob.formation_bar == i and i >= 1 and ob_key(ob) not in emitted_boundary:
            prev = candles[i - 1]
            ob_height = ob.top - ob.bottom
            zone_top = ob.bottom + entry_zone_mult * ob_height
            touched_zone = prev.low <= zone_top and prev.high >= ob.bottom
            close_above = prev.close > ob.top and prev.close > prev.open
            if touched_zone and close_above:
                count = ob_signal_counts.get(ob_key(ob), 0)
                if count < max_ob_entry_signals:
                    ob_signal_counts[ob_key(ob)] = count + 1
                    emitted_boundary.add(ob_key(ob))
                    events.append({
                        "type": "bullish_boundary_crossed",
                        "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc,
                        "trigger_bar": i - 1,  # Use prev bar's OHLC for entry/volume
                    })

    emitted_bearish_boundary: set[tuple[float, float, int]] = set()

    # Bearish: boundary cross and breaker created
    for ob in bearish_ob:
        if not ob.breaker and ob.loc < i:
            if max(c.close, c.open) > ob.top:
                events.append({"type": "bearish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc})
            else:
                ob_height = ob.top - ob.bottom
                zone_bottom = ob.top - entry_zone_mult * ob_height
                touched_zone = c.high >= zone_bottom and c.low <= ob.top
                close_below = c.close < ob.bottom and c.close < c.open
                if touched_zone and close_below:
                    count = ob_signal_counts.get(ob_key(ob), 0)
                    if count < max_ob_entry_signals:
                        ob_signal_counts[ob_key(ob)] = count + 1
                        emitted_bearish_boundary.add(ob_key(ob))
                        events.append({"type": "bearish_boundary_crossed", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc})
        elif ob.breaker and ob.break_loc == i:
            events.append({"type": "bearish_breaker_created", "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc})

    # When bearish OB newly created, check prev bar
    for ob in bearish_ob:
        if ob.formation_bar == i and i >= 1 and ob_key(ob) not in emitted_bearish_boundary:
            prev = candles[i - 1]
            ob_height = ob.top - ob.bottom
            zone_bottom = ob.top - entry_zone_mult * ob_height
            touched_zone = prev.high >= zone_bottom and prev.low <= ob.top
            close_below = prev.close < ob.bottom and prev.close < prev.open
            if touched_zone and close_below:
                count = ob_signal_counts.get(ob_key(ob), 0)
                if count < max_ob_entry_signals:
                    ob_signal_counts[ob_key(ob)] = count + 1
                    emitted_bearish_boundary.add(ob_key(ob))
                    events.append({
                        "type": "bearish_boundary_crossed",
                        "ob_top": ob.top, "ob_bottom": ob.bottom, "ob_loc": ob.loc,
                        "trigger_bar": i - 1,
                    })

    return events


def compute_order_block_trend_following(
    candles: list[Candle],
    candle_colors: dict[int, str] | None = None,
    sr_lines: list[dict] | None = None,
    *,
    entry_zone_mult: float = DEFAULT_ENTRY_ZONE_MULT,  # from order_blocks
    volume_spike_mult: float = DEFAULT_VOLUME_SPIKE_MULT,
    consecutive_closes: int = DEFAULT_CONSECUTIVE_CLOSES,
    trail_consecutive_closes: int = DEFAULT_TRAIL_CONSECUTIVE_CLOSES,
    block_ob_distance_mult: float = DEFAULT_BLOCK_OB_DISTANCE_MULT,
    block_sr_distance_mult: float = DEFAULT_BLOCK_SR_DISTANCE_MULT,
    min_sr_strength: float = DEFAULT_MIN_SR_STRENGTH,
    trail_sr_min_strength: float = DEFAULT_TRAIL_SR_MIN_STRENGTH,
    trail_param: float = DEFAULT_TRAIL_PARAM,
    max_ob_entry_signals: int = DEFAULT_MAX_OB_ENTRY_SIGNALS,
    atr_length: int = DEFAULT_ATR_LENGTH,
    atr_stop_mult: float = DEFAULT_ATR_STOP_MULT,
    breakeven_body_frac: float = DEFAULT_BREAKEVEN_BODY_FRAC,
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
    pending_long: _PendingSignal | None = None
    pending_short: _PendingSignal | None = None
    position: _ActivePosition | None = None
    vol_lookback = 20
    ob_signal_counts: dict[tuple[float, float, int], int] = {}

    for i, c, bullish_ob, bearish_ob in _iter_order_blocks(candles):
        raw_events = _detect_ob_events(
            i, c, candles, bullish_ob, bearish_ob,
            entry_zone_mult=entry_zone_mult,
            max_ob_entry_signals=max_ob_entry_signals,
            ob_signal_counts=ob_signal_counts,
        )
        time_s = c.time // 1000
        vol_avg = _volume_average(candles, vol_lookback, i + 1)
        is_bull = _is_bullish_trend(candle_colors, c.time)
        is_bear = _is_bearish_trend(candle_colors, c.time)

        # --- Debug: log raw events and state when triggers present ---
        # if raw_events:
        #     ev_types = [e["type"] for e in raw_events]
        #     logger.info(
        #         "[OB_STRAT] bar=%d time=%d | raw_events=%s | position=%s | is_bull=%s is_bear=%s | "
        #         "ohlc=(%.1f,%.1f,%.1f,%.1f) vol=%.1f vol_avg=%.1f",
        #         i, time_s, ev_types,
        #         f"{position.side}@{position.entry_price}" if position else None,
        #         is_bull, is_bear,
        #         c.open, c.high, c.low, c.close, c.volume, vol_avg,
        #     )

        # --- Check pending confirmation (only on bar N+1; "two consecutive closes" = trigger bar + next bar) ---
        if pending_long and is_bull and not position and i == pending_long.bar_index + 1:
            if c.close > pending_long.ob_top:
                # Confirmed: 2nd consecutive close above
                ob_width = pending_long.ob_width
                entry = c.close
                # Blocking
                bear_ob_closest = _get_closest_bearish_ob_below(bearish_ob, entry, active_only=True)
                if bear_ob_closest is not None:
                    dist_to_bear = entry - bear_ob_closest
                    if dist_to_bear < block_ob_distance_mult * ob_width:
                        pending_long = None
                        continue
                resistance = _get_closest_resistance_above(sr_lines, entry, min_sr_strength)
                if resistance is not None:
                    dist_to_sr = resistance[0] - entry
                    if dist_to_sr < block_sr_distance_mult * ob_width:
                        pending_long = None
                        continue
                stop = _compute_initial_stop_long(
                    pending_long.ob_bottom,
                    sr_lines,
                    entry,
                    min_sr_strength,
                    candles=candles,
                    bar_index=i,
                    atr_length=atr_length,
                    atr_stop_mult=atr_stop_mult,
                )
                events.append(
                    TradeEvent(
                        time=time_s,
                        bar_index=i,
                        type="OB_TREND_BUY",
                        side="long",
                        price=entry,
                        target_price=None,
                        initial_stop_price=stop,
                        context={
                            "ob_top": pending_long.ob_top,
                            "ob_bottom": pending_long.ob_bottom,
                            "trigger": pending_long.event_type,
                        },
                    )
                )
                position = _ActivePosition(
                    side="long",
                    entry_price=entry,
                    entry_bar=i,
                    stop_price=stop,
                    trigger_ob_top=pending_long.ob_top,
                    trigger_ob_bottom=pending_long.ob_bottom,
                )
                stop_segments.append(
                    StopSegment(start_time=time_s, end_time=time_s, price=stop, side="long")
                )
                pending_long = None  # Consumed by entry
            else:
                pending_long = None  # Lost confirmation
        else:
            pending_long = None
        if pending_short and is_bear and not position and i == pending_short.bar_index + 1:
            if c.close < pending_short.ob_bottom:
                ob_width = pending_short.ob_width
                entry = c.close
                bull_ob_closest = _get_closest_bullish_ob_above(bullish_ob, entry, active_only=True)
                if bull_ob_closest is not None:
                    dist_to_bull = bull_ob_closest - entry
                    if dist_to_bull < block_ob_distance_mult * ob_width:
                        pending_short = None
                        continue
                support = _get_closest_support_below(sr_lines, entry, min_sr_strength)
                if support is not None:
                    dist_to_sr = entry - support[0]
                    if dist_to_sr < block_sr_distance_mult * ob_width:
                        pending_short = None
                        continue
                stop = _compute_initial_stop_short(
                    pending_short.ob_top,
                    sr_lines,
                    entry,
                    min_sr_strength,
                    candles=candles,
                    bar_index=i,
                    atr_length=atr_length,
                    atr_stop_mult=atr_stop_mult,
                )
                events.append(
                    TradeEvent(
                        time=time_s,
                        bar_index=i,
                        type="OB_TREND_SELL",
                        side="short",
                        price=entry,
                        target_price=None,
                        initial_stop_price=stop,
                        context={
                            "ob_top": pending_short.ob_top,
                            "ob_bottom": pending_short.ob_bottom,
                            "trigger": pending_short.event_type,
                        },
                    )
                )
                # logger.info(
                #     "[OB_STRAT] ENTRY SHORT (pending) bar=%d time=%d price=%.1f stop=%.1f ob=[%.1f,%.1f] trigger=%s",
                #     i, time_s, entry, stop, pending_short.ob_top, pending_short.ob_bottom, pending_short.event_type,
                # )
                position = _ActivePosition(
                    side="short",
                    entry_price=entry,
                    entry_bar=i,
                    stop_price=stop,
                    trigger_ob_top=pending_short.ob_top,
                    trigger_ob_bottom=pending_short.ob_bottom,
                )
                stop_segments.append(
                    StopSegment(start_time=time_s, end_time=time_s, price=stop, side="short")
                )
                pending_short = None  # Consumed by entry
            else:
                pending_short = None
        else:
            pending_short = None

        # --- Process raw events (triggers) ---
        for ev in raw_events:
            t = ev["type"]
            ob_top, ob_bottom = ev["ob_top"], ev["ob_bottom"]
            ob_width = ob_top - ob_bottom
            # When OB created on bar i, trigger may be bar i-1 (the crossing bar)
            trigger_bar = ev.get("trigger_bar", i)
            trigger_c = candles[trigger_bar]

            if t in ("bullish_boundary_crossed", "bullish_breaker_created") and is_bull and not position:
                # Buy trigger: use trigger bar for volume/entry when it's the crossing bar
                confirmed = False
                vol_avg_trigger = _volume_average(candles, vol_lookback, trigger_bar + 1)
                vol_spike_ok = vol_avg_trigger > 0 and trigger_c.volume >= volume_spike_mult * vol_avg_trigger
                consec_ok = trigger_bar >= 1 and candles[trigger_bar - 1].close > ob_top and trigger_c.close > ob_top
                if vol_spike_ok:
                    confirmed = True
                if consec_ok:
                    confirmed = True

                if confirmed:
                    entry = trigger_c.close
                    if entry <= ob_top:
                        continue  # Price must be above OB for long
                    bear_ob_closest = _get_closest_bearish_ob_below(bearish_ob, entry)
                    bear_blocked = bear_ob_closest is not None and (entry - bear_ob_closest) < block_ob_distance_mult * ob_width
                    if bear_blocked:
                        continue
                    support = _get_closest_support_below(sr_lines, entry, min_sr_strength)
                    sr_blocked = support is not None and (entry - support[0]) < block_sr_distance_mult * ob_width
                    if sr_blocked:
                        continue
                    trigger_time_s = trigger_c.time // 1000
                    stop = _compute_initial_stop_long(
                        ob_bottom,
                        sr_lines,
                        entry,
                        min_sr_strength,
                        candles=candles,
                        bar_index=trigger_bar,
                        atr_length=atr_length,
                        atr_stop_mult=atr_stop_mult,
                    )
                    events.append(
                        TradeEvent(
                            time=trigger_time_s,
                            bar_index=trigger_bar,
                            type="OB_TREND_BUY",
                            side="long",
                            price=entry,
                            target_price=None,
                            initial_stop_price=stop,
                            context={"ob_top": ob_top, "ob_bottom": ob_bottom, "trigger": t},
                        )
                    )

                    position = _ActivePosition(
                        side="long", entry_price=entry, entry_bar=trigger_bar,
                        stop_price=stop, trigger_ob_top=ob_top, trigger_ob_bottom=ob_bottom,
                    )
                    stop_segments.append(StopSegment(start_time=trigger_time_s, end_time=trigger_time_s, price=stop, side="long"))
                else:
                    pending_long = _PendingSignal(
                        bar_index=trigger_bar, event_type=t, ob_top=ob_top, ob_bottom=ob_bottom, ob_width=ob_width, side="long"
                    )

            elif t in ("bearish_boundary_crossed", "bearish_breaker_created") and is_bear and not position:
                # Sell trigger: use trigger bar for volume/entry when it's the crossing bar
                confirmed = False
                vol_avg_trigger = _volume_average(candles, vol_lookback, trigger_bar + 1)
                if vol_avg_trigger > 0 and trigger_c.volume >= volume_spike_mult * vol_avg_trigger:
                    confirmed = True
                if trigger_bar >= 1 and candles[trigger_bar - 1].close < ob_bottom and trigger_c.close < ob_bottom:
                    confirmed = True
                if confirmed:
                    entry = trigger_c.close
                    if entry >= ob_bottom:
                        continue  # Price must be below OB for short
                    bull_ob_closest = _get_closest_bullish_ob_above(bullish_ob, entry, active_only=True)
                    if bull_ob_closest is not None and (bull_ob_closest - entry) < block_ob_distance_mult * ob_width:
                        continue
                    support = _get_closest_support_below(sr_lines, entry, min_sr_strength)
                    if support is not None and (entry - support[0]) < block_sr_distance_mult * ob_width:
                        continue
                    trigger_time_s = trigger_c.time // 1000
                    stop = _compute_initial_stop_short(
                        ob_top,
                        sr_lines,
                        entry,
                        min_sr_strength,
                        candles=candles,
                        bar_index=trigger_bar,
                        atr_length=atr_length,
                        atr_stop_mult=atr_stop_mult,
                    )
                    events.append(
                        TradeEvent(
                            time=trigger_time_s,
                            bar_index=trigger_bar,
                            type="OB_TREND_SELL",
                            side="short",
                            price=entry,
                            target_price=None,
                            initial_stop_price=stop,
                            context={"ob_top": ob_top, "ob_bottom": ob_bottom, "trigger": t},
                        )
                    )
                    position = _ActivePosition(
                        side="short", entry_price=entry, entry_bar=trigger_bar,
                        stop_price=stop, trigger_ob_top=ob_top, trigger_ob_bottom=ob_bottom,
                    )
                    stop_segments.append(StopSegment(start_time=trigger_time_s, end_time=trigger_time_s, price=stop, side="short"))
                else:
                    pending_short = _PendingSignal(
                        bar_index=trigger_bar, event_type=t, ob_top=ob_top, ob_bottom=ob_bottom, ob_width=ob_width, side="short"
                    )

        # --- Trailing stop for active position ---
        # Position open price = entry bar close (we enter on bar close when conditions met)
        if position and prev_candle is not None:
            if position.side == "long":
                # Breakeven: trail toward entry (position open = entry bar close) with relaxed confirmation
                # (1 bar close above entry is enough; no volume/consecutive required)
                if position.entry_price > position.stop_price and c.close > position.entry_price:
                    new_stop = position.entry_price - trail_param * (
                        position.entry_price - position.stop_price
                    )
                    if new_stop > position.stop_price:
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "long":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time, end_time=time_s, price=last.price, side="long"
                            )
                        stop_segments.append(
                            StopSegment(start_time=time_s, end_time=time_s, price=new_stop, side="long")
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
                    breakeven_target = position.entry_price + breakeven_body_frac * (ec.close - ec.open)
                    levels.append(breakeven_target)
                crossed = _confirmed_level_cross_long(
                    candles, i, prev_candle, levels, position.stop_price,
                    volume_spike_mult, trail_consecutive_closes, vol_lookback,
                )
                if crossed is not None:
                    new_stop = crossed - trail_param * (crossed - position.stop_price)
                    if new_stop > position.stop_price:
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "long":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time, end_time=time_s, price=last.price, side="long"
                            )
                        stop_segments.append(StopSegment(start_time=time_s, end_time=time_s, price=new_stop, side="long"))
                elif stop_segments and stop_segments[-1].side == "long":
                    last = stop_segments[-1]
                    stop_segments[-1] = StopSegment(
                        start_time=last.start_time, end_time=time_s, price=position.stop_price, side="long"
                    )
                if c.low <= position.stop_price:
                    # logger.info(
                    #     "[OB_STRAT] STOP HIT (long) bar=%d time=%d low=%.1f stop=%.1f",
                    #     i, time_s, c.low, position.stop_price,
                    # )
                    position = None
            else:
                # Breakeven: trail toward entry (position open = entry bar close) with relaxed confirmation
                if position.entry_price < position.stop_price and c.close < position.entry_price:
                    new_stop = position.entry_price + trail_param * (
                        position.stop_price - position.entry_price
                    )
                    if new_stop < position.stop_price:
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "short":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time, end_time=time_s, price=last.price, side="short"
                            )
                        stop_segments.append(
                            StopSegment(start_time=time_s, end_time=time_s, price=new_stop, side="short")
                        )
                # S/R resistance + bearish OB bottoms + bullish breaker tops (act as resistance when broken)
                # + entry price + optional breakeven target (entry + frac*body)
                levels = [l["price"] for l in sr_lines if l.get("width", 0) >= trail_sr_min_strength]
                levels.extend([ob.bottom for ob in bearish_ob])
                levels.extend([ob.top for ob in bullish_ob if ob.breaker])
                levels.append(position.entry_price)  # Position open = entry bar close
                if breakeven_body_frac > 0 and 0 <= position.entry_bar < len(candles):
                    ec = candles[position.entry_bar]
                    breakeven_target = position.entry_price + breakeven_body_frac * (ec.close - ec.open)
                    levels.append(breakeven_target)
                crossed = _confirmed_level_cross_short(
                    candles, i, prev_candle, levels, position.stop_price,
                    volume_spike_mult, trail_consecutive_closes, vol_lookback,
                )
                if crossed is not None:
                    new_stop = crossed + trail_param * (position.stop_price - crossed)
                    if new_stop < position.stop_price:
                        position.stop_price = new_stop
                        if stop_segments and stop_segments[-1].side == "short":
                            last = stop_segments[-1]
                            stop_segments[-1] = StopSegment(
                                start_time=last.start_time, end_time=time_s, price=last.price, side="short"
                            )
                        stop_segments.append(StopSegment(start_time=time_s, end_time=time_s, price=new_stop, side="short"))
                elif stop_segments and stop_segments[-1].side == "short":
                    last = stop_segments[-1]
                    stop_segments[-1] = StopSegment(
                        start_time=last.start_time, end_time=time_s, price=position.stop_price, side="short"
                    )
                if c.high >= position.stop_price:
                    # logger.info(
                    #     "[OB_STRAT] STOP HIT (short) bar=%d time=%d high=%.1f stop=%.1f",
                    #     i, time_s, c.high, position.stop_price,
                    # )
                    position = None

        prev_candle = c

    return events, stop_segments

"""Order Block Trend-Following strategy. See docs/strategy-order-block-trend-following.md."""

from dataclasses import dataclass

from app.schemas.market import Candle
from app.services.indicators.order_blocks import _iter_order_blocks_with_events, OrderBlock
from app.services.trading_strategy.types import TradeEvent, StopSegment

# Candle colors from smart_money_structure (green = bullish, red = bearish)
BULLISH_COLORS = {"#22c55e", "#15803d"}
BEARISH_COLORS = {"#dc2626", "#b91c1c"}

# Default parameters
DEFAULT_VOLUME_SPIKE_MULT = 2.0
DEFAULT_CONSECUTIVE_CLOSES = 2
DEFAULT_BLOCK_OB_DISTANCE_MULT = 2.0
DEFAULT_BLOCK_SR_DISTANCE_MULT = 2.0
DEFAULT_MIN_SR_STRENGTH = 4.0
DEFAULT_TRAIL_PARAM = 0.75


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
) -> float | None:
    """Closest bearish OB top below price."""
    tops_below = [ob.top for ob in bearish_ob if ob.top < price]
    return max(tops_below) if tops_below else None


def _get_closest_bullish_ob_above(
    bullish_ob: list[OrderBlock],
    price: float,
) -> float | None:
    """Closest bullish OB bottom above price."""
    bottoms_above = [ob.bottom for ob in bullish_ob if ob.bottom > price]
    return min(bottoms_above) if bottoms_above else None


def _compute_initial_stop_long(
    ob_bottom: float,
    sr_lines: list[dict],
    entry_price: float,
    min_strength: float,
) -> float:
    """Higher of OB bottom or support-gap (tighter = better for long)."""
    support = _get_closest_support_below(sr_lines, entry_price, min_strength)
    if support is None:
        return ob_bottom
    support_price = support[0]
    gap = (entry_price - support_price) / 2
    stop_below_support = support_price - gap
    return max(ob_bottom, stop_below_support)  # Higher stop = tighter risk


def _compute_initial_stop_short(
    ob_top: float,
    sr_lines: list[dict],
    entry_price: float,
    min_strength: float,
) -> float:
    """Lower of OB top or resistance+gap (tighter = better for short)."""
    resistance = _get_closest_resistance_above(sr_lines, entry_price, min_strength)
    if resistance is None:
        return ob_top
    res_price = resistance[0]
    gap = (res_price - entry_price) / 2
    stop_above_res = res_price + gap
    return min(ob_top, stop_above_res)


def _crossed_higher_level_long(
    close: float,
    prev_low: float,
    levels: list[float],
    current_stop: float,
) -> float | None:
    """Check if price crossed a level above current stop; return the highest crossed level."""
    crossed = [p for p in levels if p > current_stop and prev_low < p <= close]
    return max(crossed) if crossed else None


def _crossed_lower_level_short(
    close: float,
    prev_high: float,
    levels: list[float],
    current_stop: float,
) -> float | None:
    crossed = [p for p in levels if p < current_stop and prev_high > p >= close]
    return min(crossed) if crossed else None


def compute_order_block_trend_following(
    candles: list[Candle],
    candle_colors: dict[int, str] | None = None,
    sr_lines: list[dict] | None = None,
    *,
    volume_spike_mult: float = DEFAULT_VOLUME_SPIKE_MULT,
    consecutive_closes: int = DEFAULT_CONSECUTIVE_CLOSES,
    block_ob_distance_mult: float = DEFAULT_BLOCK_OB_DISTANCE_MULT,
    block_sr_distance_mult: float = DEFAULT_BLOCK_SR_DISTANCE_MULT,
    min_sr_strength: float = DEFAULT_MIN_SR_STRENGTH,
    trail_param: float = DEFAULT_TRAIL_PARAM,
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

    prev_candle: Candle | None = None

    for i, c, bullish_ob, bearish_ob, raw_events in _iter_order_blocks_with_events(candles):
        time_s = c.time // 1000
        vol_avg = _volume_average(candles, vol_lookback, i + 1)
        is_bull = _is_bullish_trend(candle_colors, c.time)
        is_bear = _is_bearish_trend(candle_colors, c.time)

        # --- Check pending confirmation ---
        if pending_long and is_bull:
            if c.close > pending_long.ob_top:
                # Confirmed: 2nd consecutive close above
                ob_width = pending_long.ob_width
                entry = c.close
                # Blocking
                bear_ob_closest = _get_closest_bearish_ob_below(bearish_ob, entry)
                if bear_ob_closest is not None:
                    dist_to_bear = entry - bear_ob_closest
                    if dist_to_bear < block_ob_distance_mult * ob_width:
                        pending_long = None
                        continue
                support = _get_closest_support_below(sr_lines, entry, min_sr_strength)
                if support is not None:
                    dist_to_sr = entry - support[0]
                    if dist_to_sr < block_sr_distance_mult * ob_width:
                        pending_long = None
                        continue
                stop = _compute_initial_stop_long(
                    pending_long.ob_bottom, sr_lines, entry, min_sr_strength
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
            else:
                pending_long = None  # Lost confirmation
        else:
            pending_long = None
        if pending_short and is_bear:
            if c.close < pending_short.ob_bottom:
                ob_width = pending_short.ob_width
                entry = c.close
                bull_ob_closest = _get_closest_bullish_ob_above(bullish_ob, entry)
                if bull_ob_closest is not None:
                    dist_to_bull = bull_ob_closest - entry
                    if dist_to_bull < block_ob_distance_mult * ob_width:
                        pending_short = None
                        continue
                resistance = _get_closest_resistance_above(sr_lines, entry, min_sr_strength)
                if resistance is not None:
                    dist_to_sr = resistance[0] - entry
                    if dist_to_sr < block_sr_distance_mult * ob_width:
                        pending_short = None
                        continue
                stop = _compute_initial_stop_short(
                    pending_short.ob_top, sr_lines, entry, min_sr_strength
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
            else:
                pending_short = None
        else:
            pending_short = None

        # --- Process raw events (triggers) ---
        for ev in raw_events:
            t = ev["type"]
            ob_top, ob_bottom = ev["ob_top"], ev["ob_bottom"]
            ob_width = ob_top - ob_bottom

            if t in ("bullish_boundary_crossed", "bullish_breaker_created") and is_bull and not position:
                # Buy trigger
                confirmed = False
                if vol_avg > 0 and c.volume >= volume_spike_mult * vol_avg:
                    confirmed = True
                if i >= 1 and candles[i - 1].close > ob_top and c.close > ob_top:
                    confirmed = True
                if confirmed:
                    # Immediate confirmation - emit now
                    entry = c.close
                    bear_ob_closest = _get_closest_bearish_ob_below(bearish_ob, entry)
                    if bear_ob_closest is not None and (entry - bear_ob_closest) < block_ob_distance_mult * ob_width:
                        continue
                    support = _get_closest_support_below(sr_lines, entry, min_sr_strength)
                    if support is not None and (entry - support[0]) < block_sr_distance_mult * ob_width:
                        continue
                    stop = _compute_initial_stop_long(ob_bottom, sr_lines, entry, min_sr_strength)
                    events.append(
                        TradeEvent(
                            time=time_s,
                            bar_index=i,
                            type="OB_TREND_BUY",
                            side="long",
                            price=entry,
                            target_price=None,
                            initial_stop_price=stop,
                            context={"ob_top": ob_top, "ob_bottom": ob_bottom, "trigger": t},
                        )
                    )
                    position = _ActivePosition(
                        side="long", entry_price=entry, entry_bar=i,
                        stop_price=stop, trigger_ob_top=ob_top, trigger_ob_bottom=ob_bottom,
                    )
                    stop_segments.append(StopSegment(start_time=time_s, end_time=time_s, price=stop, side="long"))
                else:
                    pending_long = _PendingSignal(
                        bar_index=i, event_type=t, ob_top=ob_top, ob_bottom=ob_bottom, ob_width=ob_width, side="long"
                    )

            elif t in ("bearish_boundary_crossed", "bearish_breaker_created") and is_bear and not position:
                # Sell trigger
                confirmed = False
                if vol_avg > 0 and c.volume >= volume_spike_mult * vol_avg:
                    confirmed = True
                if i >= 1 and candles[i - 1].close < ob_bottom and c.close < ob_bottom:
                    confirmed = True
                if confirmed:
                    entry = c.close
                    bull_ob_closest = _get_closest_bullish_ob_above(bullish_ob, entry)
                    if bull_ob_closest is not None and (bull_ob_closest - entry) < block_ob_distance_mult * ob_width:
                        continue
                    resistance = _get_closest_resistance_above(sr_lines, entry, min_sr_strength)
                    if resistance is not None and (resistance[0] - entry) < block_sr_distance_mult * ob_width:
                        continue
                    stop = _compute_initial_stop_short(ob_top, sr_lines, entry, min_sr_strength)
                    events.append(
                        TradeEvent(
                            time=time_s,
                            bar_index=i,
                            type="OB_TREND_SELL",
                            side="short",
                            price=entry,
                            target_price=None,
                            initial_stop_price=stop,
                            context={"ob_top": ob_top, "ob_bottom": ob_bottom, "trigger": t},
                        )
                    )
                    position = _ActivePosition(
                        side="short", entry_price=entry, entry_bar=i,
                        stop_price=stop, trigger_ob_top=ob_top, trigger_ob_bottom=ob_bottom,
                    )
                    stop_segments.append(StopSegment(start_time=time_s, end_time=time_s, price=stop, side="short"))
                else:
                    pending_short = _PendingSignal(
                        bar_index=i, event_type=t, ob_top=ob_top, ob_bottom=ob_bottom, ob_width=ob_width, side="short"
                    )

        # --- Trailing stop for active position ---
        if position and prev_candle is not None:
            if position.side == "long":
                levels = [l["price"] for l in sr_lines if l.get("width", 0) >= min_sr_strength]
                levels.extend([ob.top for ob in bullish_ob])
                crossed = _crossed_higher_level_long(c.close, prev_candle.low, levels, position.stop_price)
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
                    position = None
            else:
                levels = [l["price"] for l in sr_lines if l.get("width", 0) >= min_sr_strength]
                levels.extend([ob.bottom for ob in bearish_ob])
                crossed = _crossed_lower_level_short(c.close, prev_candle.high, levels, position.stop_price)
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
                    position = None

        prev_candle = c

    return events, stop_segments

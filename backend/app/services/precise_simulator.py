"""Precise simulation engine: prefix-only, no-future-leakage evaluation.

This module reuses the *bar-by-bar* logic we previously proved out in
`candle_stream` (before it was rolled back for performance). It computes
indicators and strategy on prefixes ending at each bar and aggregates only the
signals that belong to that bar, so that:

- No decision on bar i can depend on candles with index > i.
- The resulting `strategySignals` (markers, events, stopSegments) can be used
  by the frontend to build the same results table semantics as the streaming
  simulation path, but without future data injection.

Because this is only called on demand (via a dedicated API), the higher
computational cost is acceptable.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.schemas.market import Candle
from app.services.indicators.smart_money_structure import compute_structure
from app.services.indicators.support_resistance import compute_support_resistance_lines
from app.services.indicators.volume_profile import build_volume_profile_from_candles
from app.services.trading_strategy.chart_format import strategy_output_to_chart
from app.services.trading_strategy.order_block_trend_following import (
    compute_order_block_trend_following,
)
from app.services.trading_strategy.types import StopSegment, TradeEvent


def run_precise_simulation(
    *,
    symbol: str,
    interval: str,
    candles: List[Candle],
    volume_profile_window: int = 2000,
    bars_window: int | None = None,
) -> Dict[str, Any]:
    """Run precise, no-future simulation over the given candles.

    For each bar i, indicators and strategy are computed only from candles
    up to and including bar i (or a trailing window ending at i). No bar
    with index > i is ever used in the computation for bar i.
    """
    if not candles:
        return {"symbol": symbol, "interval": interval, "strategySignals": None}

    n = len(candles)
    if bars_window is None or bars_window <= 0:
        bars_window = volume_profile_window
    max_window = max(1, min(bars_window, n))

    all_events: List[TradeEvent] = []
    all_stops: List[StopSegment] = []

    for i in range(n):
        # Define prefix / trailing window ending at bar i (inclusive).
        start_idx = max(0, i - max_window + 1)
        window = candles[start_idx : i + 1]

        # Indicators for this prefix only.
        prefix_structure = compute_structure(
            window,
            include_candle_colors=True,
        )
        vp_prefix = build_volume_profile_from_candles(
            window,
            time=window[-1].time // 1000,
            width=6,
            window_size=min(len(window), volume_profile_window),
        )
        if not vp_prefix:
            # Without volume profile, S/R lines (and thus strategy) are undefined.
            continue
        sr_prefix = compute_support_resistance_lines(vp_prefix["profile"])

        # Run strategy on the prefix and keep only events for the last bar in this window.
        trade_events_i, stop_segments_i = compute_order_block_trend_following(
            window,
            prefix_structure.get("swingPivots") or {},
            candle_colors=prefix_structure.get("candleColors"),
            sr_lines=sr_prefix,
        )
        # Local bar index within the window corresponds to global index i.
        local_idx = len(window) - 1
        if not trade_events_i and not stop_segments_i:
            continue

        for ev in trade_events_i:
            if ev.bar_index == local_idx:
                all_events.append(
                    TradeEvent(
                        time=ev.time,
                        trade_id=ev.trade_id,
                        bar_index=i,  # rebase to global index
                        type=ev.type,
                        side=ev.side,
                        price=ev.price,
                        target_price=ev.target_price,
                        initial_stop_price=ev.initial_stop_price,
                        context=ev.context,
                    )
                )

        # Stop segments are already time-based; we can keep them as-is and let
        # the chart/result logic decide which segments are active per bar.
        for seg in stop_segments_i:
            all_stops.append(seg)

    strategy_signals = strategy_output_to_chart(all_events, all_stops, interval)

    return {
        "symbol": symbol,
        "interval": interval,
        "strategySignals": strategy_signals,
    }


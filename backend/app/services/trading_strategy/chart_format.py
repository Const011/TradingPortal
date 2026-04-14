"""Convert strategy output to chart display format."""

from app.services.trading_strategy.types import TradeEvent, StopSegment
from app.utils.intervals import interval_seconds


def strategy_output_to_chart(
    events: list[TradeEvent],
    stop_segments: list[StopSegment],
    interval: str,
) -> dict:
    """Convert strategy events + stop segments to graphics payload for chart.
    Includes full events for AI review / data export."""
    markers: list[dict] = []
    events_export: list[dict] = []
    for ev in events:
        if ev.type in ("OB_TREND_BUY", "OB_TREND_SELL") and ev.side == "long":
            markers.append({
                "time": ev.time,
                "position": "below",
                "shape": "arrowUp",
                "color": "#22c55e",
            })
        elif ev.type in ("OB_TREND_BUY", "OB_TREND_SELL") and ev.side == "short":
            markers.append({
                "time": ev.time,
                "position": "above",
                "shape": "arrowDown",
                "color": "#dc2626",
            })
        events_export.append({
            "time": ev.time,
            "tradeId": ev.trade_id,
            "barIndex": ev.bar_index,
            "type": ev.type,
            "side": ev.side,
            "price": ev.price,
            "targetPrice": ev.target_price,
            "initialStopPrice": ev.initial_stop_price,
            "context": ev.context,
        })

    stop_segments_export: list[dict] = []
    for seg in stop_segments:
        stop_segments_export.append(
            {
                "startTime": seg.start_time,
                "endTime": seg.end_time,
                "tradeId": seg.trade_id,
                "price": seg.price,
                "side": seg.side,
            }
        )

    # Build stop lines as polyline per trade.
    # For each trade we:
    # - Draw an initial horizontal segment from (first_time - interval_sec) to first_time at first_price.
    # - Connect successive nodes (time, price) with straight segments.
    # - Extend the last node horizontally to the segment's final end_time.
    stop_lines: list[dict] = []
    # For mapping targets to position lifetimes, keep (start,end) per trade_id.
    cluster_range_by_trade_id: dict[str, tuple[int, int]] = {}
    if stop_segments:
        interval_sec = interval_seconds(interval, default=0)
        by_trade_id: dict[str, list[StopSegment]] = {}
        for seg in stop_segments:
            by_trade_id.setdefault(seg.trade_id, []).append(seg)

        for trade_id, segs in by_trade_id.items():
            if not segs:
                continue
            segs_sorted = sorted(segs, key=lambda s: s.start_time)
            # Nodes are (start_time, price) for each segment in this trade.
            nodes: list[tuple[int, float]] = [
                (s.start_time, s.price) for s in segs_sorted
            ]
            deduped_nodes: list[tuple[int, float]] = []
            for t, p in nodes:
                if not deduped_nodes or (t, p) != deduped_nodes[-1]:
                    deduped_nodes.append((t, p))
            if not deduped_nodes:
                continue

            first_time, first_price = deduped_nodes[0]
            last_seg = segs_sorted[-1]
            last_end_time = last_seg.end_time
            cluster_range_by_trade_id[trade_id] = (first_time, last_end_time)

            initial_start_time = (
                first_time - interval_sec if interval_sec > 0 else first_time
            )
            stop_lines.append(
                {
                    "type": "lineSegment",
                    "from": {"time": initial_start_time, "price": first_price},
                    "to": {"time": first_time, "price": first_price},
                    "color": "#f59e0b",
                    "width": 2,
                    "style": "dashed",
                }
            )

            for (t_prev, p_prev), (t_cur, p_cur) in zip(
                deduped_nodes, deduped_nodes[1:]
            ):
                stop_lines.append(
                    {
                        "type": "lineSegment",
                        "from": {"time": t_prev, "price": p_prev},
                        "to": {"time": t_cur, "price": p_cur},
                        "color": "#f59e0b",
                        "width": 2,
                        "style": "dashed",
                    }
                )

            if last_end_time > deduped_nodes[-1][0]:
                stop_lines.append(
                    {
                        "type": "lineSegment",
                        "from": {
                            "time": deduped_nodes[-1][0],
                            "price": deduped_nodes[-1][1],
                        },
                        "to": {
                            "time": last_end_time,
                            "price": deduped_nodes[-1][1],
                        },
                        "color": "#f59e0b",
                        "width": 2,
                        "style": "dashed",
                    }
                )

    # Take-profit target lines: draw the target level from entry time until the
    # end of the corresponding trade's lifetime.
    target_lines: list[dict] = []
    interval_sec = interval_seconds(interval, default=0)
    for ev in events:
        if ev.target_price is None:
            continue
        price = ev.target_price
        start_time = ev.time
        end_time: int | None = None
        trade_range = cluster_range_by_trade_id.get(ev.trade_id)
        if trade_range is not None:
            _cluster_start, cluster_end = trade_range
            end_time = cluster_end

        # Fallback: one-bar segment when we have no stop cluster information.
        if end_time is None:
            end_time = start_time + interval_sec if interval_sec > 0 else start_time

        target_lines.append(
            {
                "type": "lineSegment",
                "from": {"time": start_time, "price": price},
                "to": {"time": end_time, "price": price},
                "color": "#22c55e" if ev.side == "long" else "#ef4444",
                "width": 1,
                "style": "solid",
            }
        )

    return {
        "markers": markers,
        "stopLines": stop_lines,
        "targetLines": target_lines,
        "events": events_export,
        "stopSegments": stop_segments_export,
    }

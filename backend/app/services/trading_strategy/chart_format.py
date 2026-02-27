"""Convert strategy output to chart display format."""

from app.services.trading_strategy.types import TradeEvent, StopSegment


def strategy_output_to_chart(
    events: list[TradeEvent],
    stop_segments: list[StopSegment],
) -> dict:
    """Convert strategy events + stop segments to graphics payload for chart.
    Includes full events for AI review / data export."""
    markers: list[dict] = []
    events_export: list[dict] = []
    for ev in events:
        if ev.side == "long":
            markers.append({
                "time": ev.time,
                "position": "below",
                "shape": "arrowUp",
                "color": "#22c55e",
            })
        elif ev.side == "short":
            markers.append({
                "time": ev.time,
                "position": "above",
                "shape": "arrowDown",
                "color": "#dc2626",
            })
        events_export.append({
            "time": ev.time,
            "barIndex": ev.bar_index,
            "type": ev.type,
            "side": ev.side,
            "price": ev.price,
            "targetPrice": ev.target_price,
            "initialStopPrice": ev.initial_stop_price,
            "context": ev.context,
        })

    stop_lines: list[dict] = []
    stop_segments_export: list[dict] = []
    for seg in stop_segments:
        stop_lines.append({
            "type": "lineSegment",
            "from": {"time": seg.start_time, "price": seg.price},
            "to": {"time": seg.end_time, "price": seg.price},
            "color": "#f59e0b",
            "width": 2,
            "style": "dashed",
        })
        stop_segments_export.append({
            "startTime": seg.start_time,
            "endTime": seg.end_time,
            "price": seg.price,
            "side": seg.side,
        })

    return {
        "markers": markers,
        "stopLines": stop_lines,
        "events": events_export,
        "stopSegments": stop_segments_export,
    }

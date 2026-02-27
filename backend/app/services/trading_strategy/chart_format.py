"""Convert strategy output to chart display format."""

from app.services.trading_strategy.types import TradeEvent, StopSegment


def strategy_output_to_chart(
    events: list[TradeEvent],
    stop_segments: list[StopSegment],
) -> dict:
    """Convert strategy events + stop segments to graphics payload for chart."""
    markers: list[dict] = []
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

    stop_lines: list[dict] = []
    for seg in stop_segments:
        stop_lines.append({
            "type": "lineSegment",
            "from": {"time": seg.start_time, "price": seg.price},
            "to": {"time": seg.end_time, "price": seg.price},
            "color": "#f59e0b",
            "width": 2,
            "style": "dashed",
        })

    return {
        "markers": markers,
        "stopLines": stop_lines,
    }

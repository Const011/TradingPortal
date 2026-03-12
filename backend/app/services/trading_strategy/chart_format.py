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

    stop_segments_export: list[dict] = []
    for seg in stop_segments:
        stop_segments_export.append(
            {
                "startTime": seg.start_time,
                "endTime": seg.end_time,
                "price": seg.price,
                "side": seg.side,
            }
        )

    # Build stop lines as polyline between successive stop levels per side.
    # For each side we:
    # - Draw an initial horizontal segment from (first_time - interval_sec) to first_time at first_price.
    # - Connect successive nodes (time, price) with straight segments.
    # - Extend the last node horizontally to the segment's final end_time.
    stop_lines: list[dict] = []
    # For mapping targets to position lifetimes, keep (start,end) for each
    # stop cluster per side in chronological order.
    cluster_ranges_by_side: dict[str, list[tuple[int, int]]] = {}
    if stop_segments:
        interval_sec = interval_seconds(interval, default=0)
        by_side: dict[str, list[StopSegment]] = {}
        for seg in stop_segments:
            by_side.setdefault(seg.side, []).append(seg)

        for side, segs in by_side.items():
            if not segs:
                continue
            segs_sorted = sorted(segs, key=lambda s: s.start_time)

            # Cluster segments to avoid connecting across long gaps (likely different trades).
            clusters: list[list[StopSegment]] = []
            current_cluster: list[StopSegment] = []
            prev_end: int | None = None
            gap_threshold = int(interval_sec * 1.5) if interval_sec > 0 else 0

            for seg in segs_sorted:
                if prev_end is None:
                    current_cluster = [seg]
                    prev_end = seg.end_time
                    continue
                if gap_threshold and seg.start_time - prev_end > gap_threshold:
                    if current_cluster:
                        clusters.append(current_cluster)
                    current_cluster = [seg]
                else:
                    current_cluster.append(seg)
                prev_end = seg.end_time
            if current_cluster:
                clusters.append(current_cluster)

            for cluster in clusters:
                if not cluster:
                    continue
                # Nodes are (start_time, price) for each segment in cluster.
                nodes: list[tuple[int, float]] = [
                    (s.start_time, s.price) for s in cluster
                ]
                # Deduplicate consecutive identical nodes.
                deduped_nodes: list[tuple[int, float]] = []
                for t, p in nodes:
                    if not deduped_nodes or (t, p) != deduped_nodes[-1]:
                        deduped_nodes.append((t, p))
                if not deduped_nodes:
                    continue

                first_time, first_price = deduped_nodes[0]
                last_seg = cluster[-1]
                last_end_time = last_seg.end_time

                # Record lifetime range for this side/cluster so targets can be
                # drawn until the position closes (similar to stop lines).
                cluster_ranges_by_side.setdefault(side, []).append(
                    (first_time, last_end_time)
                )

                # Initial horizontal segment for the first bar in this cluster.
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

                # Connect successive nodes with straight segments.
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

                # Final horizontal segment so the last stop remains visible up to last_end_time.
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
    # end of the corresponding position's lifetime (using stop clusters as a proxy).
    target_lines: list[dict] = []
    interval_sec = interval_seconds(interval, default=0)
    # Keep cluster indices per side so we pair entries with clusters in order.
    cluster_index_by_side: dict[str, int] = {side: 0 for side in cluster_ranges_by_side}
    for ev in events:
        if ev.target_price is None:
            continue
        side = ev.side or ""
        price = ev.target_price
        start_time = ev.time

        end_time: int | None = None
        clusters = cluster_ranges_by_side.get(side)
        if clusters:
            idx = cluster_index_by_side.get(side, 0)
            if 0 <= idx < len(clusters):
                cluster_start, cluster_end = clusters[idx]
                # Advance cluster index if this event occurs after current cluster.
                while idx < len(clusters) and start_time > clusters[idx][1]:
                    idx += 1
                if idx < len(clusters):
                    cluster_start, cluster_end = clusters[idx]
                    cluster_index_by_side[side] = idx + 1
                    # Use the cluster that covers or follows this entry as lifetime.
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

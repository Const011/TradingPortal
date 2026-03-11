"""Trade log service: append entry/stop/exit, get trades for API. Used in trading mode only."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


def _format_ts_local(ts: int, unit: str = "s") -> str:
    """Format Unix timestamp to local time 'YYYY-MM-DD HH:mm:ss'."""
    sec = ts if unit == "s" else ts // 1000
    return datetime.fromtimestamp(sec).strftime("%Y-%m-%d %H:%M:%S")

from app.config import settings
from app.schemas.market import Candle
from app.services.trading_strategy.types import TradeEvent, StopSegment
from app.utils.intervals import interval_seconds
from app.utils.timefmt import ts_human

logger = logging.getLogger(__name__)


def _log_dir(symbol: str, interval: str) -> Path:
    """Base dir for symbol/interval: logs/trades/BTCUSDT_60"""
    base = Path(settings.trade_log_dir)
    return base / f"{symbol}_{interval}"


def _index_path(symbol: str, interval: str) -> Path:
    """JSONL index: logs/trades/BTCUSDT_60/index.jsonl"""
    return _log_dir(symbol, interval) / "index.jsonl"


def _current_trades_path(symbol: str, interval: str) -> Path:
    """Current open trades: logs/trades/BTCUSDT_60/current.json"""
    return _log_dir(symbol, interval) / "current.json"


CurrentTrade = dict[str, Any]


def load_current_trades(symbol: str, interval: str) -> list[CurrentTrade]:
    """Load current open trades from file. Used on gateway start to restore state."""
    path = _current_trades_path(symbol, interval)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        trades = data.get("trades", [])
        return trades if isinstance(trades, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Trade log: failed to load current trades %s: %s", path, e)
        return []


def save_current_trades(symbol: str, interval: str, trades: list[CurrentTrade]) -> None:
    """Write current open trades to file."""
    log_dir = _log_dir(symbol, interval)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = _current_trades_path(symbol, interval)
    path.write_text(json.dumps({"trades": trades}, indent=2), encoding="utf-8")


def add_current_trade(
    symbol: str,
    interval: str,
    trade_id: str,
    entry_time: int,
    entry_price: float,
    initial_stop_price: float,
    side: str,
    target_price: float | None = None,
    size: float | None = None,
) -> None:
    """Add trade to current trades file (on entry). size from exchange when executor confirms fill."""
    trades = load_current_trades(symbol, interval)
    t: dict[str, Any] = {
        "tradeId": trade_id,
        "entryTime": entry_time,
        "entryTimeHuman": ts_human(entry_time, unit="s"),
        "entryPrice": entry_price,
        "currentStopPrice": initial_stop_price,
        "initialStopPrice": initial_stop_price,
        "side": side,
        "targetPrice": target_price,
    }
    if size is not None:
        t["size"] = size
    trades.append(t)
    save_current_trades(symbol, interval, trades)
    logger.debug("Trade log: added current trade %s", trade_id)


def update_current_trade_stop(
    symbol: str,
    interval: str,
    trade_id: str,
    current_stop_price: float,
) -> None:
    """Update current stop price for a trade (on stop move)."""
    trades = load_current_trades(symbol, interval)
    for t in trades:
        if t.get("tradeId") == trade_id:
            t["currentStopPrice"] = current_stop_price
            t["currentStopTime"] = int(datetime.utcnow().timestamp())
            t["currentStopTimeHuman"] = ts_human(t["currentStopTime"], unit="s")
            save_current_trades(symbol, interval, trades)
            return
    logger.warning("Trade log: update_current_trade_stop trade_id=%s not found", trade_id)


def remove_current_trade(symbol: str, interval: str, trade_id: str) -> None:
    """Remove trade from current trades file (on exit)."""
    trades = load_current_trades(symbol, interval)
    new_trades = [t for t in trades if t.get("tradeId") != trade_id]
    if len(new_trades) != len(trades):
        save_current_trades(symbol, interval, new_trades)
        logger.debug("Trade log: removed current trade %s", trade_id)


def _snapshot_path(symbol: str, interval: str, trade_id: str) -> Path:
    """Entry snapshot: logs/trades/BTCUSDT_60/entry_{trade_id}.md"""
    return _log_dir(symbol, interval) / f"entry_{trade_id}.md"


def _build_entry_snapshot_markdown(
    symbol: str,
    interval: str,
    candles: list[Candle],
    graphics: dict[str, Any],
    event: dict[str, Any],
) -> str:
    """Build markdown snapshot in same format as frontend buildStrategyExportMarkdown."""
    lines: list[str] = []
    lines.append("# Strategy Data Export (Entry Snapshot)")
    lines.append("")
    entry_ts = event.get("time", "")
    entry_str = _format_ts_local(entry_ts, "s") if isinstance(entry_ts, (int, float)) else str(entry_ts)
    lines.append(f"**Symbol:** {symbol} | **Interval:** {interval} | **Entry:** {entry_str}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. Bar Data
    lines.append("## 1. Bar Data (OHLCV)")
    lines.append("")
    if candles:
        lines.append("| time | open | high | low | close | volume |")
        lines.append("|------|------|------|-----|-------|--------|")
        for c in candles:
            ts_str = _format_ts_local(c.time, "ms") if c.time > 1e12 else _format_ts_local(int(c.time), "s")
            lines.append(f"| {ts_str} | {c.open} | {c.high} | {c.low} | {c.close} | {c.volume} |")
    else:
        lines.append("*No candle data.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 2. Indicators
    lines.append("## 2. Calculated Indicators")
    lines.append("")

    vp = graphics.get("volumeProfile")
    lines.append("### 2.1 Volume Profile")
    lines.append("")
    if vp:
        lines.append(f"Time: {vp.get('time', '')} | Width: {vp.get('width', '')}")
        lines.append("")
        profile = vp.get("profile", [])[:50]
        lines.append("| price | volume |")
        lines.append("|-------|--------|")
        for p in profile:
            lines.append(f"| {p.get('price', '')} | {p.get('vol', '')} |")
        if len(vp.get("profile", [])) > 50:
            lines.append(f"| ... ({len(vp['profile']) - 50} more rows) |")
    else:
        lines.append("*Volume profile not available.*")
    lines.append("")

    sr = graphics.get("supportResistance", {})
    sr_lines = sr.get("lines", []) if isinstance(sr, dict) else []
    lines.append("### 2.2 Support / Resistance Levels")
    lines.append("")
    if sr_lines:
        lines.append("| price | width | style |")
        lines.append("|-------|-------|-------|")
        for l in sr_lines:
            lines.append(f"| {l.get('price', '')} | {l.get('width', '')} | {l.get('style', 'solid')} |")
    else:
        lines.append("*No S/R levels.*")
    lines.append("")

    ob = graphics.get("orderBlocks", {})
    lines.append("### 2.3 Order Blocks")
    lines.append("")
    if ob:
        all_obs: list[dict] = []
        for key, lst in [
            ("bullish", ob.get("bullish", [])),
            ("bearish", ob.get("bearish", [])),
            ("bullishBreakers", ob.get("bullishBreakers", [])),
            ("bearishBreakers", ob.get("bearishBreakers", [])),
        ]:
            for o in lst:
                all_obs.append({**o, "list": key})
        if all_obs:
            lines.append("| list | top | bottom | initiationTime | structureBreakTime | breakerTime | breaker |")
            lines.append("|------|-----|--------|-----------------|--------------------|------------|---------|")
            for o in all_obs:
                init = o.get("initiationTime")
                init_str = _format_ts_local(init, "s") if isinstance(init, (int, float)) else "-"
                struct = o.get("structureBreakTime")
                struct_str = _format_ts_local(struct, "s") if isinstance(struct, (int, float)) else "-"
                breaker = o.get("breakerTime")
                breaker_str = _format_ts_local(breaker, "s") if isinstance(breaker, (int, float)) else "-"
                lines.append(
                    f"| {o.get('list', '')} | {o.get('top', '')} | {o.get('bottom', '')} | "
                    f"{init_str} | {struct_str} | {breaker_str} | {o.get('breaker', '')} |"
                )
        else:
            lines.append("*No order blocks.*")
    else:
        lines.append("*Order blocks not available.*")
    lines.append("")

    sm = graphics.get("smartMoney", {})
    structure = sm.get("structure", {}) if isinstance(sm, dict) else {}
    lines.append("### 2.4 Smart Money Structure")
    lines.append("")
    if structure:
        line_count = len(structure.get("lines", []))
        label_count = len(structure.get("labels", []))
        swing_count = len(structure.get("swingLabels", []))
        lines.append(f"Structure lines: {line_count} | Labels: {label_count} | Swing labels: {swing_count}")
        cc = structure.get("candleColors", {})
        if cc:
            lines.append(f"Candle trend colors: {len(cc)} bars")
    else:
        lines.append("*Structure not available.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 3. Trade Orders
    lines.append("## 3. Trade Orders (Entry Signals)")
    lines.append("")
    lines.append("| time | barIndex | type | side | price | targetPrice | initialStopPrice | context |")
    lines.append("|------|----------|------|------|-------|-------------|------------------|---------|")
    ev_time = event.get("time", "")
    ev_time_str = _format_ts_local(ev_time, "s") if isinstance(ev_time, (int, float)) else str(ev_time)
    ctx = json.dumps(event.get("context", {}))
    lines.append(
        f"| {ev_time_str} | {event.get('barIndex', '')} | {event.get('type', '')} | "
        f"{event.get('side', '-')} | {event.get('price', '')} | {event.get('targetPrice', '-')} | "
        f"{event.get('initialStopPrice', '')} | {ctx} |"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # 4. Trailing Stop Events
    ss = graphics.get("strategySignals", {})
    stop_segments = ss.get("stopSegments", []) if isinstance(ss, dict) else []
    lines.append("## 4. Trailing Stop Events")
    lines.append("")
    if stop_segments:
        lines.append("| startTime | endTime | price | side |")
        lines.append("|-----------|---------|-------|------|")
        for s in stop_segments:
            st = s.get("startTime", "")
            et = s.get("endTime", "")
            st_str = _format_ts_local(st, "s") if isinstance(st, (int, float)) else str(st)
            et_str = _format_ts_local(et, "s") if isinstance(et, (int, float)) else str(et)
            lines.append(f"| {st_str} | {et_str} | {s.get('price', '')} | {s.get('side', '')} |")
    else:
        lines.append("*No trailing stop segments.*")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*End of export. AI: Use this data to review the strategy logic and propose improvements.*")

    return "\n".join(lines)


def _event_to_dict(ev: TradeEvent) -> dict[str, Any]:
    return {
        "time": ev.time,
        "barIndex": ev.bar_index,
        "type": ev.type,
        "side": ev.side,
        "price": ev.price,
        "targetPrice": ev.target_price,
        "initialStopPrice": ev.initial_stop_price,
        "context": ev.context,
    }


def write_entry_snapshot_md_only(
    symbol: str,
    interval: str,
    event: TradeEvent,
    candles: list[Candle],
    graphics: dict[str, Any],
) -> str:
    """Write only the entry_*.md snapshot (strategy-owned log). Does not write index.jsonl or current.json."""
    trade_id = str(event.time)
    log_dir = _log_dir(symbol, interval)
    log_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = _snapshot_path(symbol, interval, trade_id)
    event_dict = _event_to_dict(event)
    markdown = _build_entry_snapshot_markdown(
        symbol, interval, candles, graphics, event_dict
    )
    snapshot_path.write_text(markdown, encoding="utf-8")
    logger.debug("Trade log: wrote entry snapshot only trade_id=%s", trade_id)
    return trade_id


def append_entry_index_line(
    symbol: str,
    interval: str,
    trade_id: str,
    entry_time: int,
    entry_price: float,
    side: str,
    initial_stop_price: float,
    target_price: float | None = None,
    requested_entry_price: float | None = None,
    size: float | None = None,
    bar_index: int = 0,
    context: dict | None = None,
) -> None:
    """Append only the entry line to index.jsonl (executor calls when confirming fill).
    entry_price = executed fill price; requested_entry_price = strategy's intended open price (for entry efficiency)."""
    log_dir = _log_dir(symbol, interval)
    log_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "type": "entry",
        "tradeId": trade_id,
        "time": entry_time,
        "timeHuman": ts_human(entry_time, unit="s"),
        "barIndex": bar_index,
        "side": side,
        "price": entry_price,
        "initialStopPrice": initial_stop_price,
        "targetPrice": target_price,
        "snapshotFile": f"entry_{trade_id}.md",
    }
    if requested_entry_price is not None:
        record["requestedEntryPrice"] = requested_entry_price
    if context is not None:
        record["context"] = context
    if size is not None:
        record["size"] = size
    index_path = _index_path(symbol, interval)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    logger.debug("Trade log: appended entry index line trade_id=%s", trade_id)


def append_entry(
    symbol: str,
    interval: str,
    event: TradeEvent,
    candles: list[Candle],
    graphics: dict[str, Any],
) -> str:
    """Append entry to trade log. Write snapshot .md file and index record. Returns trade_id."""
    trade_id = str(event.time)
    log_dir = _log_dir(symbol, interval)
    log_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = _snapshot_path(symbol, interval, trade_id)
    event_dict = _event_to_dict(event)
    markdown = _build_entry_snapshot_markdown(
        symbol, interval, candles, graphics, event_dict
    )
    snapshot_path.write_text(markdown, encoding="utf-8")

    record = {
        "type": "entry",
        "tradeId": trade_id,
        "time": event.time,
        "timeHuman": ts_human(event.time, unit="s"),
        "barIndex": event.bar_index,
        "side": event.side,
        "price": event.price,
        "initialStopPrice": event.initial_stop_price,
        "targetPrice": event.target_price,
        "context": event.context,
        "snapshotFile": f"entry_{trade_id}.md",
    }
    index_path = _index_path(symbol, interval)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")

    add_current_trade(
        symbol,
        interval,
        trade_id,
        event.time,
        event.price,
        event.initial_stop_price,
        event.side or "long",
        event.target_price,
    )

    logger.info("Trade log: appended entry trade_id=%s symbol=%s interval=%s", trade_id, symbol, interval)
    return trade_id


def get_effective_stop_segments_for_bar(
    stop_segments: list[StopSegment],
    bar_start_sec: int,
    bar_end_sec: int,
    events_by_side: dict[str, list[tuple[int, str]]],
    logged_entry_ids: set[str],
) -> dict[str, StopSegment]:
    """Return the single best stop segment per trade for the given bar.

    Used by trade logging and by the execution module to submit the correct stop price.
    Strategy may emit multiple segments per bar (e.g. breakeven then level_cross); this
    picks the best by end_time, then by price (long: max price, short: min price).
    """
    best: dict[str, StopSegment] = {}
    for seg in stop_segments:
        if seg.side not in events_by_side:
            continue
        if not (bar_start_sec <= seg.end_time < bar_end_sec):
            continue
        candidates = [(t, tid) for t, tid in events_by_side[seg.side] if t <= seg.start_time]
        if not candidates:
            continue
        _, trade_id = max(candidates, key=lambda x: x[0])
        if trade_id not in logged_entry_ids:
            continue
        existing = best.get(trade_id)
        if existing is None:
            best[trade_id] = seg
        elif seg.end_time > existing.end_time:
            best[trade_id] = seg
        elif seg.end_time == existing.end_time:
            if seg.side == "short" and seg.price < existing.price:
                best[trade_id] = seg
            elif seg.side == "long" and seg.price > existing.price:
                best[trade_id] = seg
    return best


def append_stop_move(
    symbol: str,
    interval: str,
    trade_id: str,
    time: int,
    price: float,
    side: str,
) -> None:
    """Append stop move to trade log."""
    record = {
        "type": "stop_move",
        "tradeId": trade_id,
        "time": time,
        "timeHuman": ts_human(time, unit="s"),
        "price": price,
        "side": side,
    }
    log_dir = _log_dir(symbol, interval)
    log_dir.mkdir(parents=True, exist_ok=True)
    index_path = _index_path(symbol, interval)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    update_current_trade_stop(symbol, interval, trade_id, price)
    logger.debug("Trade log: stop_move trade_id=%s price=%s", trade_id, price)


def append_exit(
    symbol: str,
    interval: str,
    trade_id: str,
    time: int,
    close_price: float,
    close_reason: str,
    points: float,
) -> None:
    """Append exit to trade log."""
    record = {
        "type": "exit",
        "tradeId": trade_id,
        "time": time,
        "timeHuman": ts_human(time, unit="s"),
        "closePrice": close_price,
        "closeReason": close_reason,
        "points": points,
    }
    log_dir = _log_dir(symbol, interval)
    log_dir.mkdir(parents=True, exist_ok=True)
    index_path = _index_path(symbol, interval)
    with index_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    remove_current_trade(symbol, interval, trade_id)
    logger.info("Trade log: exit trade_id=%s reason=%s points=%s", trade_id, close_reason, points)


def get_trades(
    symbol: str,
    interval: str,
    since: int | None = None,
) -> list[dict[str, Any]]:
    """Read trade log index and return list of completed trades for API.
    Each trade has: entry info, stop segments, exit info, markers, stopSegments for chart."""
    index_path = _index_path(symbol, interval)
    if not index_path.exists():
        return []

    entries: dict[str, dict[str, Any]] = {}
    stop_segments: dict[str, list[dict]] = {}
    exits: dict[str, dict] = {}

    with index_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec_type = rec.get("type")
            trade_id = rec.get("tradeId", "")
            if since is not None and rec.get("time", 0) < since:
                continue

            if rec_type == "entry":
                entries[trade_id] = rec
                stop_segments[trade_id] = []
            elif rec_type == "stop_move":
                if trade_id in stop_segments:
                    stop_segments[trade_id].append(rec)
            elif rec_type == "exit":
                exits[trade_id] = rec

    # Map interval string to seconds (same mapping as candle_stream).
    interval_sec = interval_seconds(interval, default=0)

    # Build trades; include both completed (have exit) and still-open (no exit yet).
    trades: list[dict[str, Any]] = []
    for trade_id, entry in entries.items():
        exit_rec = exits.get(trade_id)
        segments = stop_segments.get(trade_id, [])

        # Build stop segments for chart (startTime, endTime, price, side)
        # Entry time -> first stop_move -> ... -> exit (or last stop_move for open trades)
        entry_time = entry.get("time", 0)
        initial_stop = entry.get("initialStopPrice", 0.0)
        side = entry.get("side", "long")

        chart_stop_segments: list[dict] = []
        prev_time = entry_time
        prev_price = initial_stop
        for sm in sorted(segments, key=lambda x: x.get("time", 0)):
            t = sm.get("time", 0)
            p = sm.get("price", 0.0)
            chart_stop_segments.append({
                "startTime": prev_time,
                "endTime": t,
                "price": prev_price,
                "side": side,
            })
            prev_time = t
            prev_price = p

        # For completed trades, the last segment ends at exit time.
        # For open trades, the last segment ends at the last known stop-move time (or entry time if none).
        last_time = exit_rec.get("time", prev_time) if exit_rec is not None else prev_time
        chart_stop_segments.append(
            {
                "startTime": prev_time,
                "endTime": last_time,
                "price": prev_price,
                "side": side,
            }
        )

        # Markers (single entry marker)
        markers = [
            {
                "time": entry_time,
                "position": "below" if side == "long" else "above",
                "shape": "arrowUp" if side == "long" else "arrowDown",
                "color": "#22c55e" if side == "long" else "#dc2626",
            }
        ]

        # Stop lines for chart: connect successive stop levels directly,
        # with a horizontal segment for the first bar and a final horizontal
        # extension so the last stop remains visible.
        stop_lines: list[dict[str, Any]] = []
        if chart_stop_segments:
            segs_sorted = sorted(chart_stop_segments, key=lambda s: s["startTime"])
            # Nodes are (time, price) at each segment start.
            nodes: list[tuple[int, float]] = [
                (s["startTime"], s["price"]) for s in segs_sorted
            ]
            # Deduplicate consecutive identical nodes.
            deduped_nodes: list[tuple[int, float]] = []
            for t, p in nodes:
                if not deduped_nodes or (t, p) != deduped_nodes[-1]:
                    deduped_nodes.append((t, p))
            if deduped_nodes:
                first_time, first_price = deduped_nodes[0]
                last_seg = segs_sorted[-1]
                last_end_time = last_seg["endTime"]

                # Initial horizontal segment for the first bar.
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

                # Final horizontal segment from last node to last_end_time.
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

        if exit_rec is not None:
            close_dt_iso = _ts_to_iso(exit_rec.get("time", 0))
            close_price = exit_rec.get("closePrice")
            close_reason = exit_rec.get("closeReason", "manual")
            points = exit_rec.get("points", 0.0)
        else:
            # Still-open trade: no realized PnL yet. Represent as "open" with zero points.
            close_dt_iso = _ts_to_iso(last_time)
            close_price = entry.get("price")
            close_reason = "open"
            points = 0.0

        trade_obj: dict[str, Any] = {
            "tradeId": trade_id,
            "entryDateTime": _ts_to_iso(entry_time),
            "side": side,
            "entryPrice": entry.get("price"),
            "closeDateTime": close_dt_iso,
            "closePrice": close_price,
            "closeReason": close_reason,
            "points": points,
            "markers": markers,
            "stopSegments": chart_stop_segments,
            "stopLines": stop_lines,
            "events": [{
                "time": entry.get("time"),
                "barIndex": entry.get("barIndex"),
                "type": entry.get("type"),
                "side": entry.get("side"),
                "price": entry.get("price"),
                "targetPrice": entry.get("targetPrice"),
                "initialStopPrice": entry.get("initialStopPrice"),
                "context": entry.get("context"),
            }],
        }
        if entry.get("requestedEntryPrice") is not None:
            trade_obj["requestedEntryPrice"] = entry.get("requestedEntryPrice")
            if trade_obj["events"]:
                trade_obj["events"][0]["requestedEntryPrice"] = entry.get("requestedEntryPrice")
        trades.append(trade_obj)

    return trades


def _ts_to_iso(ts: int) -> str:
    """Convert Unix seconds to ISO string."""
    if ts >= 1e12:
        ts = int(ts / 1000)
    from datetime import datetime
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _to_seconds(t: int) -> int:
    """Normalize timestamp to seconds."""
    return int(t / 1000) if t >= 1e12 else t


def _get_stop_price_for_bar(
    bar_time_sec: int,
    side: str,
    initial_stop: float,
    segments: list[dict],
) -> float:
    """Get effective stop price for a bar from stop segments."""
    relevant = [s for s in segments if s.get("side") == side]
    if not relevant:
        return initial_stop
    covering = next((s for s in relevant if bar_time_sec >= s["startTime"] and bar_time_sec <= s["endTime"]), None)
    if covering:
        return covering["price"]
    if all(s["startTime"] > bar_time_sec for s in relevant):
        return initial_stop
    after_last = [s for s in relevant if s["endTime"] < bar_time_sec]
    if len(after_last) == len(relevant):
        last_seg = max(relevant, key=lambda x: x["endTime"])
        return last_seg["price"]
    ended_before = sorted([s for s in relevant if s["endTime"] < bar_time_sec], key=lambda x: -x["endTime"])
    return ended_before[0]["price"] if ended_before else initial_stop


def compute_trade_results(
    events: list[TradeEvent],
    candles: list[Candle],
    stop_segments: list[StopSegment],
) -> list[dict[str, Any]]:
    """Compute trade outcomes (same logic as frontend computeStrategyResults).
    Returns list of {tradeId, closePrice, closeBarIndex, closeReason, points} for each closed trade."""
    results: list[dict[str, Any]] = []
    segs = [
        {"startTime": s.start_time, "endTime": s.end_time, "price": s.price, "side": s.side}
        for s in stop_segments
    ]

    for ev in events:
        if ev.side not in ("long", "short"):
            continue
        entry_bar_index = ev.bar_index
        if entry_bar_index < 0 or entry_bar_index >= len(candles):
            continue

        entry_candle = candles[entry_bar_index]
        entry_price = entry_candle.close
        entry_time_sec = _to_seconds(entry_candle.time)
        target_price = ev.target_price
        initial_stop = ev.initial_stop_price

        close_price = entry_price
        close_bar_index = entry_bar_index
        close_reason = "end_of_data"

        for i in range(entry_bar_index + 1, len(candles)):
            bar = candles[i]
            bar_time_sec = _to_seconds(bar.time)
            stop_price = _get_stop_price_for_bar(bar_time_sec, ev.side, initial_stop, segs)

            stop_hit = False
            tp_hit = False
            if ev.side == "long":
                stop_hit = bar.low <= stop_price
                tp_hit = target_price is not None and bar.high >= target_price
            else:
                stop_hit = bar.high >= stop_price
                tp_hit = target_price is not None and bar.low <= target_price

            if stop_hit:
                # When stop is hit, treat the *stop level* itself as the execution
                # price for simulation results, not the bar close.
                close_price = stop_price
                close_bar_index = i
                close_reason = "stop"
                break
            if tp_hit:
                close_price = bar.close
                close_bar_index = i
                close_reason = "take_profit"
                break

        if close_reason == "end_of_data" and entry_bar_index < len(candles) - 1:
            last_bar = candles[-1]
            close_price = last_bar.close
            close_bar_index = len(candles) - 1

        points = (close_price - entry_price) if ev.side == "long" else (entry_price - close_price)

        results.append({
            "tradeId": str(ev.time),
            "closePrice": close_price,
            "closeBarIndex": close_bar_index,
            "closeTime": _to_seconds(candles[close_bar_index].time),
            "closeReason": close_reason,
            "points": points,
        })

    return results

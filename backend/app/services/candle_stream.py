import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.schemas.market import Candle
from app.services.bybit_client import BybitClient
from app.services.indicators.volume_profile import build_volume_profile_from_candles
from app.services.indicators.support_resistance import compute_support_resistance_lines
from app.services.indicators.order_blocks import compute_order_blocks
from app.services.indicators.smart_money_structure import compute_structure
from app.services.trading_strategy.order_block_trend_following import compute_order_block_trend_following
from app.services.trading_strategy.chart_format import strategy_output_to_chart
from app.services.trading_strategy.types import TradeEvent, StopSegment
from app.services.trade_log import (
    append_entry,
    append_exit,
    append_stop_move,
    compute_trade_results,
    load_current_trades,
)
from app.utils.timefmt import ts_human

logger = logging.getLogger(__name__)
DEFAULT_VOLUME_PROFILE_WINDOW = 2000

# Bybit interval string -> seconds (for current-bar detection in trade logging)
_INTERVAL_SECONDS: dict[str, int] = {
    "1": 60,
    "3": 180,
    "5": 300,
    "15": 900,
    "30": 1800,
    "60": 3600,
    "120": 7200,
    "240": 14400,
    "360": 21600,
    "720": 43200,
    "D": 86400,
    "W": 604800,
    "M": 2592000,
}


def _interval_seconds(interval: str) -> int:
    """Return bar duration in seconds for Bybit interval string."""
    return _INTERVAL_SECONDS.get(interval, 3600)


def _restore_current_trades(symbol: str, interval: str, state: "CandleStreamState") -> None:
    """Load current trades from file and merge into state. Called once per stream on start."""
    if getattr(state, "current_trades_restored", False):
        return
    path = Path(settings.trade_log_dir) / f"{symbol}_{interval}" / "current.json"
    logger.info(
        "[TRADE_RESTORE] symbol=%s interval=%s path=%s exists=%s",
        symbol,
        interval,
        str(path),
        path.exists(),
    )
    current = load_current_trades(symbol, interval)
    for t in current:
        tid = t.get("tradeId", "")
        if tid:
            state.logged_entry_ids.add(tid)
            state.last_stop_price_per_trade[tid] = t.get("currentStopPrice", 0.0)
    state.restored_trades = current
    state.current_trades_restored = True
    logger.info(
        "[TRADE_RESTORE] loaded %d trade(s): %s",
        len(current),
        [
            {
                "tradeId": t.get("tradeId"),
                "side": t.get("side"),
                "entryTime": t.get("entryTime"),
                "entryTimeHuman": ts_human(t.get("entryTime", 0), unit="s") if isinstance(t.get("entryTime", 0), (int, float)) else None,
                "entryPrice": t.get("entryPrice"),
                "currentStopPrice": t.get("currentStopPrice"),
                "initialStopPrice": t.get("initialStopPrice"),
            }
            for t in current
        ],
    )


def _apply_trade_logging(
    symbol: str,
    interval: str,
    trade_events: list,
    stop_segments: list,
    candles: list[Candle],
    graphics: dict,
    state: "CandleStreamState",
    *,
    is_live_update: bool,
) -> None:
    """When mode=trading: log entries, stop moves, exits. Mutates state.

    In trading mode, only logs signals that occur on the current bar (live bar update).
    Snapshot/resync (is_live_update=False) never logs; historical strategy output is ignored.
    At most one entry or stop move per bar: once we log one, we skip further signals for that bar.
    """
    if settings.mode != "trading":
        return

    _restore_current_trades(symbol, interval, state)

    if not is_live_update:
        return

    if not candles:
        return

    current_bar_index = len(candles) - 1
    current_bar_start_sec = candles[-1].time // 1000
    interval_sec = _interval_seconds(interval)
    current_bar_end_sec = current_bar_start_sec + interval_sec

    skip_entry_and_stop = current_bar_start_sec in state.signals_emitted_for_bar

    if not skip_entry_and_stop:
        # Log new entries (only on current bar)
        for ev in trade_events:
            if ev.bar_index != current_bar_index:
                continue
            trade_id = str(ev.time)
            if trade_id not in state.logged_entry_ids:
                append_entry(symbol, interval, ev, candles, graphics)
                state.logged_entry_ids.add(trade_id)
                state.last_stop_price_per_trade[trade_id] = ev.initial_stop_price
                state.signals_emitted_for_bar.add(current_bar_start_sec)
                skip_entry_and_stop = True
                break

    if not skip_entry_and_stop:
        events_by_side: dict[str, list[tuple[int, str]]] = {"long": [], "short": []}
        for ev in trade_events:
            if ev.side in events_by_side:
                events_by_side[ev.side].append((ev.time, str(ev.time)))
        for t in getattr(state, "restored_trades", []):
            sid = t.get("side", "")
            if sid in events_by_side:
                et = t.get("entryTime", 0)
                tid = t.get("tradeId", "")
                if (et, tid) not in [(x, y) for x, y in events_by_side[sid]]:
                    events_by_side[sid].append((et, tid))
        for side in events_by_side:
            events_by_side[side].sort(key=lambda x: x[0])

        for seg in stop_segments:
            if seg.side not in events_by_side:
                continue
            if not (current_bar_start_sec <= seg.end_time < current_bar_end_sec):
                continue
            candidates = [(t, tid) for t, tid in events_by_side[seg.side] if t <= seg.start_time]
            if not candidates:
                continue
            _, trade_id = max(candidates, key=lambda x: x[0])
            if trade_id not in state.logged_entry_ids:
                continue
            prev = state.last_stop_price_per_trade.get(trade_id)
            if prev is not None and seg.price != prev:
                append_stop_move(symbol, interval, trade_id, seg.end_time, seg.price, seg.side)
                state.last_stop_price_per_trade[trade_id] = seg.price
                # Keep in-memory restored trades consistent with file stop updates.
                for t in getattr(state, "restored_trades", []):
                    if t.get("tradeId") == trade_id:
                        t["currentStopPrice"] = seg.price
                state.signals_emitted_for_bar.add(current_bar_start_sec)
                break

    # Build events + segments for exit detection (include restored trades not in strategy output)
    all_events = list(trade_events)
    all_segments = list(stop_segments)
    strategy_trade_ids = {str(ev.time) for ev in trade_events}
    last_candle_time_sec = candles[-1].time // 1000 if candles else 0

    for t in getattr(state, "restored_trades", []):
        tid = t.get("tradeId", "")
        if tid in strategy_trade_ids or tid in state.logged_exit_ids:
            continue
        entry_time = t.get("entryTime", 0)
        entry_price = t.get("entryPrice", 0.0)
        current_stop = t.get("currentStopPrice", 0.0)
        side = t.get("side", "long")
        target_price = t.get("targetPrice")

        bar_index = 0
        for i, c in enumerate(candles):
            if c.time // 1000 >= entry_time:
                bar_index = i
                break
        else:
            if candles and candles[-1].time // 1000 < entry_time:
                continue

        all_events.append(
            TradeEvent(
                time=entry_time,
                bar_index=bar_index,
                type="RESTORED",
                side=side,
                price=entry_price,
                target_price=target_price,
                initial_stop_price=current_stop,
                context={},
            )
        )
        all_segments.append(
            StopSegment(
                start_time=entry_time,
                end_time=last_candle_time_sec,
                price=current_stop,
                side=side,
            )
        )

    results = compute_trade_results(all_events, candles, all_segments)
    for r in results:
        tid = r["tradeId"]
        if tid in state.logged_exit_ids:
            continue
        if r["closeReason"] == "end_of_data":
            continue
        close_time = r["closeTime"]
        if not (current_bar_start_sec <= close_time < current_bar_end_sec):
            continue
        append_exit(
            symbol,
            interval,
            tid,
            close_time,
            r["closePrice"],
            r["closeReason"],
            r["points"],
        )
        state.logged_exit_ids.add(tid)
        # Keep in-memory restored trades consistent with file exits.
        if getattr(state, "restored_trades", None):
            state.restored_trades = [t for t in state.restored_trades if t.get("tradeId") != tid]


def _make_snapshot_payload(
    candles: list[Candle],
    volume_profile_window: int,
    strategy_markers: str,
    symbol: str,
    interval: str,
    state: "CandleStreamState",
    *,
    is_live_update: bool = False,
) -> dict:
    payload: dict = {
        "event": "snapshot",
        "candles": [c.model_dump() for c in candles],
    }
    if candles:
        # Structure must run first so swing pivots are established before order blocks form.
        # Order blocks now reuse the exact same swing pivots from structure instead
        # of recomputing their own swings, ensuring 1:1 alignment between the
        # Smart Money structure indicator and OB zones.
        structure_result = compute_structure(
            candles,
            include_candle_colors=True,
        )
        ob_result = compute_order_blocks(
            candles,
            show_bull=0,
            show_bear=0,
            swing_pivots=structure_result.get("swingPivots") or {},
        )
        graphics: dict = {
            "orderBlocks": ob_result,
            "smartMoney": {"structure": structure_result},
        }
        vp = build_volume_profile_from_candles(
            candles,
            time=candles[-1].time // 1000,
            width=6,
            window_size=volume_profile_window,
        )
        if vp:
            graphics["volumeProfile"] = vp
            sr_lines = compute_support_resistance_lines(vp["profile"])
            graphics["supportResistance"] = {"lines": sr_lines}
            if strategy_markers in ("simulation", "trade"):
                trade_events, stop_segments = compute_order_block_trend_following(
                    candles,
                    structure_result.get("swingPivots") or {},
                    candle_colors=structure_result.get("candleColors"),
                    sr_lines=sr_lines,
                )
                chart_data = strategy_output_to_chart(trade_events, stop_segments)
                graphics["strategySignals"] = chart_data
                _apply_trade_logging(
                    symbol, interval, trade_events, stop_segments, candles, graphics, state,
                    is_live_update=is_live_update,
                )
                if settings.mode == "trading":
                    del graphics["strategySignals"]
        payload["graphics"] = graphics
    return payload


@dataclass
class CandleStreamState:
    queues: set[asyncio.Queue[dict]] = field(default_factory=set)
    candles: list[Candle] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    volume_profile_window: int = DEFAULT_VOLUME_PROFILE_WINDOW
    strategy_markers: str = "off"
    # Trade log state (mode=trading)
    logged_entry_ids: set[str] = field(default_factory=set)
    logged_exit_ids: set[str] = field(default_factory=set)
    last_stop_price_per_trade: dict[str, float] = field(default_factory=dict)
    current_trades_restored: bool = False
    restored_trades: list = field(default_factory=list)
    signals_emitted_for_bar: set[int] = field(default_factory=set)


class CandleStreamHub:
    def __init__(self, bybit_client: BybitClient, snapshot_limit: int = 300) -> None:
        self._bybit_client = bybit_client
        self._snapshot_limit = snapshot_limit
        self._streams: dict[tuple[str, str], CandleStreamState] = defaultdict(CandleStreamState)
        self._lock = asyncio.Lock()

    async def start_heartbeat(
        self,
        symbol: str,
        interval: str,
        volume_profile_window: int = DEFAULT_VOLUME_PROFILE_WINDOW,
        strategy_markers: str = "off",
    ) -> None:
        """Start heartbeat for symbol/interval without requiring a subscriber. Used for trading mode on startup."""
        stream_key = (symbol.upper(), interval)
        async with self._lock:
            state = self._streams[stream_key]
            state.volume_profile_window = volume_profile_window
            state.strategy_markers = strategy_markers
            if settings.mode == "trading":
                _restore_current_trades(symbol.upper(), interval, state)
            if state.task is None or state.task.done():
                state.task = asyncio.create_task(self._run_heartbeat(symbol.upper(), interval))

    async def subscribe(
        self,
        symbol: str,
        interval: str,
        volume_profile_window: int = DEFAULT_VOLUME_PROFILE_WINDOW,
        strategy_markers: str = "off",
    ) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)
        stream_key = (symbol, interval)
        snapshot_payload: dict | None = None
        async with self._lock:
            state = self._streams[stream_key]
            state.queues.add(queue)
            state.volume_profile_window = volume_profile_window
            state.strategy_markers = strategy_markers
            if state.candles:
                snapshot_payload = _make_snapshot_payload(
                    state.candles,
                    state.volume_profile_window,
                    state.strategy_markers,
                    symbol,
                    interval,
                    state,
                )
            if state.task is None or state.task.done():
                state.task = asyncio.create_task(self._run_heartbeat(symbol, interval))
        if snapshot_payload is not None:
            await queue.put(snapshot_payload)
        return queue

    async def unsubscribe(self, symbol: str, interval: str, queue: asyncio.Queue[dict]) -> None:
        stream_key = (symbol, interval)
        async with self._lock:
            state = self._streams.get(stream_key)
            if state is None:
                return
            state.queues.discard(queue)
            if state.queues:
                return
            if state.task:
                state.task.cancel()
            self._streams.pop(stream_key, None)

    async def _run_heartbeat(self, symbol: str, interval: str) -> None:
        """Heartbeat loop: fetch from Bybit REST at fetch_interval_sec, compute, broadcast."""
        stream_key = (symbol, interval)
        fetch_interval = settings.fetch_interval_sec
        first_run = True
        while True:
            try:
                if not first_run:
                    await asyncio.sleep(fetch_interval)
                first_run = False

                candles = await self._bybit_client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=self._snapshot_limit,
                )
                async with self._lock:
                    state = self._streams.get(stream_key)
                    if state is None:
                        return
                    state.candles = candles
                    vp_window = state.volume_profile_window
                    strategy_markers = state.strategy_markers

                payload = _make_snapshot_payload(
                    candles,
                    vp_window,
                    strategy_markers,
                    symbol,
                    interval,
                    state,
                    is_live_update=True,
                )
                await self._broadcast(stream_key, payload)
                # Trading mode debug: show current open position/stop each heartbeat.
                open_trades = load_current_trades(symbol, interval) if settings.mode == "trading" else []
                if settings.mode == "trading":
                    if open_trades:
                        t0 = open_trades[0]
                        pos_summary = (
                            f"count={len(open_trades)} "
                            f"tradeId={t0.get('tradeId')} side={t0.get('side')} "
                            f"entryTime={t0.get('entryTime')}({ts_human(t0.get('entryTime', 0), unit='s')}) "
                            f"entry={t0.get('entryPrice')} stop={t0.get('currentStopPrice')} "
                            f"initStop={t0.get('initialStopPrice')}"
                        )
                    else:
                        pos_summary = "count=0"
                else:
                    pos_summary = "n/a"
                logger.info(
                    "Heartbeat: %s %s fetched %d candles, broadcast to %d client(s) | open_position: %s",
                    symbol,
                    interval,
                    len(candles),
                    len(state.queues),
                    pos_summary,
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Heartbeat fetch failed, retrying in %ds", fetch_interval)
                await asyncio.sleep(fetch_interval)

    async def get_cached_candles(self, symbol: str, interval: str, limit: int = 2000) -> list[Candle]:
        """Return cached candles for symbol/interval if available. Used by GET /candles."""
        stream_key = (symbol.upper(), interval)
        async with self._lock:
            state = self._streams.get(stream_key)
            if state is None or not state.candles:
                return []
            candles = list(state.candles)
        if limit < len(candles):
            candles = candles[-limit:]
        return candles

    async def _broadcast(self, stream_key: tuple[str, str], payload: dict) -> None:
        async with self._lock:
            state = self._streams.get(stream_key)
            if state is None:
                return
            queues = list(state.queues)
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put(payload)

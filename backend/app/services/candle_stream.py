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
from app.services.indicators.cumulative_volume_delta import compute_cumulative_volume_delta
from app.services.trading_strategy.order_block_trend_following import compute_order_block_trend_following
from app.services.trading_strategy.chart_format import strategy_output_to_chart
from app.services.trading_strategy.types import (
    StrategySeedPosition,
    TradeEvent,
    StopSegment,
)
from app.services.trade_log import (
    ensure_trade_log_initialized,
    append_exit,
    compute_trade_results,
    get_effective_stop_segments_for_bar,
    load_current_trade_seed,
    load_current_trades,
    write_entry_snapshot_md_only,
)
from app.services.execution_service import (
    execute_forced_closure,
    submit_entry,
    sync_from_exchange,
    update_stop,
)
from app.utils.intervals import interval_seconds
from app.utils.timefmt import ts_human

logger = logging.getLogger(__name__)
DEFAULT_VOLUME_PROFILE_WINDOW = 2000


def _log_heartbeat_task_done(stream_key: tuple[str, str], task: asyncio.Task[None]) -> None:
    """Avoid 'Future exception was never retrieved' if the heartbeat task exits unexpectedly."""
    try:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Heartbeat task ended %s/%s: %s",
                stream_key[0],
                stream_key[1],
                exc,
                exc_info=exc,
            )
    except asyncio.CancelledError:
        pass


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
    _refresh_current_trades_from_file(symbol, interval, state)
    state.current_trades_restored = True
    logger.info(
        "[TRADE_RESTORE] loaded %d trade(s): %s",
        len(state.restored_trades),
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
            for t in state.restored_trades
        ],
    )


def _refresh_current_trades_from_file(
    symbol: str, interval: str, state: "CandleStreamState"
) -> None:
    """Reload current.json into state (restored_trades, logged_entry_ids, last_stop_price_per_trade).
    Called after executor sync so strategy sees executor-written state."""
    current = load_current_trades(symbol, interval)
    state.restored_trades = current
    for t in current:
        tid = t.get("tradeId", "")
        if tid:
            state.logged_entry_ids.add(tid)
            state.last_stop_price_per_trade[tid] = t.get("currentStopPrice", 0.0)


def _build_strategy_seed_position(
    symbol: str,
    interval: str,
    state: "CandleStreamState",
) -> StrategySeedPosition | None:
    """Restore the current open trade so the strategy can resume live trailing."""
    if settings.mode != "trading":
        return None
    if len(state.restored_trades) != 1:
        return None

    trade_id = str(state.restored_trades[0].get("tradeId", "")).strip()
    if not trade_id:
        return None

    seed = load_current_trade_seed(symbol, interval, trade_id)
    if seed is None:
        return None

    side = str(seed.get("side", "")).lower()
    if side not in {"long", "short"}:
        return None

    entry_time = int(seed.get("entryTime", 0) or 0)
    entry_price = float(seed.get("entryPrice", 0.0) or 0.0)
    stop_price = float(seed.get("currentStopPrice", seed.get("initialStopPrice", 0.0)) or 0.0)
    active_stop_time = int(seed.get("activeStopTime", entry_time) or entry_time)
    reference_stop_time = int(seed.get("referenceStopTime", entry_time) or entry_time)
    reference_stop_price = float(
        seed.get("referenceStopPrice", seed.get("initialStopPrice", stop_price)) or stop_price
    )
    target_value = seed.get("targetPrice")
    target_price = float(target_value) if isinstance(target_value, (int, float)) else None
    if entry_time <= 0 or entry_price <= 0.0 or stop_price <= 0.0:
        return None

    return StrategySeedPosition(
        trade_id=trade_id,
        side=side,
        entry_time=entry_time,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        active_stop_time=active_stop_time,
        reference_stop_price=reference_stop_price,
        reference_stop_time=reference_stop_time,
    )


async def _apply_trade_logging(
    symbol: str,
    interval: str,
    trade_events: list,
    stop_segments: list,
    candles: list[Candle],
    graphics: dict,
    state: "CandleStreamState",
    *,
    is_live_update: bool,
    bybit_client: BybitClient | None = None,
) -> None:
    """When mode=trading: send entry to executor (no current.json yet), write entry_*.md only; stop moves, exits. Mutates state.

    Executor owns current.json and index.jsonl; strategy only writes entry_*.md. On next heartbeat executor syncs from exchange.
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
    interval_sec = interval_seconds(interval, default=3600)
    current_bar_end_sec = current_bar_start_sec + interval_sec
    # In trading mode, both entry and trailing-stop updates are evaluated on the
    # current bar. We always submit the stop level computed by the strategy for
    # this bar whenever it differs from the last logged stop for that trade.
    trailing_bar_start_sec = current_bar_start_sec
    trailing_bar_end_sec = current_bar_end_sec

    # Prevent duplicate entries per bar, but allow multiple stop updates
    # (we rely on last_stop_price_per_trade to avoid duplicate prices).
    entry_emitted_for_bar = current_bar_start_sec in state.signals_emitted_for_bar

    # Strategy forced exit (flat only): before new entries / dry-run replay.
    for ev in trade_events:
        if ev.bar_index != current_bar_index or ev.type != "FORCED_CLOSE":
            continue
        tid = ev.trade_id
        if tid in state.logged_exit_ids:
            continue
        res = await execute_forced_closure(symbol, interval, ev, bybit_client)
        if res.get("ok"):
            _register_trade_exit(state, tid)
            if getattr(state, "restored_trades", None):
                state.restored_trades = [t for t in state.restored_trades if t.get("tradeId") != tid]

    if not entry_emitted_for_bar and bybit_client is not None:
        for ev in trade_events:
            if ev.bar_index != current_bar_index:
                continue
            if ev.type not in ("OB_TREND_BUY", "OB_TREND_SELL"):
                continue
            trade_id = ev.trade_id
            if trade_id not in state.logged_entry_ids:
                response = await submit_entry(ev, symbol, interval, bybit_client)
                if response.order_received:
                    write_entry_snapshot_md_only(symbol, interval, ev, candles, graphics)
                    state.logged_entry_ids.add(trade_id)
                    state.last_stop_price_per_trade[trade_id] = ev.initial_stop_price
                    state.signals_emitted_for_bar.add(current_bar_start_sec)
                    entry_emitted_for_bar = True
                    rev_closed = response.reversal_closed_trade_id
                    if rev_closed:
                        _register_trade_exit(state, rev_closed)
                        if getattr(state, "restored_trades", None):
                            state.restored_trades = [
                                t for t in state.restored_trades if t.get("tradeId") != rev_closed
                            ]
                else:
                    logger.warning(
                        "Executor: entry not received trade_id=%s msg=%s",
                        trade_id,
                        response.message,
                    )
                break

    # Always consider trailing-stop updates for the current bar; we only log
    # when the *effective* stop for the bar (latest segment) changes vs the
    # last logged value for that trade.
    events_by_side: dict[str, list[tuple[int, str]]] = {"long": [], "short": []}
    for ev in trade_events:
        if ev.type not in ("OB_TREND_BUY", "OB_TREND_SELL"):
            continue
        if ev.side in events_by_side:
            events_by_side[ev.side].append((ev.time, ev.trade_id))
    for t in getattr(state, "restored_trades", []):
        sid = t.get("side", "")
        if sid in events_by_side:
            et = t.get("entryTime", 0)
            tid = t.get("tradeId", "")
            if (et, tid) not in [(x, y) for x, y in events_by_side[sid]]:
                events_by_side[sid].append((et, tid))
    for side in events_by_side:
        events_by_side[side].sort(key=lambda x: x[0])

    best_seg_per_trade = get_effective_stop_segments_for_bar(
        stop_segments,
        trailing_bar_start_sec,
        trailing_bar_end_sec,
        events_by_side,
        state.logged_entry_ids,
    )

    for trade_id, seg in best_seg_per_trade.items():
        if trade_id in state.logged_exit_ids:
            continue
        prev = state.last_stop_price_per_trade.get(trade_id)
        # Always accept the newly computed stop level from the strategy and
        # overwrite whatever is in current.json, as long as the price itself
        # actually changed. We allow both tighter and looser moves.
        if prev is not None and seg.price == prev:
            continue
        # At most one stop move per bar per trade to avoid wobble from repeated live updates.
        if state.logged_stop_bar_per_trade.get(trade_id) == trailing_bar_start_sec:
            continue
        # Executor owns current.json and index (stop_move): dry run logs and persists; live sets Bybit then persists.
        await update_stop(
            symbol,
            interval,
            trade_id,
            seg.price,
            seg.side,
            seg.end_time,
            bybit_client,
        )
        state.last_stop_price_per_trade[trade_id] = seg.price
        state.logged_stop_bar_per_trade[trade_id] = trailing_bar_start_sec
        # Keep in-memory restored trades consistent with executor-written current.json.
        for t in getattr(state, "restored_trades", []):
            if t.get("tradeId") == trade_id:
                t["currentStopPrice"] = seg.price

    # Build events + segments for exit detection (include restored trades not in strategy output)
    all_events = list(trade_events)
    all_segments = list(stop_segments)
    strategy_trade_ids = {ev.trade_id for ev in trade_events}
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
                trade_id=tid,
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
                trade_id=tid,
                price=current_stop,
                side=side,
            )
        )

    # Strategy-side exit simulation: only used in executor dry-run. In live trading,
    # executor owns stop-hit detection and writes exits based on exchange state.
    if settings.executor_dry_run:
        results = compute_trade_results(all_events, candles, all_segments)
        for r in results:
            tid = r["tradeId"]
            if tid in state.logged_exit_ids:
                continue
            if r["closeReason"] in ("forced_closure", "reversal"):
                # Already logged via execute_forced_closure / submit_entry reversal close.
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


async def _make_snapshot_payload(
    candles: list[Candle],
    volume_profile_window: int,
    strategy_markers: str,
    symbol: str,
    interval: str,
    state: "CandleStreamState",
    *,
    is_live_update: bool = False,
    bybit_client: BybitClient | None = None,
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
            # Cumulative volume delta-style indicator (for separate pane).
            cvd = compute_cumulative_volume_delta(candles)
            if cvd.get("points"):
                graphics["cumulativeVolumeDelta"] = cvd
            if strategy_markers in ("simulation", "trade"):
                seed_position = _build_strategy_seed_position(symbol, interval, state)
                trade_events, stop_segments = compute_order_block_trend_following(
                    candles,
                    structure_result.get("swingPivots") or {},
                    candle_colors=structure_result.get("candleColors"),
                    sr_lines=sr_lines,
                    tick_size=state.tick_size,
                    seed_position=seed_position,
                )
                chart_data = strategy_output_to_chart(
                    trade_events, stop_segments, interval
                )
                graphics["strategySignals"] = chart_data
                await _apply_trade_logging(
                    symbol,
                    interval,
                    trade_events,
                    stop_segments,
                    candles,
                    graphics,
                    state,
                    is_live_update=is_live_update,
                    bybit_client=bybit_client,
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
    tick_size: float | None = None
    # Trade log state (mode=trading)
    logged_entry_ids: set[str] = field(default_factory=set)
    logged_exit_ids: set[str] = field(default_factory=set)
    last_stop_price_per_trade: dict[str, float] = field(default_factory=dict)
    logged_stop_bar_per_trade: dict[str, int] = field(default_factory=dict)  # trade_id -> bar_start_sec; one stop_move per bar
    current_trades_restored: bool = False
    restored_trades: list = field(default_factory=list)
    signals_emitted_for_bar: set[int] = field(default_factory=set)


def _register_trade_exit(state: CandleStreamState, trade_id: str) -> None:
    """Record exit and clear trailing-stop cache.

    Strategy replay still emits ``StopSegment``s for historical trade IDs while those IDs
    remain in ``logged_entry_ids``; without this, we would keep calling ``update_stop`` /
    appending ``stop_move`` after the exchange (or dry-run) has already closed the leg.
    """
    if not trade_id:
        return
    state.logged_exit_ids.add(trade_id)
    state.last_stop_price_per_trade.pop(trade_id, None)
    state.logged_stop_bar_per_trade.pop(trade_id, None)


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
                sym = symbol.upper()
                ensure_trade_log_initialized(sym, interval)
                _restore_current_trades(sym, interval, state)
            if state.task is None or state.task.done():
                t = asyncio.create_task(self._run_heartbeat(symbol.upper(), interval))
                t.add_done_callback(
                    lambda task, sk=stream_key: _log_heartbeat_task_done(sk, task)
                )
                state.task = t

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
                snapshot_payload = await _make_snapshot_payload(
                    state.candles,
                    state.volume_profile_window,
                    state.strategy_markers,
                    symbol,
                    interval,
                    state,
                    bybit_client=self._bybit_client,
                )
            if state.task is None or state.task.done():
                t = asyncio.create_task(self._run_heartbeat(symbol, interval))
                t.add_done_callback(
                    lambda task, sk=stream_key: _log_heartbeat_task_done(sk, task)
                )
                state.task = t
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
            # In simulation mode, stop heartbeat when no clients are listening.
            # In trading mode, keep heartbeat running independent of clients.
            if state.queues:
                return
            if settings.mode != "trading":
                if state.task:
                    state.task.cancel()
                self._streams.pop(stream_key, None)

    async def _run_heartbeat(self, symbol: str, interval: str) -> None:
        """Heartbeat loop: fetch from Bybit REST at fetch_interval_sec, compute, broadcast."""
        stream_key = (symbol.upper(), interval)
        fetch_interval = settings.fetch_interval_sec
        reconnect_max = max(fetch_interval, settings.network_reconnect_max_sec)
        # After errors, sleep grows up to reconnect_max (reduces log/DNS hammer when offline).
        error_backoff = fetch_interval
        first_run = True
        # Fetch tick size once per heartbeat stream (best-effort; fall back to heuristic in strategy).
        tick_size: float | None = None
        try:
            tick_size = await self._bybit_client.get_tick_size(symbol=symbol)
        except Exception as e:
            logger.warning("Heartbeat: failed to fetch tick size symbol=%s err=%s", symbol, e)
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
                exited_ids: list[str] = []
                if settings.mode == "trading":
                    exited_ids = await sync_from_exchange(symbol, interval, self._bybit_client)
                async with self._lock:
                    state = self._streams.get(stream_key)
                    if state is None:
                        return
                    state.candles = candles
                    state.tick_size = tick_size
                    vp_window = state.volume_profile_window
                    strategy_markers = state.strategy_markers
                    if settings.mode == "trading":
                        _refresh_current_trades_from_file(symbol, interval, state)
                        for tid in exited_ids:
                            _register_trade_exit(state, tid)

                payload = await _make_snapshot_payload(
                    candles,
                    vp_window,
                    strategy_markers,
                    symbol,
                    interval,
                    state,
                    is_live_update=True,
                    bybit_client=self._bybit_client,
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
                error_backoff = fetch_interval
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    "Heartbeat %s %s: %s (%s); retry in %ds",
                    symbol,
                    interval,
                    type(e).__name__,
                    e,
                    error_backoff,
                )
                try:
                    await asyncio.sleep(error_backoff)
                except asyncio.CancelledError:
                    break
                error_backoff = min(reconnect_max, max(fetch_interval, error_backoff * 2))
                first_run = True  # retry soon; do not wait fetch_interval again before next get_klines

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

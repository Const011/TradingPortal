import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field

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

logger = logging.getLogger(__name__)
DEFAULT_VOLUME_PROFILE_WINDOW = 2000


def _candle_from_bar(start: int, open_: float, high: float, low: float, close: float, volume: float) -> Candle:
    return Candle(time=start, open=open_, high=high, low=low, close=close, volume=volume)


def _restore_current_trades(symbol: str, interval: str, state: "CandleStreamState") -> None:
    """Load current trades from file and merge into state. Called once per stream on start."""
    if getattr(state, "current_trades_restored", False):
        return
    current = load_current_trades(symbol, interval)
    for t in current:
        tid = t.get("tradeId", "")
        if tid:
            state.logged_entry_ids.add(tid)
            state.last_stop_price_per_trade[tid] = t.get("currentStopPrice", 0.0)
    state.restored_trades = current
    state.current_trades_restored = True


def _apply_trade_logging(
    symbol: str,
    interval: str,
    trade_events: list,
    stop_segments: list,
    candles: list[Candle],
    graphics: dict,
    state: "CandleStreamState",
) -> None:
    """When mode=trading: log entries, stop moves, exits. Mutates state."""
    if settings.mode != "trading":
        return

    _restore_current_trades(symbol, interval, state)

    # Log new entries
    for ev in trade_events:
        trade_id = str(ev.time)
        if trade_id not in state.logged_entry_ids:
            append_entry(symbol, interval, ev, candles, graphics)
            state.logged_entry_ids.add(trade_id)
            state.last_stop_price_per_trade[trade_id] = ev.initial_stop_price

    # Include restored trades in events_by_side for stop-move matching
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
        if r["closeReason"] != "end_of_data":
            append_exit(
                symbol,
                interval,
                tid,
                r["closeTime"],
                r["closePrice"],
                r["closeReason"],
                r["points"],
            )
            state.logged_exit_ids.add(tid)


def _make_snapshot_payload(
    candles: list[Candle],
    volume_profile_window: int,
    strategy_markers: str,
    symbol: str,
    interval: str,
    state: "CandleStreamState",
) -> dict:
    payload: dict = {
        "event": "snapshot",
        "candles": [c.model_dump() for c in candles],
    }
    if candles:
        ob_result = compute_order_blocks(candles, show_bull=0, show_bear=0)
        structure_result = compute_structure(
            candles, include_candle_colors=True, max_swing_labels=50
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
                    candle_colors=structure_result.get("candleColors"),
                    sr_lines=sr_lines,
                )
                chart_data = strategy_output_to_chart(trade_events, stop_segments)
                graphics["strategySignals"] = chart_data
                _apply_trade_logging(symbol, interval, trade_events, stop_segments, candles, graphics, state)
                if settings.mode == "trading":
                    del graphics["strategySignals"]
        payload["graphics"] = graphics
    return payload


def _make_upsert_payload(
    candle: Candle,
    candles: list[Candle],
    volume_profile_window: int,
    strategy_markers: str,
    symbol: str,
    interval: str,
    state: "CandleStreamState",
) -> dict:
    payload: dict = {
        "event": "upsert",
        "candle": candle.model_dump(),
    }
    if candles:
        ob_result = compute_order_blocks(candles, show_bull=0, show_bear=0)
        structure_result = compute_structure(
            candles, include_candle_colors=True, max_swing_labels=50
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
                    candle_colors=structure_result.get("candleColors"),
                    sr_lines=sr_lines,
                )
                chart_data = strategy_output_to_chart(trade_events, stop_segments)
                graphics["strategySignals"] = chart_data
                _apply_trade_logging(symbol, interval, trade_events, stop_segments, candles, graphics, state)
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


class CandleStreamHub:
    def __init__(self, bybit_client: BybitClient, snapshot_limit: int = 300) -> None:
        self._bybit_client = bybit_client
        self._snapshot_limit = snapshot_limit
        self._streams: dict[tuple[str, str], CandleStreamState] = defaultdict(CandleStreamState)
        self._lock = asyncio.Lock()

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
                state.task = asyncio.create_task(self._run_stream(symbol, interval))
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

    async def _run_stream(self, symbol: str, interval: str) -> None:
        stream_key = (symbol, interval)
        while True:
            try:
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
                payload = _make_snapshot_payload(
                    candles,
                    state.volume_profile_window,
                    state.strategy_markers,
                    symbol,
                    interval,
                    state,
                )
                await self._broadcast(stream_key, payload)

                async for bar in self._bybit_client.stream_kline(symbol, interval):
                    candidate = _candle_from_bar(
                        start=bar.start,
                        open_=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                    )

                    do_resync = False
                    event_payload: dict | None = None
                    async with self._lock:
                        state = self._streams.get(stream_key)
                        if state is None:
                            return
                        if not state.candles:
                            state.candles = [candidate]
                            event_payload = _make_upsert_payload(
                                candidate,
                                state.candles,
                                state.volume_profile_window,
                                state.strategy_markers,
                                symbol,
                                interval,
                                state,
                            )
                        else:
                            last = state.candles[-1]
                            if candidate.time > last.time:
                                state.candles.append(candidate)
                                if len(state.candles) > self._snapshot_limit:
                                    state.candles = state.candles[-self._snapshot_limit :]
                                event_payload = _make_upsert_payload(
                                    candidate,
                                    state.candles,
                                    state.volume_profile_window,
                                    state.strategy_markers,
                                    symbol,
                                    interval,
                                    state,
                                )
                                do_resync = True
                            elif candidate.time == last.time:
                                state.candles[-1] = candidate
                                event_payload = _make_upsert_payload(
                                    candidate,
                                    state.candles,
                                    state.volume_profile_window,
                                    state.strategy_markers,
                                    symbol,
                                    interval,
                                    state,
                                )
                            else:
                                for idx, candle in enumerate(state.candles):
                                    if candle.time == candidate.time:
                                        state.candles[idx] = candidate
                                        event_payload = _make_upsert_payload(
                                            candidate,
                                            state.candles,
                                            state.volume_profile_window,
                                            state.strategy_markers,
                                            symbol,
                                            interval,
                                            state,
                                        )
                                        break

                    if event_payload is not None:
                        await self._broadcast(stream_key, event_payload)

                    if do_resync:
                        await self._resync_and_broadcast(stream_key, symbol, interval)
            except asyncio.CancelledError:
                break
            except Exception:
                # Retry on transient upstream/network issues.
                await asyncio.sleep(2)

    async def _resync_and_broadcast(self, stream_key: tuple[str, str], symbol: str, interval: str) -> None:
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
                candles, vp_window, strategy_markers, symbol, interval, state
            )
        await self._broadcast(stream_key, payload)

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

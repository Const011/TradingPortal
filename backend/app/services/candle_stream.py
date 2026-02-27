import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from app.schemas.market import Candle
from app.services.bybit_client import BybitClient
from app.services.indicators.volume_profile import build_volume_profile_from_candles
from app.services.indicators.support_resistance import compute_support_resistance_lines
from app.services.indicators.order_blocks import compute_order_blocks
from app.services.indicators.smart_money_structure import compute_structure
from app.services.trading_strategy.order_block_trend_following import compute_order_block_trend_following
from app.services.trading_strategy.chart_format import strategy_output_to_chart

DEFAULT_VOLUME_PROFILE_WINDOW = 2000


def _candle_from_bar(start: int, open_: float, high: float, low: float, close: float, volume: float) -> Candle:
    return Candle(time=start, open=open_, high=high, low=low, close=close, volume=volume)


def _make_snapshot_payload(
    candles: list[Candle],
    volume_profile_window: int,
    strategy_markers: str = "off",
) -> dict:
    payload: dict = {
        "event": "snapshot",
        "candles": [c.model_dump() for c in candles],
    }
    if candles:
        ob_result = compute_order_blocks(candles)
        structure_result = compute_structure(candles, include_candle_colors=True)
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
                graphics["strategySignals"] = strategy_output_to_chart(trade_events, stop_segments)
        payload["graphics"] = graphics
    return payload


def _make_upsert_payload(
    candle: Candle,
    candles: list[Candle],
    volume_profile_window: int,
    strategy_markers: str = "off",
) -> dict:
    payload: dict = {
        "event": "upsert",
        "candle": candle.model_dump(),
    }
    if candles:
        ob_result = compute_order_blocks(candles)
        structure_result = compute_structure(candles, include_candle_colors=True)
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
                graphics["strategySignals"] = strategy_output_to_chart(trade_events, stop_segments)
        payload["graphics"] = graphics
    return payload


@dataclass
class CandleStreamState:
    queues: set[asyncio.Queue[dict]] = field(default_factory=set)
    candles: list[Candle] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    volume_profile_window: int = DEFAULT_VOLUME_PROFILE_WINDOW
    strategy_markers: str = "off"


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
                    candles, state.volume_profile_window, state.strategy_markers
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
                                )
                                do_resync = True
                            elif candidate.time == last.time:
                                state.candles[-1] = candidate
                                event_payload = _make_upsert_payload(
                                    candidate,
                                    state.candles,
                                    state.volume_profile_window,
                                    state.strategy_markers,
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
        await self._broadcast(
            stream_key, _make_snapshot_payload(candles, vp_window, strategy_markers)
        )

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

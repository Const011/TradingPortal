import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from app.schemas.market import Candle
from app.services.bybit_client import BybitClient
from app.services.indicators.volume_profile import build_volume_profile_from_candles
from app.services.indicators.support_resistance import compute_support_resistance_lines

DEFAULT_VOLUME_PROFILE_WINDOW = 2000


def _candle_from_bar(start: int, open_: float, high: float, low: float, close: float, volume: float) -> Candle:
    return Candle(time=start, open=open_, high=high, low=low, close=close, volume=volume)


def _make_snapshot_payload(candles: list[Candle], volume_profile_window: int) -> dict:
    payload: dict = {
        "event": "snapshot",
        "candles": [c.model_dump() for c in candles],
    }
    if candles:
        vp = build_volume_profile_from_candles(
            candles,
            time=candles[-1].time // 1000,
            width=6,
            window_size=volume_profile_window,
        )
        if vp:
            sr_lines = compute_support_resistance_lines(vp["profile"])
            payload["graphics"] = {
                "volumeProfile": vp,
                "supportResistance": {"lines": sr_lines},
            }
    return payload


def _make_upsert_payload(candle: Candle, candles: list[Candle], volume_profile_window: int) -> dict:
    payload: dict = {
        "event": "upsert",
        "candle": candle.model_dump(),
    }
    if candles:
        vp = build_volume_profile_from_candles(
            candles,
            time=candles[-1].time // 1000,
            width=6,
            window_size=volume_profile_window,
        )
        if vp:
            sr_lines = compute_support_resistance_lines(vp["profile"])
            payload["graphics"] = {
                "volumeProfile": vp,
                "supportResistance": {"lines": sr_lines},
            }
    return payload


@dataclass
class CandleStreamState:
    queues: set[asyncio.Queue[dict]] = field(default_factory=set)
    candles: list[Candle] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    volume_profile_window: int = DEFAULT_VOLUME_PROFILE_WINDOW


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
    ) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)
        stream_key = (symbol, interval)
        snapshot_payload: dict | None = None
        async with self._lock:
            state = self._streams[stream_key]
            state.queues.add(queue)
            state.volume_profile_window = volume_profile_window
            if state.candles:
                snapshot_payload = _make_snapshot_payload(
                    state.candles,
                    state.volume_profile_window,
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
                payload = _make_snapshot_payload(candles, state.volume_profile_window)
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
                                candidate, state.candles, state.volume_profile_window
                            )
                        else:
                            last = state.candles[-1]
                            if candidate.time > last.time:
                                state.candles.append(candidate)
                                if len(state.candles) > self._snapshot_limit:
                                    state.candles = state.candles[-self._snapshot_limit :]
                                event_payload = _make_upsert_payload(
                                    candidate, state.candles, state.volume_profile_window
                                )
                                do_resync = True
                            elif candidate.time == last.time:
                                state.candles[-1] = candidate
                                event_payload = _make_upsert_payload(
                                    candidate, state.candles, state.volume_profile_window
                                )
                            else:
                                for idx, candle in enumerate(state.candles):
                                    if candle.time == candidate.time:
                                        state.candles[idx] = candidate
                                        event_payload = _make_upsert_payload(
                                            candidate, state.candles, state.volume_profile_window
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
        await self._broadcast(stream_key, _make_snapshot_payload(candles, vp_window))

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

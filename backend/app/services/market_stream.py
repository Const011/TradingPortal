import asyncio
import logging
from collections import defaultdict

from app.schemas.market import TickerTick
from app.services.bybit_client import BybitClient

logger = logging.getLogger(__name__)

# Ticker WS: reconnect backoff (seconds). Resets when we receive a tick again.
_TICKER_BACKOFF_INITIAL = 3
_TICKER_BACKOFF_MAX = 90


def _log_task_done(name: str, task: asyncio.Task[None]) -> None:
    """Consume task exception so asyncio does not log 'Future exception was never retrieved'."""
    try:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("%s task ended with error: %s", name, exc, exc_info=True)
    except asyncio.CancelledError:
        pass


class MarketStreamHub:
    def __init__(self, bybit_client: BybitClient) -> None:
        self._bybit_client = bybit_client
        self._symbol_queues: dict[str, set[asyncio.Queue[TickerTick]]] = defaultdict(set)
        self._stream_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, symbol: str) -> asyncio.Queue[TickerTick]:
        queue: asyncio.Queue[TickerTick] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._symbol_queues[symbol].add(queue)
            if symbol not in self._stream_tasks:
                t = asyncio.create_task(self._run_symbol_stream(symbol))
                t.add_done_callback(lambda task, sym=symbol: _log_task_done(f"ticker_ws[{sym}]", task))
                self._stream_tasks[symbol] = t
        return queue

    async def unsubscribe(self, symbol: str, queue: asyncio.Queue[TickerTick]) -> None:
        async with self._lock:
            if symbol in self._symbol_queues and queue in self._symbol_queues[symbol]:
                self._symbol_queues[symbol].remove(queue)
            if symbol in self._symbol_queues and not self._symbol_queues[symbol]:
                self._symbol_queues.pop(symbol, None)
                task = self._stream_tasks.pop(symbol, None)
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    async def _run_symbol_stream(self, symbol: str) -> None:
        """Maintain Bybit ticker WebSocket; auto-reconnect with backoff on any failure."""
        backoff = _TICKER_BACKOFF_INITIAL
        while True:
            try:
                async for tick in self._bybit_client.stream_ticker(symbol):
                    await self._broadcast(symbol, tick)
                    backoff = _TICKER_BACKOFF_INITIAL
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    "Ticker WS %s: %s (%s); reconnect in %ds",
                    symbol,
                    type(e).__name__,
                    e,
                    backoff,
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                backoff = min(_TICKER_BACKOFF_MAX, max(_TICKER_BACKOFF_INITIAL, backoff * 2))

    async def _broadcast(self, symbol: str, tick: TickerTick) -> None:
        queues = list(self._symbol_queues.get(symbol, set()))
        for q in queues:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await q.put(tick)

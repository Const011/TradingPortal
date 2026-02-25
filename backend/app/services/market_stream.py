import asyncio
from collections import defaultdict

from app.schemas.market import TickerTick
from app.services.bybit_client import BybitClient


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
                self._stream_tasks[symbol] = asyncio.create_task(
                    self._run_symbol_stream(symbol)
                )
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

    async def _run_symbol_stream(self, symbol: str) -> None:
        while True:
            try:
                async for tick in self._bybit_client.stream_ticker(symbol):
                    await self._broadcast(symbol, tick)
            except asyncio.CancelledError:
                break
            except Exception:
                # Reconnect after transient provider/network failures.
                await asyncio.sleep(2)

    async def _broadcast(self, symbol: str, tick: TickerTick) -> None:
        queues = list(self._symbol_queues.get(symbol, set()))
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put(tick)


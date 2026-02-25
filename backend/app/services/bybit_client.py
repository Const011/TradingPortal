import json
from collections.abc import AsyncGenerator

import httpx
import websockets

from app.config import settings
from app.schemas.market import BarUpdate, Candle, SymbolInfo, TickerSnapshot, TickerTick


class BybitClient:
    async def list_spot_symbols(self) -> list[SymbolInfo]:
        url = f"{settings.bybit_rest_base_url}/v5/market/instruments-info"
        params = {"category": "spot"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        payload = response.json()
        raw_symbols = payload.get("result", {}).get("list", [])
        symbols: list[SymbolInfo] = []
        for item in raw_symbols:
            symbols.append(
                SymbolInfo(
                    symbol=item["symbol"],
                    baseCoin=item["baseCoin"],
                    quoteCoin=item["quoteCoin"],
                    status=item["status"],
                )
            )
        return symbols

    async def get_klines(
        self, symbol: str, interval: str = "1", limit: int = 300
    ) -> list[Candle]:
        url = f"{settings.bybit_rest_base_url}/v5/market/kline"
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        payload = response.json()
        raw_klines = payload.get("result", {}).get("list", [])
        candles: list[Candle] = []
        for kline in reversed(raw_klines):
            candles.append(
                Candle(
                    time=int(kline[0]),
                    open=float(kline[1]),
                    high=float(kline[2]),
                    low=float(kline[3]),
                    close=float(kline[4]),
                    volume=float(kline[5]),
                )
            )
        return candles

    async def get_tickers(self, symbols: list[str] | None = None) -> list[TickerSnapshot]:
        url = f"{settings.bybit_rest_base_url}/v5/market/tickers"
        params = {"category": "spot"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        payload = response.json()
        raw_tickers = payload.get("result", {}).get("list", [])
        requested = {item.upper() for item in symbols} if symbols else None
        snapshots: list[TickerSnapshot] = []
        for row in raw_tickers:
            symbol = row.get("symbol", "")
            if requested and symbol.upper() not in requested:
                continue
            snapshots.append(
                TickerSnapshot(
                    symbol=symbol,
                    price=float(row.get("lastPrice", 0.0)),
                    change_24h_percent=float(row.get("price24hPcnt", 0.0)) * 100.0,
                    volume_24h=float(row.get("volume24h", 0.0)),
                )
            )
        return snapshots

    async def stream_ticker(self, symbol: str) -> AsyncGenerator[TickerTick, None]:
        topic = f"tickers.{symbol}"
        async with websockets.connect(settings.bybit_ws_public_spot_url) as connection:
            subscribe_message = json.dumps({"op": "subscribe", "args": [topic]})
            await connection.send(subscribe_message)

            async for message in connection:
                data = json.loads(message)
                raw_payload = data.get("data")
                if raw_payload is None:
                    continue
                if isinstance(raw_payload, list):
                    if not raw_payload:
                        continue
                    row = raw_payload[0]
                else:
                    row = raw_payload
                tick = TickerTick(
                    symbol=row.get("symbol", symbol),
                    price=float(row.get("lastPrice", 0.0)),
                    change_24h_percent=float(row.get("price24hPcnt", 0.0)) * 100.0,
                    volume_24h=float(row.get("volume24h", 0.0)),
                    ts=int(data.get("ts", 0)),
                )
                yield tick

    async def stream_kline(
        self, symbol: str, interval: str
    ) -> AsyncGenerator[BarUpdate, None]:
        """Stream real-time kline updates for the current bar (volume accumulates until confirm=true).
        Uses linear stream; spot may not expose kline topic (same symbols e.g. BTCUSDT work on linear)."""
        topic = f"kline.{interval}.{symbol}"
        async with websockets.connect(settings.bybit_ws_public_linear_url) as connection:
            subscribe_message = json.dumps({"op": "subscribe", "args": [topic]})
            await connection.send(subscribe_message)

            async for message in connection:
                data = json.loads(message)
                raw_payload = data.get("data")
                if raw_payload is None:
                    continue
                if isinstance(raw_payload, list):
                    if not raw_payload:
                        continue
                    row = raw_payload[0]
                else:
                    row = raw_payload
                yield BarUpdate(
                    start=int(row["start"]),
                    end=int(row["end"]),
                    open=float(row["open"]),
                    close=float(row["close"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    volume=float(row["volume"]),
                    confirm=bool(row.get("confirm", True)),
                    timestamp=int(row.get("timestamp", data.get("ts", 0))),
                )


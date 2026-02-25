import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, HTTPException

from app.schemas.market import Candle, SymbolInfo, TickerSnapshot
from app.services.bybit_client import BybitClient
from app.services.market_stream import MarketStreamHub

# Bybit spot kline intervals: 1,3,5,15,30,60,120,240,360,720 (minutes), D, W, M
CANDLE_INTERVALS = frozenset({"1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"})

router = APIRouter(prefix="/api/v1", tags=["market"])


def get_bybit_client() -> BybitClient:
    return BybitClient()


def get_stream_hub() -> MarketStreamHub:
    # Dependency override in main.py will supply singleton.
    raise RuntimeError("stream hub dependency is not configured")


@router.get("/intervals")
async def list_intervals() -> dict[str, list[str]]:
    """Return supported kline intervals for the candles API."""
    return {"intervals": sorted(CANDLE_INTERVALS, key=_interval_sort_key)}


def _interval_sort_key(s: str) -> tuple[int, int]:
    """Order: minutes first (numeric), then D, W, M."""
    if s == "D":
        return (1, 0)
    if s == "W":
        return (2, 0)
    if s == "M":
        return (3, 0)
    return (0, int(s))


@router.get("/symbols", response_model=list[SymbolInfo])
async def list_symbols(bybit_client: BybitClient = Depends(get_bybit_client)) -> list[SymbolInfo]:
    symbols = await bybit_client.list_spot_symbols()
    return [item for item in symbols if item.status == "Trading"]


@router.get("/candles", response_model=list[Candle])
async def list_candles(
    symbol: str = Query(min_length=6, max_length=20),
    interval: str = Query(default="1", description="Kline interval: 1,3,5,15,30,60,120,240,360,720,D,W,M"),
    limit: int = Query(default=300, ge=50, le=1000),
    bybit_client: BybitClient = Depends(get_bybit_client),
) -> list[Candle]:
    if interval not in CANDLE_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval. Allowed: {sorted(CANDLE_INTERVALS)}",
        )
    return await bybit_client.get_klines(symbol=symbol.upper(), interval=interval, limit=limit)


@router.get("/tickers", response_model=list[TickerSnapshot])
async def list_tickers(
    symbols: str | None = Query(default=None),
    bybit_client: BybitClient = Depends(get_bybit_client),
) -> list[TickerSnapshot]:
    requested_symbols = [item.strip().upper() for item in symbols.split(",")] if symbols else None
    return await bybit_client.get_tickers(symbols=requested_symbols)


@router.websocket("/stream/ticks/{symbol}")
async def stream_ticks(
    websocket: WebSocket,
    symbol: str,
    stream_hub: MarketStreamHub = Depends(get_stream_hub),
) -> None:
    await websocket.accept()
    normalized_symbol = symbol.upper()
    queue = await stream_hub.subscribe(normalized_symbol)
    try:
        while True:
            try:
                tick = await asyncio.wait_for(queue.get(), timeout=30)
                await websocket.send_json(tick.model_dump())
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        await stream_hub.unsubscribe(normalized_symbol, queue)


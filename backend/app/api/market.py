"""Market API: endpoints for frontend. Backend talks to Bybit via BybitClient."""
import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, HTTPException

from app.schemas.market import Candle, SymbolInfo, TickerSnapshot
from app.services.bybit_client import BybitClient
from app.services.candle_stream import CandleStreamHub
from app.services.market_stream import MarketStreamHub

logger = logging.getLogger(__name__)

# Bybit spot kline intervals: 1,3,5,15,30,60,120,240,360,720 (minutes), D, W, M
CANDLE_INTERVALS = frozenset({"1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"})

router = APIRouter(prefix="/api/v1", tags=["market"])


def get_bybit_client() -> BybitClient:
    return BybitClient()


def get_stream_hub() -> MarketStreamHub:
    # Dependency override in main.py will supply singleton.
    raise RuntimeError("stream hub dependency is not configured")


def get_candle_stream_hub() -> CandleStreamHub:
    # Dependency override in main.py will supply singleton.
    raise RuntimeError("candle stream hub dependency is not configured")


@router.get("/intervals")
async def list_intervals() -> dict[str, list[str]]:
    """[Frontend] Return supported kline intervals for the chart.
    Used by the frontend to populate interval selector buttons."""
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
    """[Frontend] Return tradable spot symbols.
    Fetches from Bybit; used by the frontend for symbol selector and ticker list."""
    try:
        symbols = await bybit_client.list_spot_symbols()
        return [item for item in symbols if item.status == "Trading"]
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Bybit API unreachable: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Market data temporarily unavailable. Check network or try again later.",
        ) from e

@router.get("/tickers", response_model=list[TickerSnapshot])
async def list_tickers(
    symbols: str | None = Query(default=None),
    bybit_client: BybitClient = Depends(get_bybit_client),
) -> list[TickerSnapshot]:
    """[Frontend] Return 24h ticker snapshots (lastPrice, volume24h, change%) for symbols.
    Fetches from Bybit REST; used by the frontend ticker list only (not for chart data)."""
    requested_symbols = [item.strip().upper() for item in symbols.split(",")] if symbols else None
    try:
        return await bybit_client.get_tickers(symbols=requested_symbols)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Bybit API unreachable: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Market data temporarily unavailable. Check network or try again later.",
        ) from e

@router.get("/candles", response_model=list[Candle])
async def list_candles(
    symbol: str = Query(min_length=6, max_length=20),
    interval: str = Query(default="1", description="Kline interval: 1,3,5,15,30,60,120,240,360,720,D,W,M"),
    limit: int = Query(default=2000, ge=50, le=2000),
    bybit_client: BybitClient = Depends(get_bybit_client),
) -> list[Candle]:
    """[Frontend] Return historical kline (candle) data for a symbol and interval.
    Fetches from Bybit REST; used for initial chart load or standalone history fetch."""
    if interval not in CANDLE_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval. Allowed: {sorted(CANDLE_INTERVALS)}",
        )
    try:
        return await bybit_client.get_klines(symbol=symbol.upper(), interval=interval, limit=limit)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Bybit API unreachable: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Market data temporarily unavailable. Check network or try again later.",
        ) from e


@router.websocket("/stream/candles/{symbol}")
async def stream_candles(
    websocket: WebSocket,
    symbol: str,
    interval: str = Query(default="1", description="Kline interval (e.g. 1, 5, 15, 60, D)"),
    volume_profile_window: int = Query(default=2000, ge=100, le=10000),
    candle_stream_hub: CandleStreamHub = Depends(get_candle_stream_hub),
) -> None:
    """[Frontend] Stream merged candle data: initial snapshot + live bar updates + indicators.
    Backend merges Bybit REST kline (history) + Bybit kline WebSocket (current bar);
    sends snapshot and upsert events with computed volume profile."""
    if interval not in CANDLE_INTERVALS:
        await websocket.close(code=4000)
        return
    await websocket.accept()
    normalized_symbol = symbol.upper()
    queue = await candle_stream_hub.subscribe(
        normalized_symbol, interval, volume_profile_window=volume_profile_window
    )
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30)
                await websocket.send_json(payload)
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        await candle_stream_hub.unsubscribe(normalized_symbol, interval, queue)


@router.websocket("/stream/ticks/{symbol}")
async def stream_ticks(
    websocket: WebSocket,
    symbol: str,
    stream_hub: MarketStreamHub = Depends(get_stream_hub),
) -> None:
    """[Frontend] Stream ticker updates (lastPrice, volume24h, change%).
    Proxies Bybit ticker WebSocket; for ticker list only, not for chart bar updates."""
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


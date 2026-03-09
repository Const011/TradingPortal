import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exec import router as exec_router
from app.api.market import (
    get_candle_stream_hub,
    get_stream_hub,
    router as market_router,
)
from app.config import settings
from app.services.bybit_client import BybitClient
from app.services.candle_stream import CandleStreamHub
from app.services.market_stream import MarketStreamHub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

if settings.mode == "trading":
    dry_run_note = " [EXECUTOR_DRY_RUN=true: no real orders/stops]" if settings.executor_dry_run else ""
    print(
        f"Gateway: port={settings.backend_port} mode=trading market={settings.market} "
        f"symbol={settings.trading_symbol} interval={settings.trading_interval} "
        f"position_size={settings.position_size or '(not set)'} leverage={settings.leverage}{dry_run_note}"
    )
else:
    print(f"Gateway: port={settings.backend_port} mode=simulation")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.mode == "trading":
        await candle_stream_hub.start_heartbeat(
            symbol=settings.trading_symbol,
            interval=settings.trading_interval,
            volume_profile_window=settings.bars_window,
            strategy_markers="trade",
        )
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bybit_client = BybitClient()
stream_hub = MarketStreamHub(bybit_client=bybit_client)
candle_stream_hub = CandleStreamHub(
    bybit_client=bybit_client,
    snapshot_limit=settings.bars_window if settings.mode == "trading" else 2000,
)


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(market_router)
app.include_router(exec_router)
app.dependency_overrides[get_stream_hub] = lambda: stream_hub
app.dependency_overrides[get_candle_stream_hub] = lambda: candle_stream_hub


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.market import (
    get_candle_stream_hub,
    get_stream_hub,
    router as market_router,
)
from app.config import settings
from app.services.bybit_client import BybitClient
from app.services.candle_stream import CandleStreamHub
from app.services.market_stream import MarketStreamHub

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bybit_client = BybitClient()
stream_hub = MarketStreamHub(bybit_client=bybit_client)
candle_stream_hub = CandleStreamHub(bybit_client=bybit_client, snapshot_limit=1500)


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(market_router)
app.dependency_overrides[get_stream_hub] = lambda: stream_hub
app.dependency_overrides[get_candle_stream_hub] = lambda: candle_stream_hub


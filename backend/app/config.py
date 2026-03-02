from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_cors_origins() -> list[str]:
    """Frontend (4000) + ports 9000-9100 for simulation and trading gateways."""
    return (
        ["http://localhost:4000"]
        + [f"http://localhost:{p}" for p in range(9000, 9101)]
    )


class Settings(BaseSettings):
    app_name: str = "Trading Portal Backend"
    bybit_rest_base_url: str = "https://api.bybit.com"
    bybit_ws_public_spot_url: str = "wss://stream.bybit.com/v5/public/spot"
    bybit_ws_public_linear_url: str = "wss://stream.bybit.com/v5/public/linear"
    cors_origins: list[str] = Field(default_factory=_default_cors_origins)

    # Simulation: 9000. Trading: 9001, 9002, ... (one per ticker/timeframe)
    mode: Literal["simulation", "trading"] = "simulation"
    # Trade log: {trade_log_dir}/{symbol}_{interval}/index.jsonl, current.json
    trade_log_dir: str = "logs/trades"
    # Trading mode: fixed symbol, interval, bars window (not changeable in UI)
    trading_symbol: str = "BTCUSDT"
    trading_interval: str = "60"
    bars_window: int = 2000

    # Data fetch: heartbeat polls Bybit REST at this interval (both simulation and trading)
    fetch_interval_sec: int = 60

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()


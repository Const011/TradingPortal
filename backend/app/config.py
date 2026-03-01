from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Trading Portal Backend"
    bybit_rest_base_url: str = "https://api.bybit.com"
    bybit_ws_public_spot_url: str = "wss://stream.bybit.com/v5/public/spot"
    bybit_ws_public_linear_url: str = "wss://stream.bybit.com/v5/public/linear"
    cors_origins: list[str] = ["http://localhost:4000", "http://localhost:4001"]

    # Multi-gateway: trading (4000/9000) vs simulation (4001/9001)
    mode: Literal["simulation", "trading"] = "simulation"
    trade_log_dir: str = "logs/trades"
    # Trading mode: fixed symbol and interval (not changeable in UI)
    trading_symbol: str = "BTCUSDT"
    trading_interval: str = "60"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()


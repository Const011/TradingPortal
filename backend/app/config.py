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
    # Private REST (orders, positions, wallet): optional; leave empty for public-only.
    # To trade as a subaccount (e.g. 550311863): create a sub API key for that UID via
    # POST /v5/user/create-sub-api (master key with "Subaccount Transfer" etc.) and use
    # that key/secret here. Account = which key you use; no subUID param on order/create.
    # https://bybit-exchange.github.io/docs/v5/user/create-subuid-apikey
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    bybit_recv_window: int = 10000
    # Optional: sub UID you are trading as (for display/logging). Set when using a sub API key.
    bybit_sub_uid: str = ""
    # Trading market: "spot" or "linear" (linear = USDT perpetuals).
    market: Literal["spot", "linear"] = "spot"
    cors_origins: list[str] = Field(default_factory=_default_cors_origins)

    # Simulation: 9000. Trading: 9001, 9002, ... (one per ticker/timeframe)
    mode: Literal["simulation", "trading"] = "simulation"
    # Server port (set by run-dev-*.sh via BACKEND_PORT for startup console output).
    backend_port: int = Field(9001, description="Port the server runs on (env: BACKEND_PORT)")
    # Trade log: {trade_log_dir}/{symbol}_{interval}/index.jsonl, current.json
    trade_log_dir: str = "logs/trades"
    # Trading mode: fixed symbol, interval, bars window (not changeable in UI)
    trading_symbol: str = "BTCUSDT"
    trading_interval: str = "60"
    bars_window: int = 2000

    # Data fetch: heartbeat polls Bybit REST at this interval (both simulation and trading)
    fetch_interval_sec: int = 60
    # After a failed REST/WS call, backoff grows up to this cap (seconds) before retry.
    network_reconnect_max_sec: int = Field(
        120, description="Max delay between retries when Bybit/network fails (env: NETWORK_RECONNECT_MAX_SEC)"
    )

    # Trade executor: order qty and leverage (set via POSITION_SIZE and LEVERAGE in run-dev-trading.sh).
    position_size: str = ""
    # Linear only: leverage (e.g. 10 for 10x). Executor calls set_linear_leverage before/on entry.
    leverage: int = Field(10, description="Linear leverage (env: LEVERAGE)")

    # Temporary debug stubs: if True, no real Bybit order/stop calls; log params to console and update files as if success.
    # Set EXECUTOR_DRY_RUN=false when testing is complete to enable live execution.
    executor_dry_run: bool = Field(True, description="Dry run: no real orders/stops (env: EXECUTOR_DRY_RUN)")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()


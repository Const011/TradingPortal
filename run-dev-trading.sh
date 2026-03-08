#!/usr/bin/env bash
set -e

# Trading gateway: one per ticker/timeframe. Override via env:
#   BACKEND_PORT        - port (default 9001)
#   TRADING_SYMBOL      - ticker, e.g. BTCUSDT (default)
#   TRADING_INTERVAL    - timeframe: 1,5,15,60,240,D (default 60)
#   BARS_WINDOW         - bars for chart, e.g. 2000 or 5000 (default 2000)
#   FETCH_INTERVAL_SEC  - data fetch frequency in seconds (default 60)
#   MARKET              - "spot" or "linear" (default spot)
#   POSITION_SIZE       - order qty for this symbol, e.g. 0.001 (default 0.001)
#   LEVERAGE            - linear only: leverage e.g. 10 for 10x (default 10)
#   EXECUTOR_DRY_RUN    - true (default): no real Bybit orders/stops; log params and update files. Set false for live.
#
# Examples:
#   ./run-dev-trading.sh
#   TRADING_SYMBOL=ETHUSDT TRADING_INTERVAL=15 BACKEND_PORT=9002 ./run-dev-trading.sh
#   TRADING_SYMBOL=XRPUSDT POSITION_SIZE=100 LEVERAGE=20 BACKEND_PORT=9003 ./run-dev-trading.sh

export BACKEND_PORT="${BACKEND_PORT:-9001}"
export TRADING_SYMBOL="${TRADING_SYMBOL:-BTCUSDT}"
export TRADING_INTERVAL="${TRADING_INTERVAL:-60}"
export POSITION_SIZE="${POSITION_SIZE:-0.001}"
export BARS_WINDOW="${BARS_WINDOW:-2000}"
export FETCH_INTERVAL_SEC="${FETCH_INTERVAL_SEC:-60}"
export MARKET="${MARKET:-spot}"
export LEVERAGE="${LEVERAGE:-10}"
export EXECUTOR_DRY_RUN="${EXECUTOR_DRY_RUN:-true}"


ROOT="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source $ROOT/../.venv/bin/activate

pip install -q -r requirements.txt
MODE=trading uvicorn app.main:app --reload --port "$BACKEND_PORT"

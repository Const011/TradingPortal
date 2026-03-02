#!/usr/bin/env bash
set -e

# Trading gateway: one per ticker/timeframe. Override via env:
#   BACKEND_PORT        - port (default 9001)
#   TRADING_SYMBOL      - ticker, e.g. BTCUSDT (default)
#   TRADING_INTERVAL    - timeframe: 1,5,15,60,240,D (default 60)
#   BARS_WINDOW         - bars for chart, e.g. 2000 or 5000 (default 2000)
#   FETCH_INTERVAL_SEC  - data fetch frequency in seconds (default 60)
#
# Examples:
#   ./run-dev-trading.sh
#   TRADING_SYMBOL=ETHUSDT TRADING_INTERVAL=15 BACKEND_PORT=9002 ./run-dev-trading.sh
#   TRADING_SYMBOL=XRPUSDT BARS_WINDOW=5000 FETCH_INTERVAL_SEC=30 BACKEND_PORT=9003 ./run-dev-trading.sh

BACKEND_PORT="${BACKEND_PORT:-9001}"
export TRADING_SYMBOL="${TRADING_SYMBOL:-BTCUSDT}"
export TRADING_INTERVAL="${TRADING_INTERVAL:-60}"
export BARS_WINDOW="${BARS_WINDOW:-2000}"
export FETCH_INTERVAL_SEC="${FETCH_INTERVAL_SEC:-60}"

ROOT="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source $ROOT/../.venv/bin/activate

pip install -q -r requirements.txt
MODE=trading uvicorn app.main:app --reload --port "$BACKEND_PORT"

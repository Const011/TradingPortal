#!/usr/bin/env bash
set -e

# Trading gateway: ETHUSDT @ 60m on port 9002. Override via env (same as run-dev-trading-btc.sh).
#   BACKEND_PORT        - default 9002
#   TRADING_SYMBOL      - default ETHUSDT
#   TRADING_INTERVAL    - default 60
#   POSITION_SIZE       - default 0.04
#   BARS_WINDOW, FETCH_INTERVAL_SEC, MARKET, LEVERAGE, EXECUTOR_DRY_RUN — see run-dev-trading-btc.sh

export BACKEND_PORT="${BACKEND_PORT:-9002}"
export TRADING_SYMBOL="${TRADING_SYMBOL:-ETHUSDT}"
export TRADING_INTERVAL="${TRADING_INTERVAL:-60}"
export POSITION_SIZE="${POSITION_SIZE:-0.03}"
export BARS_WINDOW="${BARS_WINDOW:-2000}"
export FETCH_INTERVAL_SEC="${FETCH_INTERVAL_SEC:-60}"
export MARKET="${MARKET:-linear}"
export LEVERAGE="${LEVERAGE:-1}"
export EXECUTOR_DRY_RUN="${EXECUTOR_DRY_RUN:-false}"

ROOT="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source $ROOT/../.venv/bin/activate

pip install -q -r requirements.txt
MODE=trading uvicorn app.main:app --reload --port "$BACKEND_PORT"

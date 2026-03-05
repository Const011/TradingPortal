#!/usr/bin/env bash
set -e

# Simulation backend only (port 9000). Run frontend separately via run-dev.sh.
# Override FETCH_INTERVAL_SEC for data fetch frequency (default 60).
# MARKET controls Bybit market: "spot" or "linear" (default spot).
BACKEND_PORT="${BACKEND_PORT:-9000}"
export FETCH_INTERVAL_SEC="${FETCH_INTERVAL_SEC:-60}"
export MARKET="${MARKET:-linear}"

ROOT="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source $ROOT/../.venv/bin/activate

pip install -q -r requirements.txt
MODE=simulation uvicorn app.main:app --reload --port "$BACKEND_PORT"

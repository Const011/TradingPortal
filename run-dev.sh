#!/usr/bin/env bash
set -e


# Combined dev runner: backend (simulation mode, port 9000) + frontend (port 4000 by default).
# Override:
#   BACKEND_PORT       - backend port (default 9000)
#   FRONTEND_PORT      - frontend port (default 4000)
#   FETCH_INTERVAL_SEC - data fetch frequency in seconds (default 60)
#   MARKET             - "spot" or "linear" (default linear)
BACKEND_PORT="${BACKEND_PORT:-9000}"
FRONTEND_PORT="${FRONTEND_PORT:-4000}"
export BARS_WINDOW="${BARS_WINDOW:-2000}"

export FETCH_INTERVAL_SEC="${FETCH_INTERVAL_SEC:-60}"
export MARKET="${MARKET:-spot}"

ROOT="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM


cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source $ROOT/../.venv/bin/activate

pip install -q -r requirements.txt

MODE=simulation uvicorn app.main:app --reload --port "$BACKEND_PORT" &
BACKEND_PID=$!

cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then
  npm install
fi
NEXT_PUBLIC_MODE=simulation NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT" npm run dev -- -p "$FRONTEND_PORT"



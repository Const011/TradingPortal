#!/usr/bin/env bash
set -e

# Configurable ports (default: trading gateway)
BACKEND_PORT="${BACKEND_PORT:-9000}"
FRONTEND_PORT="${FRONTEND_PORT:-4000}"

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PID=""

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
MODE=trading uvicorn app.main:app --reload --port "$BACKEND_PORT" &
BACKEND_PID=$!

cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then
  npm install
fi
NEXT_PUBLIC_MODE=trading NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT" npm run dev -- -p "$FRONTEND_PORT"

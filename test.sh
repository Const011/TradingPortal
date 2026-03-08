#!/usr/bin/env bash
# Curl tests for manual exec endpoints. Usage: ./test.sh [PORT]
# Example: ./test.sh 9001   or   ./test.sh
# Ensure backend is running (e.g. trading gateway on PORT) and BYBIT_* are set in .env for private endpoints.

PORT="${1:-9001}"
BASE="http://localhost:${PORT}"

echo "=== Exec endpoints (port $PORT) ==="

echo ""
echo "--- GET /api/v1/exec/wallet-balance ---"
curl -s -w "\n" "${BASE}/api/v1/exec/wallet-balance?accountType=UNIFIED"

echo ""
echo "--- GET /api/v1/exec/positions (symbol=BTCUSDT) ---"
curl -s -w "\n" "${BASE}/api/v1/exec/positions?symbol=BTCUSDT"

echo ""
echo "--- GET /api/v1/exec/orders (symbol=BTCUSDT, category=linear) ---"
curl -s -w "\n" "${BASE}/api/v1/exec/orders?symbol=BTCUSDT&category=linear"

echo ""
echo "--- POST /api/v1/exec/order (market buy linear, small qty - uncomment to run) ---"
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/order" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","side":"Buy","orderType":"Market","qty":"0.001","category":"linear"}'

echo ""
echo "--- POST /api/v1/exec/order/cancel (set orderId first) ---"
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/order/cancel" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","category":"linear","orderId":"YOUR_ORDER_ID"}'

echo ""
echo "--- POST /api/v1/exec/positions/trading-stop (set stopLoss for linear position) ---"
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/positions/trading-stop" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","stopLoss":65000.0}'

echo ""
echo "--- POST /api/v1/exec/positions/close (closes position - use with care) ---"
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/positions/close" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","category":"linear"}'

echo ""
echo "=== done ==="

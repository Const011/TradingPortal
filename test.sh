#!/usr/bin/env bash
# Curl tests for manual exec endpoints. Usage: ./test.sh [PORT]
# Example: ./test.sh 9001   or   ./test.sh
# Ensure backend is running (e.g. trading gateway on PORT) and BYBIT_* are set in .env for private endpoints.
# Subaccount: orders/wallet/positions go to the account of the API key. To use sub 550311863, create a sub API key
# (POST /v5/user/create-sub-api, subuid=550311863) and set BYBIT_API_KEY/BYBIT_API_SECRET to that key.

PORT="${1:-9000}"
BASE="http://localhost:${PORT}"

echo "=== Exec endpoints (port $PORT) ==="

echo ""
echo "--- GET /api/v1/exec/wallet-balance ---"
# Bybit: https://bybit-exchange.github.io/docs/v5/account/wallet-balance
curl -s -w "\n" "${BASE}/api/v1/exec/wallet-balance?accountType=UNIFIED"

echo ""
echo "--- GET /api/v1/exec/positions (symbol=BTCUSDT) ---"
# Bybit: https://bybit-exchange.github.io/docs/v5/position/position-info
curl -s -w "\n" "${BASE}/api/v1/exec/positions?symbol=BTCUSDT"

echo ""
echo "--- GET /api/v1/exec/orders (symbol=BTCUSDT, category=linear) ---"
# Bybit: https://bybit-exchange.github.io/docs/v5/order/open-order
curl -s -w "\n" "${BASE}/api/v1/exec/orders?symbol=BTCUSDT&category=linear"

echo ""
echo "--- POST /api/v1/exec/positions/set-leverage (10x - run before limit order) ---"
# Bybit: https://bybit-exchange.github.io/docs/v5/position/leverage
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/positions/set-leverage" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","buyLeverage":10}'

echo ""
echo "--- POST /api/v1/exec/order (limit buy linear, price 63000; optional: stopLoss/takeProfit on order = auto-applied when filled) ---"
# Bybit: https://bybit-exchange.github.io/docs/v5/order/create-order
# To have SL/TP applied as soon as the order fills, pass stopLoss (and optionally takeProfit) in the order:
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/order" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","side":"Buy","orderType":"Limit","qty":"0.001","price":"63000","category":"linear","stopLoss":60000,"tpslMode":"Full"}'

echo ""
echo "--- POST /api/v1/exec/order/cancel (set orderId first) ---"
# Bybit: https://bybit-exchange.github.io/docs/v5/order/cancel-order
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/order/cancel" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","category":"linear","orderId":"14f2fcf9-f32b-4236-8dba-fab9b6921ab3"}'

echo ""
echo "--- POST /api/v1/exec/positions/trading-stop (change SL/TP on *existing* position only) ---"
# Bybit: https://bybit-exchange.github.io/docs/v5/position/trading-stop
# Use this to update SL/TP after the position exists. For new orders, set stopLoss/takeProfit on the order so they apply when filled.
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/positions/trading-stop" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","stopLoss":61000.0}'

echo ""
echo "--- POST /api/v1/exec/positions/close (closes position - use with care) ---"
# Bybit: position list + create order (reduceOnly). https://bybit-exchange.github.io/docs/v5/position/position-info https://bybit-exchange.github.io/docs/v5/order/create-order
# curl -s -w "\n" -X POST "${BASE}/api/v1/exec/positions/close" \
#   -H "Content-Type: application/json" \
#   -d '{"symbol":"BTCUSDT","category":"linear"}'

echo ""
echo "=== done ==="

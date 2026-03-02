# Trading Portal

Spot-first trading portal skeleton with:

- `backend/`: FastAPI service with Bybit REST + WebSocket market integration; **computes all indicators and runs trade strategy logic**.
- `frontend/`: Next.js portal with ticker switcher and Lightweight Charts view; **visualization only** (displays pre-calculated data).
- `docs/`: BRD (`brd-architecture.md`) and architecture decisions (`adr/001-v1-architecture-decisions.md`).

## Quick Start

```bash
./run-dev-simulation.sh
./run-dev-trading.sh
```

Starts the frontend on port 4000 and the simulation backend on port 9000. Open `http://localhost:4000`.

## Gateway Selector

The frontend has a **Gateway** control under the header. Use it to switch between:

- **Simulation** — Connects to simulation backend (port 9000). Full flexibility: select any ticker, timeframe, and indicators.
- **Trade** — Connects to trading backend. Port 9001 for first instance, 9002 for second, etc. (one per ticker/timeframe). Symbol and interval are fixed by the gateway.

## Run Scripts

| Script | What it starts |
|--------|----------------|
| `./run-dev-simulation.sh` | Simulation backend 9000 only |
| `./run-dev-trading.sh` | Trading backend 9001 (configurable per instance) |

### Trading Gateway Parameters

Each trading gateway is configured at startup via environment variables:

| Variable | Default | Description |
|----------|---------|--------------|
| `BACKEND_PORT` | 9001 | Port for this instance |
| `TRADING_SYMBOL` | BTCUSDT | Ticker (e.g. ETHUSDT, XRPUSDT) |
| `TRADING_INTERVAL` | 60 | Timeframe: 1, 5, 15, 60, 240, D |
| `BARS_WINDOW` | 2000 | Number of bars for chart (e.g. 2000, 5000) |
| `FETCH_INTERVAL_SEC` | 60 | Data fetch frequency (seconds); heartbeat polls Bybit at this interval |
| `TRADE_LOG_DIR` | logs/trades | Base dir for trade log; files use `{symbol}_{interval}/` subdirs |

**Examples:**

```bash
# Default: BTCUSDT 60m on 9001
./run-dev-trading.sh

# ETHUSDT 15m on 9002
TRADING_SYMBOL=ETHUSDT TRADING_INTERVAL=15 BACKEND_PORT=9002 ./run-dev-trading.sh

# XRPUSDT 60m, 5000 bars, on 9003
TRADING_SYMBOL=XRPUSDT BARS_WINDOW=5000 BACKEND_PORT=9003 ./run-dev-trading.sh
```

To use both simulation and trading:

1. Run `./run-dev.sh` (frontend + simulation).
2. In another terminal, run `./run-dev-trading.sh` (or with overrides).
3. In the UI, select **Trade**, enter the port, click **Connect**.

## Manual Backend Run

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
MODE=simulation uvicorn app.main:app --reload --port 9000
# Trading (default: BTCUSDT 60m 2000 bars):
MODE=trading uvicorn app.main:app --reload --port 9001
# Trading with custom params:
TRADING_SYMBOL=ETHUSDT TRADING_INTERVAL=15 BARS_WINDOW=5000 MODE=trading uvicorn app.main:app --reload --port 9002
```

## Manual Frontend Run

```bash
cd frontend
npm install
npm run dev
```

The frontend connects via the gateway selector; no env vars needed for the default flow.

See `docs/single-frontend-gateway-plan.md` for the implementation plan.

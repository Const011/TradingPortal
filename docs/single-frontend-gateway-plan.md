# Single Frontend, Multi-Gateway Implementation Plan

## 1. Overview

Change from **two frontends** (4000/4001) to **one frontend** (4000) that can connect to either a simulation or trading backend. The user selects the gateway via a control under the "Trading Portal" caption.

### Goals

1. **Single frontend** on port 4000 only.
2. **Gateway selector** — User chooses Simulation or Trade; when Trade, user enters the trading gateway port.
3. **Gateway handshake** — On connect, backend returns mode and (when trading) symbol, interval, bars_window.
4. **Frontend behavior by mode:**
   - **Simulation:** Full flexibility — tickers, timeframes, volume profile window.
   - **Trading:** Read-only — single ticker, single timeframe, fixed bars window; controls disabled.
5. **Trade display** — Simulation: from stream (strategy per tick). Trading: from trade log + current.json.

---

## 2. Architecture Summary

| Aspect | Simulation | Trading |
|--------|------------|---------|
| Frontend port | 4000 | 4000 |
| Backend port | 9000 | 9001, 9002, ... (one per ticker/timeframe) |
| Ticker selection | User selects any | Fixed (one) |
| Timeframe | User selects | Fixed |
| Bars window | User configurable | Fixed (e.g. 2000, 5000) |
| Trade markers | From stream (strategySignals) | From trade log API |
| Results table | Computed from stream | From trade log |

---

## 3. Backend Changes

### 3.1 Config (`app/config.py`)

Add `bars_window` for trading mode:

```python
# Trading mode: fixed symbol, interval, bars window (not changeable in UI)
trading_symbol: str = "BTCUSDT"
trading_interval: str = "60"
bars_window: int = 2000  # NEW: configurable via BARS_WINDOW env
```

### 3.2 GET /api/v1/mode

Extend response when `mode=trading`:

```json
{
  "mode": "trading",
  "symbol": "BTCUSDT",
  "interval": "60",
  "bars_window": 2000
}
```

### 3.3 CandleStreamHub

- Use `settings.bars_window` when `mode=trading` for snapshot limit (instead of hardcoded 2000).
- Pass `bars_window` to candle stream subscription when in trading mode (or derive from config).

### 3.4 CORS

- Ensure `http://localhost:4000` is in `cors_origins` (remove 4001 if no longer needed, or keep for flexibility).

### 3.5 Run Scripts and Trading Gateway Parameters

- **run-dev-simulation.sh:** Simulation backend 9000 only.
- **run-dev-trading.sh:** Trading backend 9001 by default; configurable per instance.

**Trading gateway startup parameters** (env vars passed into each instance):

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_PORT` | 9001 | Port for this instance |
| `TRADING_SYMBOL` | BTCUSDT | Ticker (e.g. ETHUSDT, XRPUSDT) |
| `TRADING_INTERVAL` | 60 | Timeframe: 1, 5, 15, 60, 240, D |
| `BARS_WINDOW` | 2000 | Number of bars for chart |

Examples:

```bash
# Default: BTCUSDT 60m on 9001
./run-dev-trading.sh

# ETHUSDT 15m on 9002
TRADING_SYMBOL=ETHUSDT TRADING_INTERVAL=15 BACKEND_PORT=9002 ./run-dev-trading.sh

# XRPUSDT 60m, 5000 bars, on 9003
TRADING_SYMBOL=XRPUSDT BARS_WINDOW=5000 BACKEND_PORT=9003 ./run-dev-trading.sh
```

---

## 4. Frontend Changes

### 4.1 Gateway Selector Component

New control under "Trading Portal" caption:

- **Radio or dropdown:** Simulation | Trade
- **When Trade:** Input field for backend port (default 9001)
- **Connect / Apply:** Fetches `GET /api/v1/mode` from selected URL; stores `backendBaseUrl` and gateway config in context.

### 4.2 API URL Source

- **Remove** `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_MODE` from build-time env (or keep as fallback defaults).
- **Dynamic:** Backend URL = `http://localhost:{port}` where port comes from gateway selector.
- **Persistence:** Optionally persist last selected gateway (simulation/trade + port) in localStorage.

### 4.3 Market Data Context

- **On gateway change:** Clear symbols, tickers, candles, selectedSymbol; reconnect all streams to new backend.
- **Fetch gateway config:** On connect, call `GET /api/v1/mode`. Store `mode`, `symbol`, `interval`, `bars_window`.
- **Simulation mode:** Current behavior — fetch symbols, tickers; user selects symbol/interval; volume profile window from preferences.
- **Trading mode:** Use `symbol`, `interval`, `bars_window` from config; disable symbol/interval selectors; pass `bars_window` to candle stream.

### 4.4 Candle Stream URL

- **Simulation:** `volume_profile_window` from user preferences.
- **Trading:** `volume_profile_window` = `bars_window` from gateway config.

### 4.5 Caption

- Display "Trading Portal - SIMULATION" or "Trading Portal - TRADING" based on `mode` from gateway response.

---

## 5. Implementation Phases

### Phase 1: Backend Config and API

1. Add `bars_window` to `config.py` (env: `BARS_WINDOW`, default 2000).
2. Extend `GET /api/v1/mode` to return `bars_window` when `mode=trading`.
3. Wire `bars_window` into CandleStreamHub for trading mode snapshot limit.

### Phase 2: Gateway Selector UI

1. Create `GatewaySelector` component:
   - Mode: Simulation | Trade (radio or select)
   - Port input (visible when Trade, default 9000)
   - Connect/Apply button
2. Place under "Trading Portal" caption in `MarketShell` or layout.
3. On Apply: compute `backendBaseUrl = http://localhost:{port}`; trigger gateway config fetch.

### Phase 3: Frontend Context Refactor

1. **Remove** build-time `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_MODE` as sole source of truth.
2. Add `backendBaseUrl` and `gatewayConfig` to a new `GatewayContext` or extend `MarketDataContext`:
   - `backendBaseUrl: string` — set by gateway selector
   - `gatewayConfig: { mode, symbol?, interval?, bars_window? }` — from `GET /api/v1/mode`
3. On gateway selector Apply:
   - Set `backendBaseUrl`
   - Fetch `GET /api/v1/mode` from that URL
   - Store `gatewayConfig`
   - Reset market data (symbols, tickers, candles, selectedSymbol)
   - Re-run data loading (symbols, tickers, candle stream, tick stream)

### Phase 4: Mode-Dependent Behavior

1. **Simulation:** Keep current behavior — symbols from API, user selects symbol/interval, volume profile window from preferences.
2. **Trading:** 
   - Set `selectedSymbol` = config.symbol, `chartInterval` = config.interval
   - Disable ticker list symbol clicks, interval buttons
   - Use `bars_window` for candle stream `volume_profile_window`
   - Fetch trade log and current trades as today

### Phase 5: Run Scripts and Defaults

1. **run-dev.sh:** Frontend 4000 + simulation backend 9000.
2. **run-dev-simulation.sh:** Simulation backend 9000 only.
3. **run-dev-trading.sh:** Trading backend 9001 by default; supports `BACKEND_PORT`, `TRADING_SYMBOL`, `TRADING_INTERVAL`, `BARS_WINDOW`.

### Phase 6: Cleanup and Docs

1. Remove `NEXT_PUBLIC_MODE` from run scripts (or keep as initial default only).
2. Update README with new flow: single frontend, gateway selector, how to run each backend.
3. Update `.env.example` for frontend (no required API URL if using selector).

---

## 6. File Changes Summary

| File | Changes |
|------|---------|
| `backend/app/config.py` | Add `bars_window` |
| `backend/app/api/market.py` | Extend `GET /api/v1/mode` with `bars_window` |
| `backend/app/services/candle_stream.py` | Use `bars_window` when mode=trading |
| `frontend/src/components/gateway-selector.tsx` | **NEW** — mode + port UI |
| `frontend/src/contexts/gateway-context.tsx` | **NEW** or extend market-data-context — backend URL, gateway config |
| `frontend/src/contexts/market-data-context.tsx` | Use gateway context for URL/config; mode-dependent behavior |
| `frontend/src/lib/api/market.ts` | `backendBaseUrl` from context, not env |
| `frontend/src/components/market-shell.tsx` | Add GatewaySelector; show caption from gateway config |
| `run-dev.sh` | Frontend 4000; optionally simulation backend 9001 |
| `run-dev-simulation.sh` | Backend 9001 only |
| `run-dev-trading.sh` | Backend 9000 only |
| `docs/brd-architecture.md` | Updated (done) |

---

## 7. Default Ports

| Component | Port |
|-----------|------|
| Frontend | 4000 |
| Simulation backend | 9000 |
| Trading backend | 9001, 9002, ... (one per ticker/timeframe; user specifies via selector) |

---

## 8. Open Questions

1. **Initial state:** On first load, should the frontend auto-connect to simulation (9001) or require user to click Connect?
2. **Persistence:** Persist last gateway selection (mode + port) in localStorage?
3. **Connection status:** Show connection status (connected / disconnected / error) in gateway selector?

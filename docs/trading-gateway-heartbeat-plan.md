# Backend Heartbeat Implementation Plan

## 1. Overview

Replace the current frontend-driven / Bybit WebSocket-driven data flow with a **unified heartbeat-driven flow** for both simulation and trading gateways. The backend runs its own heartbeat that fetches data from Bybit at a configurable interval. The frontend connects and receives data—it does not initiate fetches.

### Goals

1. **Single mechanic** — Both simulation and trading use the same heartbeat-based data flow.
2. **Backend-owned heartbeat** — Fetches run at `FETCH_INTERVAL_SEC` regardless of frontend connection.
3. **Frontend simplification** — Frontend connects to WebSocket and receives data; no `GET /candles` or other fetch triggers.
4. **Configurable frequency** — `FETCH_INTERVAL_SEC` set when gateway is launched (env var).

---

## 2. Architecture Summary

| Aspect | Current | Target |
|--------|---------|--------|
| Simulation data trigger | Frontend connects → backend fetches + Bybit WS | Backend heartbeat (REST poll at interval) |
| Trading data trigger | Frontend connects → backend fetches + Bybit WS | Backend heartbeat (REST poll at interval) |
| Bybit WebSocket | Used for live bar updates | Removed; REST polling only |
| Frontend role | Subscribes; triggers stream start | Subscribes; receives from backend cache |
| `GET /candles` | Used for initial/standalone fetch | Deprecated or returns cached data only |

---

## 3. Backend Changes

### 3.1 Config (`app/config.py`)

```python
# Data fetch: heartbeat polls Bybit REST at this interval (both modes)
fetch_interval_sec: int = 60  # env: FETCH_INTERVAL_SEC
```

### 3.2 CandleStreamHub Refactor

**Current:** `_run_stream` fetches REST once, then `async for bar in stream_kline(...)` drives updates.

**Target:** `_run_heartbeat` loop:
- `await asyncio.sleep(fetch_interval_sec)`
- `candles = await bybit_client.get_klines(...)`
- Update state, compute indicators/strategy, build snapshot payload
- Broadcast to all queues

**Simulation:** Heartbeat runs for the symbol/interval of the first (or active) subscription. When frontend subscribes with symbol X interval Y, ensure heartbeat exists for (X, Y); attach queue to it.

**Trading:** On app startup (`mode=trading`), start heartbeat for `(trading_symbol, trading_interval)` immediately. Frontend subscribes and attaches to that stream.

### 3.3 Stream Lifecycle

- **Simulation:** Stream (heartbeat) starts when first client subscribes to a given (symbol, interval). When last client unsubscribes, heartbeat can stop (or keep running for a grace period).
- **Trading:** Heartbeat starts on app startup; never stops until app shutdown. Symbol/interval fixed.

### 3.4 Payload Format

Keep existing snapshot format: `{ event: "snapshot", candles, graphics }`. No upsert events (no within-bar WebSocket updates). Each heartbeat produces a full snapshot. Frontend replaces candles + graphics on each snapshot.

### 3.5 GET /candles

Options:
- **A)** Deprecate; frontend uses WebSocket only.
- **B)** Return cached data from CandleStreamHub if available; no Bybit fetch.

Recommend **B** for backward compatibility; document that it returns cached data when heartbeat has run.

---

## 4. Frontend Changes

### 4.1 Remove Fetch Triggers

- Do not call `fetchCandles` for chart data. Rely on WebSocket snapshot only.
- WebSocket connection remains the single source of candle + graphics data.

### 4.2 Snapshot Handling

- Each WebSocket message with `event: "snapshot"` replaces candles and graphics (no upsert merge).
- Remove upsert handling if heartbeat only sends snapshots (or keep for compatibility if we later add upserts).

### 4.3 Connection Flow

- Connect to `WS /stream/candles/{symbol}?interval=...&...` when symbol/interval selected.
- Wait for first snapshot; display. Subsequent snapshots replace.
- Same flow for simulation and trading.

---

## 5. Implementation Order

1. Add `fetch_interval_sec` to config.
2. Implement `_run_heartbeat` in CandleStreamHub (replace Bybit WebSocket with REST polling loop).
3. Wire trading mode: start heartbeat on startup for (trading_symbol, trading_interval).
4. Wire simulation mode: start heartbeat on first subscribe for (symbol, interval); stop when last unsubscribe.
5. Update `GET /candles` to return cached data when available.
6. Frontend: remove `fetchCandles` usage for chart; ensure snapshot-only handling works.
7. Update run scripts to pass `FETCH_INTERVAL_SEC`.

---

## 6. Files to Modify

| File | Changes |
|------|---------|
| `backend/app/config.py` | Add `fetch_interval_sec` |
| `backend/app/services/candle_stream.py` | Replace `_run_stream` with `_run_heartbeat`; remove Bybit kline WebSocket |
| `backend/app/main.py` | Start trading heartbeat on startup when `mode=trading` |
| `backend/app/api/market.py` | `GET /candles` returns cached from hub when available |
| `frontend/src/contexts/market-data-context.tsx` | Remove fetchCandles for chart; snapshot-only flow |
| `frontend/src/lib/api/market.ts` | Optional: deprecate or document `fetchCandles` |
| `run-dev-trading.sh` | Export `FETCH_INTERVAL_SEC` if needed |
| `run-dev-simulation.sh` | Export `FETCH_INTERVAL_SEC` if needed |

---

## 7. Edge Cases

- **First connect before first heartbeat:** If frontend connects before first fetch completes, queue receives snapshot when ready. No empty state needed if we block subscribe until first fetch, or send empty snapshot and then update.
- **Interval alignment:** For 60m candles, fetching every 60s is fine; each fetch gets latest closed + forming bar. No need to align to bar boundaries.
- **Multiple symbols (simulation):** Each (symbol, interval) has its own heartbeat when subscribed. Unsubscribe stops heartbeat after last client leaves.

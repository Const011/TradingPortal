# Multi-Gateway Architecture: Trading vs Simulation

## 1. Overview

Replace the unified gateway with **separate backend instances** that can run independently:

- **Trading gateway** — Frontend 4000, backend 9000 (default). Strategy runs on live candle stream; emits signals that may lead to real orders. All trade events (entry, stop move, exit) are **logged to a trade log file**. Chart displays logged trades, not live strategy output. Results use actual logged trade data.
- **Simulation gateway** — Frontend 4001, backend 9001. Strategy runs on live candle stream; emits simulated trade events in graphics payload. No order execution. Frontend computes results from simulated events.

Both gateways share the same codebase; mode and port are configured via command-line parameters.

---

## 2. Architecture Changes

### 2.1 Runtime Modes

| Mode       | Frontend | Backend | Strategy output in stream | Chart data source      | Results data source   |
|------------|----------|---------|---------------------------|------------------------|-----------------------|
| Trading    | 4000     | 9000    | No (or optional)          | Trade log API          | Trade log             |
| Simulation | 4001     | 9001    | Yes (graphics.strategySignals) | Stream (simulated events) | Simulated (from stream) |

### 2.2 Data Flow Comparison

**Simulation mode (current behavior):**
```
CandleStreamHub → strategy → TradeEvent[] → graphics.strategySignals
                                                      ↓
Frontend ← stream (candles + graphics) ← markers, stopLines, events
                                                      ↓
Frontend: computeStrategyResults(events, candles, stopSegments)
```

**Trading mode (new):**
```
CandleStreamHub → strategy → TradeEvent[] → OrderIntent → Execution
                              ↓
                         TradeLogService (append: entry + snapshot, stop move, exit)
                                                      ↓
Frontend ← stream (candles + graphics, NO strategySignals)
Frontend ← GET /api/v1/trade-log?symbol=...&interval=... → logged trades
                                                      ↓
Frontend: display logged markers/stops, computeStrategyResults(loggedTrades)
```

### 2.3 Trade Log Structure

Each log entry is a JSON line (JSONL) or structured record. Types:

1. **Entry** — When an order is placed:
   - `type: "entry"`
   - `time`, `barIndex`, `side`, `price`, `initialStopPrice`, `targetPrice`, `context`
   - **Full snapshot** (same format as "Export for AI"): candles, volumeProfile, supportResistance, orderBlocks, structure, event context. Stored as **one Markdown file per entry** (e.g. `logs/trades_{symbol}_{interval}/entry_{timestamp}.md`), using the same Markdown format as `buildStrategyExportMarkdown`. The log record references `snapshotFile: "entry_1234567890.md"`.

2. **Stop move** — When trailing stop is updated:
   - `type: "stop_move"`
   - `time`, `price`, `side`, `tradeId` (or entry time as id)

3. **Exit** — When position is closed (stop hit, TP hit, or manual):
   - `type: "exit"`
   - `time`, `closePrice`, `closeReason` (stop | take_profit | manual), `tradeId`, `points`

Log files: e.g. `logs/trades_{symbol}_{interval}/index.jsonl` for the index; `logs/trades_{symbol}_{interval}/entry_*.md` for entry snapshots.

**Current trades file** (`logs/trades_{symbol}_{interval}/current.json`): Persists open positions for gateway restart recovery. Contains `{ trades: [{ tradeId, entryTime, entryPrice, currentStopPrice, initialStopPrice, side, targetPrice }] }`. Updated on entry (add), stop move (update currentStopPrice), exit (remove). Read by gateway on stream start to restore state.

---

## 3. Command-Line Configuration

### 3.1 Backend

```bash
# Trading (default: 9000)
uvicorn app.main:app --port 9000
# or
MODE=trading uvicorn app.main:app --port 9000

# Simulation (9001)
uvicorn app.main:app --port 9001
# or
MODE=simulation uvicorn app.main:app --port 9001
```

**Config parameters:**
- `MODE`: `simulation` | `trading` (default: `simulation` in config; run scripts use trading as default)
- `BACKEND_PORT`, `FRONTEND_PORT`: configurable in run scripts (defaults: trading 9000/4000, simulation 9001/4001)

**Settings (config.py):**
```python
mode: Literal["simulation", "trading"] = "simulation"
trade_log_dir: str = "logs/trades"
trading_symbol: str = "BTCUSDT"   # fixed when mode=trading (env: TRADING_SYMBOL)
trading_interval: str = "60"      # fixed when mode=trading (env: TRADING_INTERVAL)
```

When `mode=trading`, symbol and interval are fixed by gateway config; the frontend disables the ticker and interval selectors.

### 3.2 Frontend

Frontend must know:
1. **Backend URL** — `http://localhost:9000` or `http://localhost:9001` (from env or config)
2. **Mode** — To display caption and choose data source

**Options:**
- A) Single frontend build; backend URL + mode from env (`NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_MODE`)
- B) Frontend fetches `GET /api/v1/mode` → `{ "mode": "simulation" | "trading" }` and infers port from the request origin

**Recommendation:** A) Env vars. Run scripts:
- `run-dev-trading.sh` → backend 9000, frontend 4000, `NEXT_PUBLIC_API_URL=http://localhost:9000` `NEXT_PUBLIC_MODE=trading`
- `run-dev-simulation.sh` → backend 9001, frontend 4001, `NEXT_PUBLIC_API_URL=http://localhost:9001` `NEXT_PUBLIC_MODE=simulation`

Ports are configurable via `BACKEND_PORT` and `FRONTEND_PORT` env vars.

---

## 4. Frontend Changes

### 4.1 Caption

- **Simulation:** "Trading Portal - SIMULATION"
- **Trading:** "Trading Portal - TRADING"

Display in header/nav or page title.

### 4.2 Chart Data Source by Mode

| Mode       | Strategy markers / stop lines | Results table |
|------------|-------------------------------|---------------|
| Simulation | From `graphics.strategySignals` (stream) | `computeStrategyResults(events, candles, stopSegments)` from stream |
| Trading    | From `GET /api/v1/trade-log?...`         | From trade log (precomputed or derived from log) |

### 4.3 Trade Log API Response

`GET /api/v1/trade-log?symbol=BTCUSDT&interval=60` returns:

```json
{
  "mode": "trading",
  "trades": [
    {
      "entryDateTime": "2026-02-25T10:30:00.000Z",
      "side": "long",
      "entryPrice": 97500.5,
      "closeDateTime": "2026-02-25T11:45:00.000Z",
      "closePrice": 97800.2,
      "closeReason": "take_profit",
      "points": 299.7,
      "markers": [...],
      "stopSegments": [...]
    }
  ]
}
```

Or a format compatible with `StrategySignalsData` so the chart can reuse the same rendering.

### 4.4 Entry Snapshot Format (Audit)

For each entry, the full snapshot of data that led to the signal is stored for future audit. Use the **same file format as "Export for AI"** — the Markdown format from `buildStrategyExportMarkdown` (bar data, indicators, trade orders, trailing stops). **One file per entry**, e.g. `logs/trades_{symbol}_{interval}/entry_{timestamp}.md`.

The trade log (JSONL or structured index) references the snapshot file path for each entry.

---

## 5. Backend Changes

### 5.1 Trade Log Service

New module: `app/services/trade_log.py`

- `append_entry(trade_id, event, snapshot)` — Write entry to JSONL index; write full snapshot to **one Markdown file per entry** using the same format as "Export for AI" (`buildStrategyExportMarkdown`).
- `append_stop_move(trade_id, time, price, side)` — Append stop update to index
- `append_exit(trade_id, time, close_price, close_reason, points)` — Append exit to index
- `get_trades(symbol, interval, since?)` — Read and parse index; return trades for API

Entry snapshot: one `.md` file per entry, same Markdown structure as the frontend "Export for AI" (bar data, indicators, trade orders, trailing stops). The backend will implement an equivalent of `buildStrategyExportMarkdown` to produce this format. Path: `logs/trades_{symbol}_{interval}/entry_{timestamp}.md`.

### 5.2 CandleStreamHub / Strategy Integration

**Simulation mode:**
- Unchanged. Strategy runs; output goes to `graphics.strategySignals`; streamed to frontend.

**Trading mode:**
- Strategy runs; on each `TradeEvent`:
  - If order is placed: `TradeLogService.append_entry(...)` with full snapshot
  - Do **not** (or optionally) add `strategySignals` to graphics payload for chart
- On stop trail: `TradeLogService.append_stop_move(...)`
- On exit: `TradeLogService.append_exit(...)`

### 5.3 New API Endpoints

- `GET /api/v1/mode` — Return `{ "mode": "simulation" | "trading" }` (optional; frontend can use env)
- `GET /api/v1/trade-log?symbol=BTCUSDT&interval=60&since=...` — Return logged trades in format suitable for chart + results table

---

## 6. Implementation Plan

### Phase 1: Backend Mode and Port Configuration

1. Add `mode` and `port` to `app/config.py` (from env `MODE`, `PORT`).
2. Update `run-dev.sh` / add `run-dev-simulation.sh` and `run-dev-trading.sh`:
   - Trading: `uvicorn ... --port 9000`, `MODE=trading`; frontend `-p 4000`
   - Simulation: `uvicorn ... --port 9001`, `MODE=simulation`; frontend `-p 4001`
3. Add `GET /api/v1/mode` returning `{ "mode": "simulation" | "trading" }`.

### Phase 2: Trade Log Service (Trading Mode Only)

1. Create `app/services/trade_log.py`:
   - Define log entry schema (entry, stop_move, exit).
   - Implement `append_entry`, `append_stop_move`, `append_exit`.
   - Entry snapshot: reuse logic from strategy export (candles, indicators, event).
   - Log file path: `{trade_log_dir}/{symbol}_{interval}.jsonl` or similar.
2. Wire TradeLogService into trading flow (when `mode == "trading"`):
   - On strategy event → order placed: call `append_entry` with snapshot.
   - On trailing stop update: call `append_stop_move`.
   - On position exit: call `append_exit` with close price, reason, points.

### Phase 3: Trade Log API

1. Add `GET /api/v1/trade-log?symbol=...&interval=...`.
2. Response format: list of trades with `StrategySignalsData`-compatible structure (markers, stopLines, events) plus computed results (points, closeReason).

### Phase 4: CandleStreamHub Behavior by Mode

1. In **simulation** mode: include `strategySignals` in graphics (current behavior).
2. In **trading** mode: omit `strategySignals` from graphics (chart will use trade log instead).

### Phase 5: Frontend Mode Awareness

1. Add `NEXT_PUBLIC_MODE` and `NEXT_PUBLIC_API_URL` to frontend env.
2. Update layout/header to show "Trading Portal - SIMULATION" or "Trading Portal - TRADING".
3. Ensure API client uses `NEXT_PUBLIC_API_URL` for all requests.

### Phase 6: Frontend Chart and Results by Mode

1. **Simulation mode:** Keep current behavior — use `strategySignals` from stream; compute results from stream data.
2. **Trading mode:**
   - Fetch trade log via `GET /api/v1/trade-log?symbol=...&interval=...` when symbol/interval match.
   - Use trade log data for markers, stop lines, and results table.
   - Do not use `graphics.strategySignals` (it will be empty or absent).

### Phase 7: Run Scripts and Documentation

1. Create `run-dev-simulation.sh` and `run-dev-trading.sh`.
2. Update `run-dev.sh` to default to trading (frontend 4000, backend 9000). Ports configurable via `FRONTEND_PORT`, `BACKEND_PORT`.
3. Update `docs/brd-architecture.md` with multi-gateway section.
4. Update README with instructions for running each mode.

---

## 7. File Structure (New/Modified)

```
backend/
  app/
    config.py              # + mode, port, trade_log_dir
    api/
      market.py            # (existing)
      trade_log.py         # NEW: GET /trade-log
    services/
      trade_log.py         # NEW: append_entry, append_stop_move, append_exit, get_trades
      candle_stream.py     # MOD: omit strategySignals when mode=trading
      trading_strategy/    # (existing; invoked by candle_stream)

run-dev-simulation.sh      # NEW
run-dev-trading.sh         # NEW
run-dev.sh                 # MOD: default trading (4000/9000)

frontend/
  .env.development         # or .env.local: NEXT_PUBLIC_MODE, NEXT_PUBLIC_API_URL
  src/
    components/
      layout.tsx           # or header: show mode in caption
    contexts/
      market-data-context  # MOD: fetch trade log when mode=trading
    lib/
      api/                 # MOD: use NEXT_PUBLIC_API_URL
```

---

## 8. Open Questions for Review

1. **Trade log granularity:** One index file per symbol+interval, or one per day, or one global? Recommendation: per symbol+interval for simplicity.
2. **Entry snapshot format:** Use the same Markdown format as "Export for AI" — one `.md` file per entry. **Decided.**
3. **Real-time updates in trading mode:** Should the frontend poll trade log, or should the backend push updates via WebSocket when a new trade/stop/exit is logged? Phase 1: poll on symbol/interval change; Phase 2: optional WebSocket for live updates.
4. **Execution service:** This plan assumes an execution path exists (strategy → OrderIntent → Execution). If not yet implemented, `append_entry` can be called when strategy emits an event that *would* lead to an order; actual execution can be stubbed.

---

## 9. Summary

| Component        | Trading (4000/9000)   | Simulation (4001/9001)    |
|-----------------|-----------------------|---------------------------|
| Strategy output | Not in stream         | In stream                 |
| Trade log       | All entries/stops/exits | Not used               |
| Chart markers   | From trade log API    | From stream               |
| Results table   | From trade log (precomputed) | From stream (compute) |
| Caption         | Trading Portal - TRADING | Trading Portal - SIMULATION |

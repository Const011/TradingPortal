## Simulation mode: indicator recomputation and no-future-leakage plan

### 1. Problem recap

- **Current behavior**
  - In `candle_stream.py`, `_make_snapshot_payload` always feeds the **entire `candles` window** into:
    - `compute_structure(candles, ...)`
    - `compute_order_blocks(candles, ...)`
    - `build_volume_profile_from_candles(candles, ...)`
    - `compute_support_resistance_lines(...)`
    - `compute_order_block_trend_following(candles, ...)`
  - In **simulation mode**, the frontend:
    - Receives these indicators and `graphics.strategySignals` from the candle stream.
    - Reconstructs trades and computes PnL from the same full-window data (see BRD §10 and §12.3).
- **Issue**
  - For bar \(i\), indicators and strategy are computed from a snapshot that already includes bars \(> i\).
  - This creates **forward-looking bias**: strategy decisions for earlier bars can depend on information that only exists because later bars are present.
  - Result: simulation shows **exaggerated performance** versus what would be achievable in live trading.

### 2. Architectural direction

- **Separate display indicators from simulation indicators**
  - **Display path (CandleStream → frontend)**:
    - Keep full-window indicator computation for rich visuals (OB zones, structure, volume profile, S/R).
  - **Simulation path (SimulatorService)**:
    - Run a dedicated bar-by-bar engine that:
      - Recomputes indicators on a **truncated window ending at the current bar** (e.g. last 2000 bars).
      - Feeds only that bar’s indicators into the trading strategy.
      - Produces canonical per-trade results and metrics.
- **Unify strategy semantics**
  - The Trading Strategy module stays **mode-agnostic**, but:
    - It is invoked in a strictly **bar-sequenced** manner for simulation.
    - Live/trading heartbeat continues to use whatever data it has “so far” (no future is available anyway).
- **Single source of truth for simulation**
  - The **backend SimulatorService** becomes the **only** producer of simulation results (trades, PnL, metrics).
  - Frontend in simulation mode stops computing PnL directly from streaming markers.

### 3. SimulatorService: design and responsibilities

- **New module**
  - Create `backend/app/services/simulator/` with core entrypoint:
    - `run_simulation(symbol, interval, start_ts, end_ts, params) -> SimulationRun`
  - `SimulationRun` should align with BRD’s domain model:
    - `strategyVersion`, `datasetRange`, `metrics`, `artifacts` (trade list, markers, stop segments).

- **Data sourcing**
  - For v1:
    - Use `BybitClient.get_klines(symbol, interval, limit=...)` or a historical candle store for:
      - `warmup_bars` before `start_ts` (e.g. 2000 bars) so indicators can stabilize.
      - Full range up to `end_ts`.
  - In a later iteration, prefer reading from the canonical candle store (PostgreSQL) if populated.

- **Bar-by-bar simulation loop**
  - General pattern:
    - Let `candles_all` be the entire historical slice (`warmup + simulation range`).
    - Choose `bars_window` (e.g. 2000) for the indicator lookback.
    - Find `start_index` such that:
      - `candles_all[start_index].time >= start_ts`.
      - `start_index` is at least `warmup_bars` after the beginning where possible.
  - For each bar index `i` from `start_index` to `len(candles_all) - 1`:
    - Define window:
      - `window_start = max(0, i - bars_window + 1)`
      - `window = candles_all[window_start : i + 1]`
    - **Indicators (no future)**
      - Structure:
        - `structure = compute_structure(window, include_candle_colors=True)`
      - Order blocks (reuse structure pivots):
        - `ob_result = compute_order_blocks(window, show_bull=0, show_bear=0, swing_pivots=structure.get("swingPivots") or {})`
      - Volume profile:
        - `vp = build_volume_profile_from_candles(window, time=window[-1].time // 1000, width=6, window_size=min(len(window), bars_window))`
      - Support/resistance:
        - `sr_lines = compute_support_resistance_lines(vp["profile"]) if vp else []`
    - **Strategy evaluation for this bar**
      - Invoke the existing OB trend-following strategy:
        - `trade_events, stop_segments = compute_order_block_trend_following(window, structure.get("swingPivots") or {}, candle_colors=structure.get("candleColors"), sr_lines=sr_lines)`
      - Filter results for the **last candle in `window`** (bar index `len(window) - 1`, which corresponds to global index `i`):
        - Only events and stop segments whose `bar_index` (or equivalent index) matches the current bar.
    - **Trade state update**
      - Feed filtered `trade_events` and `stop_segments` into a shared trade reconstruction engine (see section 4).
      - Maintain in-memory state of open trades, stop levels, and completed trades for PnL.
    - **Per-bar artifacts (optional)**
      - Record per-bar markers / graphics needed for UI (entry markers, stop lines, etc.) in a compact structure for the eventual SimulationRun artifacts.

- **Outputs**
  - At the end of the loop, build:
    - `trades`: list of completed and open trades with:
      - `entryDateTime`, `side`, `entryPrice`,
      - `closeDateTime`, `closePrice`, `closeReason`,
      - `points`, `markers`, `stopSegments`, `events`.
    - `metrics`:
      - Total points, average points per trade.
      - Win rate, max drawdown, Sharpe-like ratio.
    - `graphics` (optional, for chart overlays in simulation view):
      - Series of markers and stop lines derived from the same trade state.

### 4. Shared trade reconstruction engine

- **Goal**
  - Avoid duplicating “event → trade” logic across:
    - `strategy_output_to_chart`.
    - Frontend “Strategy Results Calculation”.
    - New SimulatorService.

- **New utility**
  - Create something like `backend/app/services/trading_strategy/trade_builder.py` with APIs such as:
    - `update_trades_for_bar(events: list[TradeEvent], stop_segments: list[StopSegment], candles: list[Candle], bar_index: int, state: TradeSimulationState) -> None`
    - Where `TradeSimulationState` holds:
      - Open trades, stop levels, event history.
      - Completed trades and their realized PnL.

- **Behavior**
  - Must match the semantics described in BRD §12.3:
    - **Entry**
      - Entry price = close price of the entry bar.
    - **Stop hit**
      - Close price = close of the first bar whose range touches the effective stop level.
    - **Target hit (if used)**
      - Close price = close of the first bar whose range touches the target price.
    - Points:
      - Long: `points = closePrice - entryPrice`.
      - Short: `points = entryPrice - closePrice`.
  - This engine should be the single implementation that:
    - SimulatorService uses to compute trades and PnL.
    - `strategy_output_to_chart` uses to derive chart markers and per-trade summaries.
    - Trading-mode trade log adapter can reuse for consistency where applicable.

### 5. Backend API wiring

- **Simulation endpoints (BRD-aligned)**
  - Implement or finish:
    - `POST /api/v1/strategies/{strategyId}/simulate`
      - Request:
        - `symbol`, `interval`, `startTs`, `endTs` or `bars`, and strategy parameters.
      - Behavior:
        - Call `run_simulation(...)`.
        - Optionally persist `SimulationRun` and return `runId`.
      - Response:
        - `trades`: per-trade objects compatible with trading-mode trade log format.
        - `metrics`: summary metrics.
        - Optional lightweight `graphics` data for overlays.
    - `GET /api/v1/simulations/{runId}` (if persistence is desired).

- **Consistency with trading**
  - Ensure simulation trade objects have the same shape as those returned by:
    - `GET /api/v1/trade-log?symbol=...&interval=...`
  - This allows the frontend to render both simulation and live trades with the same components.

### 6. CandleStreamHub and frontend adjustments

- **CandleStreamHub (`candle_stream.py`)**
  - Keep existing behavior for **visual indicators**:
    - Compute structure, OB zones, volume profile, and S/R on the full `candles` snapshot.
    - Optionally compute `strategySignals` for **marker previews**.
  - Clarify semantics:
    - When `settings.mode == "simulation"`:
      - Any `strategySignals` in `graphics` are **visual hints only**, not the source of record for PnL.
    - When `settings.mode == "trading"`:
      - Continue to delete `strategySignals` before sending to clients (as now).

- **Frontend (simulation mode)**
  - Stop deriving final PnL directly from the candle stream.
  - On “Run Simulation” / parameter change:
    - Call `POST /api/v1/strategies/{strategyId}/simulate`.
    - Use response `trades` + `metrics` to:
      - Populate the Strategy Results table.
      - Draw entry/exit markers and trailing stops.
  - Candle stream remains responsible for:
    - Live-like chart behavior and visual indicators.
    - Optional real-time previews of strategy markers (derived from stream), visually distinguished from persisted simulation runs.

### 7. Testing strategy

- **Unit tests**
  - Indicators:
    - For each indicator (structure, OB, volume profile, S/R), add tests asserting:
      - Determinism when adding one bar at a time (no hidden lookahead).
  - Trade builder:
    - Synthetic scenarios for:
      - Simple entry/stop-hit sequences.
      - Trailing stops.
      - Mixed long/short trades.
    - Assert PnL and close reasons match expectations.
  - SimulatorService:
    - Use small synthetic candle sets where the presence of future bars would clearly change decisions if leaked.
    - Verify that bar-by-bar simulation respects “no future” by construction.

- **Integration tests**
  - End-to-end simulation via API:
    - Run the same simulation twice:
      - Once with a larger `bars_window`.
      - Once with a smaller `bars_window` (but still sufficient to cover indicator lookbacks).
    - Assert that results are identical, confirming no dependency on candles beyond the current bar.

- **Regression**
  - Compare old front-end-computed simulation results vs. new backend SimulatorService on historical data:
    - Expect:
      - Lower or equal performance (no more future leakage).
      - Semantically similar trade shapes where indicator and strategy rules allow.

### 8. Documentation updates

- **BRD updates**
  - In **FR-4 Indicator Engine**:
    - Note that indicators support a **simulation-safe recomputation mode** where they are evaluated only on history up to each bar.
  - In **FR-6 Simulation Tool**:
    - Explicitly describe that the SimulatorService:
      - Replays candles bar-by-bar.
      - Recomputes indicators for each bar up to that point.
      - Uses the same Trading Strategy module as live mode.
  - In **Frontend Strategy Results Calculation** section:
    - Clarify:
      - Simulation results (trades + metrics) now come from the backend simulation API.
      - Frontend no longer infers PnL directly from strategy markers in the candle stream.


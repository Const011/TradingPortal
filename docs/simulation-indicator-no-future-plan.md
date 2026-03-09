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
  - This creates **forward-looking bias**: strategy decisions and indicator overlays on earlier bars can depend on information that only exists because later bars are present.
  - Result: simulation shows **exaggerated performance** and misleading visuals versus what would be achievable in live trading.

### 2. New direction: separate, on-demand precise simulator

- **Goal**
  - Keep the main candle stream and heartbeat logic lightweight and responsive.
  - Move the expensive, prefix-only, no-future-leakage algorithm into a **separate simulation engine** that runs **only on demand**.
- **Mode separation**
  - **Trading mode (`settings.mode == "trading"`)**
    - Leave runtime behavior unchanged:
      - Full-window indicators and a single strategy call per heartbeat.
      - `_apply_trade_logging` remains the only writer for trade logs and `current.json`.
  - **Simulation mode (live view)**
    - Continue using the existing fast snapshot behavior for candles and indicators (and optional approximate strategy markers).
    - This view is optimized for interactivity, not for perfect no-future correctness.
  - **Precise Simulation mode (on demand)**
    - Implemented as a separate backend module and API.
    - Enforces the invariant:
      - For each bar index \(i\), both indicators and strategy decisions for that bar are computed only from candles up to and including that bar (`candles[0 : i+1]` or a trailing window ending at `i`); never from bars \(> i\).

### 3. Precise Simulator: backend behavior

- **New module**
  - Create `backend/app/services/precise_simulator/` with core entrypoint:
    - `run_precise_simulation(symbol, interval, start_ts, end_ts, params) -> SimulationRun`.
  - `SimulationRun` should include:
    - `trades`: full per-trade objects (entry/exit, reason, points).
    - `stopSegments` and `events` for chart overlays.
    - `metrics`: net points, average per trade, win rate, max drawdown, etc.

- **Inputs and data source**
  - Arguments:
    - `symbol`, `interval`, and either `start_ts` / `end_ts` or a `bars` count.
    - Strategy parameters or a reference to the active `StrategyVersion`.
  - Candle data:
    - Prefer reading from the canonical candle store (DB) if available.
    - Fallback: `BybitClient.get_klines` to fetch:
      - Sufficient warm-up history (`warmup_bars`) before the visible range.
      - Complete visible range used for precise simulation.

- **Bar-by-bar no-future loop**
  - Let `candles_all` be the full sequence (warm-up + visible).
  - Choose `bars_window` for indicator lookback (e.g. 2000).
  - For each bar index `i` in `range(0, len(candles_all))`:
    - Define prefix/trailing window ending at `i`:
      - `window_start = max(0, i - bars_window + 1)`.
      - `window = candles_all[window_start : i + 1]` (bars 0..i only).
    - Compute indicators on `window`:
      - `structure_i = compute_structure(window, include_candle_colors=True)`.
      - `ob_i = compute_order_blocks(window, swing_pivots=structure_i.get("swingPivots") or {}, show_bull=0, show_bear=0)`.
      - `vp_i = build_volume_profile_from_candles(window, time=window[-1].time // 1000, width=6, window_size=min(len(window), bars_window))`.
      - `sr_lines_i = compute_support_resistance_lines(vp_i["profile"]) if vp_i else []`.
    - Run strategy on `window`:
      - `trade_events_i, stop_segments_i = compute_order_block_trend_following(window, structure_i.get("swingPivots") or {}, candle_colors=structure_i.get("candleColors"), sr_lines=sr_lines_i)`.
    - Filter to the current bar:
      - Local index in `window`: `local_i = len(window) - 1`.
      - Keep only events where `ev.bar_index == local_i`, rebasing them to global index `i`.
      - Normalize stop segments so they align with global times and indices.
    - Accumulate:
      - `all_trade_events` and `all_stop_segments` across all bars.
  - At the end:
    - Convert accumulated events and stops into chart markers:
      - `strategySignals = strategy_output_to_chart(all_trade_events, all_stop_segments, interval)`.
    - Build per-trade objects and metrics:
      - Either by reusing the existing results-table logic on the backend, or via a shared trade-builder utility.
    - Return a `SimulationRun` object containing:
      - `trades`, `metrics`, and `graphics` (including `strategySignals`).

### 4. API and frontend wiring for “Precise simulate”

- **Backend API**
  - Add `POST /api/v1/strategies/{strategyId}/simulate-precise`:
    - Request body:
      - `symbol`, `interval`, and either `startTs` / `endTs` or a `bars` count.
      - Strategy parameters or the ID of the active strategy version.
    - Behavior:
      - Calls `run_precise_simulation(...)`.
      - Optionally persists the resulting `SimulationRun` and includes a `runId` in the response.
    - Response:
      - `trades`: full precise trade list (shape compatible with trade-log API).
      - `metrics`: precise summary metrics.
      - `graphics`: at least `strategySignals` for the chart, optionally updated indicator overlays.

- **Frontend changes**
  - In simulation mode, in the controls area above the results table:
    - Place a **“Precise simulate”** toggle button **right after** the “Export for AI” control.
  - Behavior:
    - When the toggle is **off** (default), all simulation-related calls continue to use the existing **quick simulation** path (current fast behavior).
    - When the toggle is switched **on**, all subsequent simulation actions (e.g. “Run simulation”, parameter changes) are routed to the **precise simulation endpoint** instead of the quick one:
      - Frontend calls the precise simulation API with the current symbol, interval, visible range, and parameters.
      - Shows a loading indicator while the precise run is executing.
      - On success:
        - Replaces the chart’s strategy markers (entries, exits, stop lines) with `graphics.strategySignals` from the precise run.
        - Rebuilds the results table using the `trades` and `metrics` from the response.
        - Optionally annotates the UI to indicate that results are from a **Precise simulation**.
    - Toggling **back off** returns the behavior to the quick simulation path (no calls to the precise simulator).

### 5. Testing strategy

- **Unit tests**
  - For indicators (`compute_structure`, `compute_order_blocks`, volume profile, S/R):
    - Verify that running them bar-by-bar on prefixes and aggregating gives consistent results with one-shot computations on the same prefixes.
  - For strategy:
    - On synthetic data, confirm that the bar-by-bar precise loop matches running the strategy once per prefix and inspecting only the last bar’s events.
- **Simulation correctness sanity checks**
  - Given a small fixed candle set:
    - Run the existing “fast” simulation flow (for comparison only).
    - Run the new precise simulator.
    - Show that truncating the data at bar `k` and re-running yields identical decisions up to `k`, confirming no future leakage.

### 6. Documentation updates

- **BRD updates**
  - In **FR-4 Indicator Engine** and **FR-6 Simulation Tool**:
    - Note that:
      - The main heartbeat-based flows remain lightweight and may use snapshot-based logic for speed.
      - A separate **Precise Simulation** path exists which recomputes indicators and strategy **per bar** on prefixes ending at the current bar index (no use of future bars) and runs only when requested.
  - In **Candle Stream** / **Simulation Tool** sections:
    - Clarify that:
      - Trading mode uses full-window indicators and a single strategy call per heartbeat (no future exists).
      - Simulation mode’s default live view is optimized for speed, while high-accuracy evaluation is provided by the on-demand Precise Simulation engine triggered from the UI.
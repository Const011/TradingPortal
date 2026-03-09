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

### 2. Direction: fix inside `candle_stream` without a new service

- **Goal**
  - Fix simulation correctness **in-place** in `candle_stream._make_snapshot_payload` without introducing a separate SimulatorService.
- **Mode separation**
  - **Trading mode (`settings.mode == "trading"`)**
    - Leave behavior unchanged:
      - Full-window indicators and single strategy call.
      - `_apply_trade_logging` remains the only writer for trade logs and `current.json`.
  - **Simulation mode (`settings.mode == "simulation"`)**
    - Enforce a strict invariant:
      - For each bar index \(i\), **both** indicators and strategy decisions for that bar are computed only from candles up to and including that bar:
        - Allowed history for bar \(i\): `candles[0 : i+1]` (or a trailing window ending at `i`).
        - **Forbidden**: any use of bars with index \(> i\).

### 3. Simulation-mode behavior in `_make_snapshot_payload`

- **Inputs**
  - `candles`: full snapshot window from Bybit (e.g. last 300–2000 bars).
  - `volume_profile_window`: maximum bars to include in volume profile computation.
  - `strategy_markers`: `"off" | "simulation" | "trade"`.
  - `symbol`, `interval`, `state`: as currently defined.

- **Trading mode branch (unchanged)**
  - When `settings.mode == "trading"`:
    - Keep the existing implementation:
      - Compute `structure_result`, `ob_result`, `vp`, `sr_lines` on full `candles`.
      - Call `compute_order_block_trend_following(candles, ...)` once.
      - Convert to chart data via `strategy_output_to_chart`.
      - Call `_apply_trade_logging(...)`.
      - Remove `strategySignals` from `graphics` before returning.

- **Simulation mode branch (new bar-by-bar logic)**
  - When `settings.mode == "simulation"`:
    - **Do not** compute any indicators or strategy directly on the full `candles` window.
    - Instead:
      1. **Prepare accumulators**
         - `all_trade_events: list[TradeEvent] = []`
         - `all_stop_segments: list[StopSegment] = []`
         - Indicator graphics accumulators, for example:
           - `ob_graphics_accum = {...}` (matching the shape expected for `graphics["orderBlocks"]`).
           - `structure_graphics_accum = {...}` (structure swings and colors).
           - `vp_graphics_accum = {...}` (volume profile bins).
           - `sr_lines_accum: list[...] = []` (support/resistance lines).
      2. **Decide warm-up and optional trailing window**
         - Get `warmup_bars` from the strategy config (`strategy-order-block-trend-following`), or use its default (1000).
         - Optionally define `bars_window` (e.g. 2000) to cap indicator lookback for performance.
      3. **Loop over bars**
         - For each global bar index `i` in `range(0, len(candles))`:
           - Define a history window ending at `i`:
             - `window_start = max(0, i - bars_window + 1)` (or `0` to use all history).
             - `window = candles[window_start : i + 1]`.
             - This window includes **bars 0..i only**; no future bars.
           - **Compute indicators for this window**
             - `structure_i = compute_structure(window, include_candle_colors=True)`.
             - `ob_i = compute_order_blocks(window, swing_pivots=structure_i.get("swingPivots") or {}, show_bull=0, show_bear=0)`.
             - `vp_i = build_volume_profile_from_candles(window, time=window[-1].time // 1000, width=6, window_size=min(len(window), volume_profile_window))`.
             - `sr_lines_i = compute_support_resistance_lines(vp_i["profile"]) if vp_i else []`.
           - **Update indicator graphics accumulators**
             - Merge `structure_i` into `structure_graphics_accum` (swings and candle colors as of bar `i`).
             - Merge `ob_i` into `ob_graphics_accum` (active OB zones as of bar `i`).
             - Merge `vp_i` (if not `None`) into `vp_graphics_accum` for the final volume profile.
             - Append `sr_lines_i` to `sr_lines_accum` or maintain only the latest effective lines, depending on how the frontend expects the data.
           - **Run strategy for this window when enabled**
             - Only if `strategy_markers in ("simulation", "trade")`:
               - `trade_events_i, stop_segments_i = compute_order_block_trend_following(window, structure_i.get("swingPivots") or {}, candle_colors=structure_i.get("candleColors"), sr_lines=sr_lines_i)`.
               - Determine the local index of the current bar within `window`:
                 - `local_i = len(window) - 1`.
               - **Filter to the current bar only**:
                 - `events_for_bar = [ev for ev in trade_events_i if ev.bar_index == local_i]`.
                 - `stops_for_bar = [...]` (filter `stop_segments_i` to segments that start/stop at `local_i` or cover `local_i`, depending on actual `StopSegment` schema).
               - Append:
                 - `all_trade_events.extend(events_for_bar)`.
                 - `all_stop_segments.extend(stops_for_bar)`.
      4. **Build final graphics object**
         - After the loop:
           - `graphics = {`
           - `  "orderBlocks": ob_graphics_accum,`
           - `  "smartMoney": {"structure": structure_graphics_accum},`
           - `}`
           - If `vp_graphics_accum` is not empty:
             - `graphics["volumeProfile"] = vp_graphics_accum`.
             - `graphics["supportResistance"] = {"lines": sr_lines_accum}` (or a deduplicated/filtered version).
           - If `strategy_markers in ("simulation", "trade")`:
             - `chart_data = strategy_output_to_chart(all_trade_events, all_stop_segments, interval)`.
             - `graphics["strategySignals"] = chart_data`.
      5. **Skip trade logging in simulation mode**
         - Do **not** call `_apply_trade_logging` when `settings.mode == "simulation"`; that function is only meaningful in trading mode.

### 4. Edge cases and performance

- **Warm-up behavior**
  - Strategy already has `warmup_bars` and will not open trades before that bar index.
  - The loop still computes indicators for all bars, but:
    - You can optionally start the **strategy** evaluation after `warmup_bars`:
      - For `i < warmup_bars`, skip the call to `compute_order_block_trend_following` to save CPU.
- **Short history**
  - If `len(candles) <= warmup_bars`:
    - Indicators are still computed prefix-wise.
    - Strategy may emit no trades (as designed).
- **Performance considerations**
  - Complexity in simulation mode becomes roughly \(O(N^2)\) in the number of bars within `snapshot_limit` if you always use `window = candles[0 : i+1]`.
  - To mitigate this:
    - Use a fixed `bars_window` and `window = candles[max(0, i - bars_window + 1) : i + 1]` so work per bar is bounded.
    - Ensure you correctly translate local indices (`local_i`) to global context when needed.

### 5. Testing strategy

- **Unit tests**
  - For indicators (`compute_structure`, `compute_order_blocks`, volume profile, S/R):
    - Verify that computing them bar-by-bar with `window = candles[0 : i+1]` and then aggregating gives consistent results with a one-shot computation when restricted to the same prefix.
  - For strategy:
    - On synthetic data, confirm that:
      - Running the bar-by-bar loop and collecting `events_for_bar` matches running the strategy once on each prefix and inspecting only the last bar.
- **Simulation correctness sanity checks**
  - Given a small fixed candle set:
    - Run the old full-window version (for comparison only).
    - Run the new prefix-only version.
    - Show that when you truncate the data at bar `k` and re-run, the decisions up to `k` remain identical (no dependency on future bars).

### 6. Documentation updates

- **BRD updates**
  - In **FR-4 Indicator Engine** and **FR-6 Simulation Tool**:
    - Note that in simulation mode, indicators and strategy are recomputed **per bar** on prefixes ending at the current bar index (no use of future bars for any visuals or decisions).
  - In **Candle Stream** / **Simulation Tool** sections:
    - Clarify that:
      - Trading mode uses full-window indicators and a single strategy call per heartbeat (no future exists).
      - Simulation mode uses a bar-by-bar prefix-only recomputation inside `candle_stream._make_snapshot_payload`.


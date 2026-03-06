# Execution Service & Bybit Integration – Implementation Plan (Spot + Linear v1.1+)

## 1. Goals

- Support both **Spot** and **Linear** trading via a unified Execution Service and `BybitClient`.
- Allow configuring **position size per gateway** (`POSITION_SIZE`).
- Ensure **stop logic** always closes the **actual live position size** on the exchange (not just the initial configured size).
- Keep the Trading Strategy module **pure** (no direct Bybit calls) and reuse the same trade-log / chart formats for simulation and trading modes.

---

## 2. Bybit API Surface (v5)

### 2.1 Common (Spot + Linear)

- **Place order**  
  - `POST /v5/order/create`  
  - Key params:
    - `category`: `"spot"` or `"linear"` (from app-level `market`).
    - `symbol`, `side` (`Buy`/`Sell`), `orderType` (`Market`/`Limit`), `qty`, optional `price`.

- **Cancel order**  
  - `POST /v5/order/cancel`  
  - Params:
    - `category`, `symbol`, and either `orderId` or `orderLinkId`.

- **Open orders (Spot)**  
  - `GET /v5/order/realtime?category=spot&symbol=...`

- **Unified-account wallet balances (Spot + Linear margin)**  
  - `GET /v5/account/wallet-balance?accountType=UNIFIED[&coin=...]`

### 2.2 Linear-specific

- **Current positions**  
  - `GET /v5/position/list?category=linear&symbol=...`
  - Returns size, avg entry, margin, PnL, etc.

- **Trading stop (TP/SL/TS)**  
  - `POST /v5/position/trading-stop`  
  - Params:
    - `symbol`, `category=linear`, `stopLoss`, `takeProfit`, `trailingStop`, `slTriggerBy`, `tpTriggerBy`, etc.

---

## 3. `BybitClient` Extensions

Extend `backend/app/services/bybit_client.py` with authenticated helpers:

- `async def create_order(self, *, category, symbol, side, orderType, qty, price=None, **kwargs) -> dict:`
- `async def cancel_order(self, *, category, symbol, orderId=None, orderLinkId=None) -> dict:`
- `async def get_open_orders(self, *, category, symbol) -> list[dict]:`
- `async def get_wallet_balance(self, *, accountType="UNIFIED", coin: str | None = None) -> dict:`
- `async def get_linear_positions(self, *, symbol: str) -> list[dict]:  # category=linear`
- `async def set_linear_trading_stop(self, *, symbol, **kwargs) -> dict:  # /v5/position/trading-stop`

Implementation details:

- **Authentication (Bybit v5 private REST):**
  - Read `BYBIT_API_KEY`, `BYBIT_API_SECRET`, and optional `BYBIT_RECV_WINDOW` from `Settings` (env-backed).
  - For each private request:
    - Compute `timestamp_ms = int(time.time() * 1000)`.
    - Build the pre-sign string:
      - GET: `timestamp_ms + apiKey + recvWindow + queryString`
      - POST: `timestamp_ms + apiKey + recvWindow + rawRequestBody`
    - Compute `sign = HMAC_SHA256(pre_sign, apiSecret)` and hex-encode.
    - Send headers:
      - `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP`, `X-BAPI-RECV-WINDOW`, `X-BAPI-SIGN`.
- Respect timeouts + retries; surface clean error types for Execution Service.

---

## 4. Execution Service Design

### 4.1 Responsibilities

- Map strategy-level `TradeEvent` → `OrderIntent` → concrete Bybit orders.
- Own all live **order placement / cancellation / stop management**.
- Maintain and update:
  - Trade log (`index.jsonl`, `entry_*.md`).
  - `current.json` (open positions, including `currentStopPrice`).
- Provide reconciliation views (Spot/Linear) for drift detection.

### 4.2 Inputs

- `TradeEvent` from `compute_order_block_trend_following`:
  - `side`: `"long" | "short"`.
  - `price`: entry price (close of bar).
  - `initial_stop_price`.
  - `context`: OB metadata, etc.
- Gateway config:
  - `TRADING_SYMBOL`, `TRADING_INTERVAL`.
  - `MARKET` (`"spot" | "linear"`).
  - `POSITION_SIZE` (float).

---

## 5. Entry Flow (Open Trade)

### 5.1 Determine Qty from `POSITION_SIZE`

- **Spot**:
  - Use `POSITION_SIZE` as base-asset quantity (e.g. `0.01 BTC`).
  - Optionally cap by available base/quote balances from `wallet-balance`.

- **Linear**:
  - Use `POSITION_SIZE` as base/contract qty:
    - Map to `qty` parameter respecting contract `lotSizeFilter` (future enhancement).

### 5.2 Place Order

1. Build `OrderIntent`:
   - `symbol`, `side` (`Buy` for long, `Sell` for short), `orderType="Market"` (v1), `qty`, `context`.
2. Call `BybitClient.create_order(...)` with `category=settings.market`.
3. On success/fill:
   - Append `entry` record via `append_entry(...)` (trade log service).
   - Write into `current.json` via `add_current_trade(...)` with:
     - `tradeId`, `entryTime`, `entryPrice`, `initialStopPrice`, `currentStopPrice`, `side`.

### 5.3 Attach Stop (Linear only, optional)

- For `MARKET=linear`, after entry:
  - Call `set_linear_trading_stop(symbol, stopLoss=initial_stop_price, ...)`.
  - Record that SL is “owned” by exchange; still mirror it into `current.json` for UI.

---

## 6. Stop Handling (Hit + Move)

### 6.1 Stop-Hit (Live trading vs simulation)

In **simulation mode**, stop-hit is determined by the strategy/bar replay logic:

- On each simulated bar:
  1. Check if the bar range touches the effective stop level.
  2. If hit → treat the trade as closed at that bar (simulation-only).

In **live trading mode**, the Execution Service **must not** re-simulate stops from candles. Instead it:

- Places real stop/TP/TS on the exchange (Linear via `position/trading-stop`, Spot via logical/conditional orders if used).
- Periodically or via WebSocket:
  - Checks whether the **stop order or position close** has been reported by Bybit:
    - Spot: position size inferred from balances + orders drops to zero, or specific stop order is filled.
    - Linear: `position/list` shows size reduced to zero, and/or stop order is reported as filled.
- If the stop order was executed:
  - Treat the position as closed, append an `exit` in the trade log, and remove it from `current.json`.
- If the stop order was **not** executed:
  - Treat the position as still open; trailing logic and further stop adjustments continue based on the live exchange state.

### 6.2 Close Position Using Live Size

When a stop (or manual close) is triggered:

- **Spot**:
  1. Query `GET /v5/account/wallet-balance` + `GET /v5/order/realtime?category=spot&symbol=...`.
  2. Compute **effective net Spot position** (base/quote differential minus any pending opposite orders).
  3. If net size ≈ 0 → nothing to close. Else:
     - Send `POST /v5/order/create` with opposite side and `qty = live_size`.

- **Linear**:
  1. Query `GET /v5/position/list?category=linear&symbol=...`.
  2. Extract current position size and direction.
  3. If size ≈ 0 → nothing to close. Else:
     - Send `POST /v5/order/create` with opposite side and `qty = live_size`.

After the close:

- Append `exit` via `append_exit(...)` with close reason `"stop"` / `"manual"`.
- Remove from `current.json` via `remove_current_trade(...)`.

### 6.3 Trailing / Moving Stop

- **Strategy**:
  - Calculates new stop price per bar and emits stop segments (already implemented).
- **Trading mode wiring** (already partially implemented in `candle_stream._apply_trade_logging`):
  - For each new stop segment on the current bar:
    - Log `stop_move` via `append_stop_move(...)`.
    - Update `current.json`’s `currentStopPrice`.
    - Before touching any on-exchange stop:
      - **Re-query the live position size** to account for partial fills, manual adjustments, or margin changes:
        - Spot: recompute effective exposure from wallet balances + open Spot orders.
        - Linear: use `GET /v5/position/list?category=linear&symbol=...` as the source of truth.
      - If the live size differs from the original `POSITION_SIZE`, adjust the **stop order size** accordingly so the stop still covers the full remaining position.
    - **Linear only:** call `set_linear_trading_stop` with updated `stopLoss` **and, where applicable, size parameters (e.g. `slSize`)** so that both price and quantity of the SL/TS reflect the current live position.

---

## 7. Reconciliation Plan

Run periodically and on gateway startup:

- Load `current.json` to get expected open trades.

- **Spot**:
  - Fetch `wallet-balance` and `order/realtime?category=spot`.
  - Reconstruct net Spot exposure per symbol and compare to `current.json`.

- **Linear**:
  - Fetch `position/list?category=linear&symbol=...`.
  - Compare reported position size/avg entry to `current.json`.

On mismatch:

- Log a reconciliation warning with both views.
- Optionally:
  - Autoclose orphaned local trades with reason `"reconciled"`.
  - Or mark them as “stale” for operator attention.

---

## 8. Configuration Summary (Trading Gateway)

- `MARKET`: `"spot"` or `"linear"` — drives `category` for all Bybit calls.
- `POSITION_SIZE`: float — default entry size per signal:
  - Spot: base quantity.
  - Linear: contract/base qty.
- `TRADING_SYMBOL`, `TRADING_INTERVAL`, `BARS_WINDOW`, `FETCH_INTERVAL_SEC`, `TRADE_LOG_DIR` — as already documented.

---

## 9. Milestones

1. **BybitClient auth & helpers**
   - Implement and unit-test `create_order`, `cancel_order`, `get_wallet_balance`, `get_open_orders`, `get_linear_positions`, `set_linear_trading_stop`.
2. **Execution Service skeleton**
   - Define `OrderIntent` → concrete Bybit calls; wire to trade-log and `current.json`.
3. **Stop-hit wiring (trading mode)**
   - Map strategy stop hits to exit intents and close positions using live size.
4. **Linear trading-stop support**
   - Mirror strategy trailing into `position/trading-stop` for Linear.
5. **Reconciliation jobs**
   - Implement periodic Spot/Linear reconciliation and basic alerting/logging.

---

## 10. Integration Points with `order_block_trend_following`

The Trading Strategy module (`backend/app/services/trading_strategy/order_block_trend_following.py`) exposes **two primary integration points** that the Execution Service must consume:

1. **Entry / reversal events (`TradeEvent`)**  
   - **Where in code:** inside `_open_from_candidate(...)` in `compute_order_block_trend_following`.  
   - **What it emits:** a `TradeEvent` with:
     - `type`: `"OB_TREND_BUY"` or `"OB_TREND_SELL"`.
     - `side`: `"long"` or `"short"`.
     - `time`: close time of the entry bar (Unix seconds).
     - `bar_index`: index of the entry bar.
     - `price`: entry price (close of bar).
     - `initial_stop_price`: strategy-computed initial SL.
     - `context`: OB metadata (`ob_top`, `ob_bottom`, `trigger`, `reversal_from`, etc.).
   - **Execution Service hook:**
     - Treat each `TradeEvent` whose `bar_index` is the **current bar** as a **new entry intent**.
     - Derive `qty` from `POSITION_SIZE` + risk rules.
     - Call `BybitClient.create_order(...)` with appropriate `category`, `symbol`, `side`, `orderType`, and `qty`.
     - On success, record the trade via `append_entry(...)` and `add_current_trade(...)`.

2. **Trailing stop moves (`StopSegment`)**  
   - **Where in code:** at the end of the main loop in `compute_order_block_trend_following`, in the block labeled:
     - `# --- Trailing stop for active position (define stop level for next bar) ---`
   - **What it emits:** `StopSegment` objects appended to `stop_segments`:
     - `start_time`, `end_time`, `price`, `side` for each new stop level.
   - **Execution Service hook:**
     - On each heartbeat, inspect **new** stop segments that end on the current bar.
     - For each segment associated with a logged trade:
       - Log a `stop_move` via `append_stop_move(...)`.
       - Update `current.json`’s `currentStopPrice`.
       - **Live trading:** re-query live position size (Spot: balances + Spot orders; Linear: `position/list`) and:
         - For Spot, update the local “logical stop” level; actual closure still uses opposite-side market order on stop-hit.
         - For Linear, call `set_linear_trading_stop` with updated `stopLoss` (and, as needed, size parameters such as `slSize`) so the exchange-level SL/TS matches the strategy’s trailing decision.

In practice, CandleStream’s `_apply_trade_logging(...)` already sits at this integration boundary: it receives the `trade_events` and `stop_segments` produced by `compute_order_block_trend_following`. The Execution Service should be invoked from this layer (or directly downstream of it) so that **every new `TradeEvent` and stop move** produced by the strategy results in a corresponding **order/position action** on Bybit in trading mode.


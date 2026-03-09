# Execution Service & Bybit Integration – Implementation Plan (Spot + Linear v1.1+)

## 1. Goals

- Support both **Spot** and **Linear** trading via a unified Execution Service and `BybitClient`.
- Allow configuring **position size per gateway** (`POSITION_SIZE`).
- Ensure **stop logic** always closes the **actual live position size** on the exchange (not just the initial configured size).
- Keep the Trading Strategy module **pure** (no direct Bybit calls) and reuse the same trade-log / chart formats for simulation and trading modes.
- Provide a **manual mode** with curl-callable FastAPI endpoints to test each execution method against Bybit before wiring the strategy.

---

## 2. Bybit API Surface (v5)

### 2.1 Common (Spot + Linear)

- **Place order**  
  - `POST /v5/order/create`  
  - Key params:
    - `category`: `"spot"` or `"linear"` (from app-level `market`).
    - `symbol`, `side` (`Buy`/`Sell`), `orderType` (`Market`/`Limit`), `qty`, optional `price`.
    - For **market entry**: `slippageToleranceType="Percent"`, `slippageTolerance="0.01"` (0.01%).
    - Optional `stopLoss` (and `takeProfit`) in the same request so the exchange attaches them when the order fills.

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

### 4.1 Architecture Overview

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                  Execution Service                        │
                    │  ┌─────────────────┐    ┌─────────────────────────────┐
                    │  │  Manual mode    │    │  Strategy-driven mode         │
                    │  │  (test only)    │    │  (live trading)                │
                    │  │                 │    │                                │
                    │  │  FastAPI        │    │  CandleStream / trade_log      │
                    │  │  /api/v1/exec/  │    │  _apply_trade_logging          │
                    │  │  * endpoints    │    │  → entry / stop_move / exit    │
                    │  └────────┬────────┘    └───────────────┬─────────────────┘
                    │           │                            │
                    │           └────────────┬───────────────┘
                    │                        ▼
                    │           ┌─────────────────────────────┐
                    │           │  BybitClient (auth REST)    │
                    │           │  create_order, cancel,     │
                    │           │  get_* , set_trading_stop  │
                    │           └──────────────┬──────────────┘
                    └─────────────────────────│────────────────────────────────┘
                                              ▼
                                    Bybit API (v5)
```

- **Manual mode:** Execution methods are exposed as **FastAPI endpoints** under a dedicated prefix (e.g. `/api/v1/exec/`). No strategy or trade log is involved; each endpoint calls the corresponding `BybitClient` method. Used to verify behaviour on Bybit (orders, positions, stops) via `curl` before enabling strategy-driven execution.
- **Strategy-driven mode:** Same `BybitClient` methods are invoked from the trading pipeline (e.g. from `candle_stream._apply_trade_logging` or a dedicated Execution Service layer) when `mode=trading`: on `TradeEvent` → place order + log entry; on `StopSegment` → update stop + log stop_move; on stop-hit → close position + log exit.

Both modes share the same `BybitClient` implementation and config (`BYBIT_API_KEY`, `BYBIT_API_SECRET`, `MARKET`, etc.).

### 4.2 Responsibilities and ownership (trading mode)

- **Executor module** owns:
  - All live **order placement / cancellation / stop management** (via `BybitClient`).
  - **`current.json`** — single source of truth for open position state (entry price, size, current stop). Updated by the executor: on entry (after fill), on **trailing stop move** (via `update_stop()`), and on **stop hit** (trade removed).
  - **`index.jsonl`** — trade log index (entry/stop_move/exit lines). Written by the executor when it confirms fills, when it applies a stop move (`update_stop`), and when it registers a stop hit (exit).
- **Strategy module** (trading mode):
  - Sends **entry intent** to the executor (do not treat entry as performed until executor confirms).
  - Emits **stop segments** (trailing stop levels); the **executor** is invoked to apply them via `update_stop()` (writes `current.json` + `stop_move` in index). Strategy does **not** write `current.json` or `index.jsonl`.
  - Owns **`entry_*.md`** only — logs the orders/signals it issued (for audit and strategy-level notes).
  - **Stop-hit** is **not** detected by the strategy; it is detected by the executor at the start of each heartbeat (see § 6.1 and § 10.4).
- Expose **manual test endpoints** (see § 12) for each execution primitive so effects can be verified on Bybit with `curl`.
- Provide reconciliation views (Spot/Linear) for drift detection.

### 4.3 Inputs (Strategy-driven mode)

- `TradeEvent` from `compute_order_block_trend_following`:
  - `side`: `"long" | "short"`.
  - `price`: entry price (close of bar).
  - `initial_stop_price`.
  - `context`: OB metadata, etc.
- Gateway config:
  - `TRADING_SYMBOL`, `TRADING_INTERVAL`.
  - `MARKET` (`"spot" | "linear"`).
  - `POSITION_SIZE` — order qty (executor always uses this; no runtime capping).
  - `LEVERAGE` — linear only, e.g. 10 for 10x (default 10); executor calls `set_linear_leverage` for the symbol.

---

## 5. Entry Flow (Open Trade)

### 5.1 Determine qty from `POSITION_SIZE`

- The executor **always** uses the qty from the initial gateway settings: **`POSITION_SIZE`** (env, set per run in `run-dev-trading.sh`). No runtime capping or recomputation from balances.
- **Spot**: `POSITION_SIZE` = base-asset quantity (e.g. `0.01 BTC`).
- **Linear**: `POSITION_SIZE` = base/contract qty for the order.

### 5.2 Place entry order (trading mode)

1. **Order type and slippage**
   - Use **market order** with Bybit slippage protection: `slippageToleranceType="Percent"`, `slippageTolerance="0.01"` (0.01%).
2. **Initial stop in same transaction**
   - Include **`stopLoss`** (strategy’s initial stop price) in the **same** `POST /v5/order/create` request so that when the order fills, the exchange attaches the stop to the position immediately. No separate call to set initial stop after fill.
3. **Executor behaviour after sending the order**
   - Executor **registers** that it has received the entry order (e.g. stores pending order id / orderLinkId).
   - Returns to the strategy a signal **“order received, no entry yet”**.
   - **Strategy does not update `current.json`** at this point; it does not consider the entry as performed.
4. **Executor owns `current.json` and `index.jsonl`**
   - On the **next heartbeat** (or when the executor detects fill via position/order state):
     - Executor reads **position** (and optionally order fill) from the exchange.
     - If position is open: executor writes/updates **`current.json`** with **entry price**, **size** (new variable from exchange), `initialStopPrice`, `currentStopPrice`, `side`, `tradeId`, etc.
     - Executor appends **`entry`** to **`index.jsonl`** when it confirms the entry from the exchange.
   - Strategy **reads `current.json`** on each heartbeat to see whether the position was opened and the stop is in place; it does not write `current.json`.

### 5.3 Strategy vs executor (trading vs simulation)

- **Simulation mode**: Unchanged. Strategy receives current price from the stream and captures entry; strategy/stream own the in-memory state and any log writes as today.
- **Trading mode**:
  1. Strategy emits entry intent (e.g. `TradeEvent`) and sends it to the executor.
  2. Executor places market order (slippage 0.01%) with `stopLoss` in the same request → confirms “order received”, returns “no entry yet”.
  3. Strategy does **not** update `current.json`; it may log the intent in **`entry_*.md`** (strategy-owned).
  4. On next heartbeat: executor updates `current.json` and `index.jsonl` from exchange state; strategy reads `current.json` to see if position is open and stop is in place.

---

## 6. Stop Handling (Hit + Move)

### 6.1 Stop-Hit (Live trading vs simulation/dry-run)

In **simulation mode**, stop-hit is determined by the strategy/bar replay logic:

- On each simulated bar:
  1. Check if the bar range touches the effective stop level.
  2. If hit → treat the trade as closed at that bar (simulation-only).

In **live trading mode**, stop-hit is detected **only in the executor**, at the **start of each heartbeat** (when syncing from the exchange):

- The executor calls `get_linear_positions(symbol)` (or Spot equivalent). If **position size is 0** for the symbol but **`current.json`** still has open trade(s) for that symbol, the executor treats this as **stop hit** (position was closed by the exchange, e.g. by the attached stop).
- The executor then:
  1. **Cancels any open orders** for the active symbol (e.g. leftover SL/TP orders) via `get_open_orders` and `cancel_order`.
  2. For each trade in `current.json` for that symbol: **appends an `exit`** to `index.jsonl` (close_price = last stop, close_reason = `"stop"`) and **removes the trade from `current.json`** via `remove_current_trade(...)`.
  3. Logs to console (e.g. `Executor: stop hit detected (position size 0), registering exit(s) and updating current.json` and per-trade `Executor: stop hit trade_id=... close_price=... position closed`).
- The strategy does **not** detect or write stop-hit; it only reads `current.json` after the executor has updated it.

In **executor dry-run mode** (`EXECUTOR_DRY_RUN=true`), we still use the **same executor-owned stop-hit path**, but we simulate the “position size” from local state instead of querying the exchange:

- The executor fetches the **latest candle** for the trading symbol/interval and computes its `high`/`low`.
- It then builds a **fake Bybit-like positions list** from `current.json` and removes any trade whose effective stop would have been touched by the bar range:
  - **Long:** bar low ≤ stop price.
  - **Short:** bar high ≥ stop price.
- This produces a synthetic “position size” of 0 when the bar hits the stop, while `current.json` still contains the open trade(s).
- The normal stop-hit branch above then runs unchanged: cancel open orders (live only), append `exit` to `index.jsonl` with `close_reason="stop"`, remove the trade(s) from `current.json`, and log per-trade `Executor: stop hit trade_id=... close_price=... position closed`.

This means **dry-run trading mode exercises the full executor stop-hit pipeline** (including trade-log writes and `current.json` updates) without requiring a real position on the exchange.

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

- **Strategy**: Calculates a new stop price per bar (see strategy docs) and emits stop segments. It does **not** write `current.json` or `index.jsonl`.
- **Trading mode (all markets; linear vs spot differs only in exchange calls)**:
  - The **executor** exposes **`update_stop(symbol, interval, trade_id, new_stop_price, side, end_time, client)`**. The candle stream calls it when the strategy selects the effective stop segment for the current bar for an open trade.
  - The executor **always overwrites** the trade’s `currentStopPrice` in `current.json` with `new_stop_price` whenever that price changes, regardless of whether it is tighter or looser than the previous stop. Any “never move stop against the trade” policy must be enforced inside the strategy itself.
  - **Dry run:** Executor logs (e.g. `Executor: [DRY RUN] stop moved trade_id=... new_stop=... side=...`), then updates `current.json` via `update_current_trade_stop(...)` and appends `stop_move` via `append_stop_move(...)`. No Bybit call is made.
  - **Live + Linear market:** In addition to updating `current.json` and appending `stop_move`, executor calls **`set_linear_trading_stop(symbol, stopLoss=new_stop_price)`** for the whole position. No partial size; the exchange applies the new stop to the entire position.
  - **Live + Spot market:** Stops are tracked **locally only**; there is no native trailing-stop call. The executor still updates `current.json` and appends `stop_move` exactly as in dry run, but skips any Bybit stop-management call.
  - At most one `stop_move` is written per bar per trade; repeated strategy segments that resolve to the same `new_stop_price` for that trade and bar are ignored.
  - All writes to `current.json` and `index.jsonl` for stop moves are done **only** in the executor; the strategy never writes them.

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
- `POSITION_SIZE`: order qty — executor always uses this (Spot: base qty; Linear: contract/base qty). Set per run in `run-dev-trading.sh`.
- `LEVERAGE`: linear only — e.g. 10 for 10x (default 10). Executor calls `set_linear_leverage(symbol, buyLeverage=LEVERAGE)` for the trading symbol (e.g. before first entry).
- `TRADING_SYMBOL`, `TRADING_INTERVAL`, `BARS_WINDOW`, `FETCH_INTERVAL_SEC`, `TRADE_LOG_DIR` — as already documented.

---

## 9. Milestones

1. **BybitClient auth & helpers**
   - Implement and unit-test `create_order`, `cancel_order`, `get_wallet_balance`, `get_open_orders`, `get_linear_positions`, `set_linear_trading_stop`.
2. **Manual mode & test endpoints**
   - Add FastAPI router `/api/v1/exec` with one endpoint per Bybit operation (§ 12). Verify each with `curl` against Bybit (use testnet/small size). No trade log or strategy involved.
3. **Execution Service skeleton**
   - Define `OrderIntent` → concrete Bybit calls; wire to trade-log and `current.json` (strategy-driven mode).
4. **Stop-hit wiring (trading mode)**
   - Map strategy stop hits to exit intents and close positions using live size.
5. **Linear trading-stop support**
   - Mirror strategy trailing into `position/trading-stop` for Linear.
6. **Reconciliation jobs**
   - Implement periodic Spot/Linear reconciliation and basic alerting/logging.

---

## 10. Step-by-step implementation

Concrete implementation steps, in order. Complete each milestone’s steps before moving to the next.

### 10.1 Milestone 1: BybitClient auth & helpers — **DONE**

**Config & private REST**

1. ~~**Add Bybit env to Settings**~~ **DONE** — `bybit_api_key`, `bybit_api_secret`, `bybit_recv_window`, `bybit_rest_base_url` in `config.py`; server time used for signing.
2. ~~**Implement private request signing**~~ **DONE** — `_sign_request` in `bybit_client.py`.
3. ~~**Add generic private REST call**~~ **DONE** — `_request` in `BybitClient`.

**BybitClient methods**

4. ~~**create_order**~~ **DONE** — with `**kwargs` (e.g. `stopLoss`, `takeProfit`, `slippageTolerance`, `slippageToleranceType`).
5. ~~**cancel_order**~~ **DONE**
6. ~~**get_open_orders**~~ **DONE**
7. ~~**get_wallet_balance**~~ **DONE**
8. ~~**get_linear_positions**~~ **DONE**
9. ~~**set_linear_trading_stop**~~ **DONE**
10. ~~**set_linear_leverage**~~ **DONE** (added for manual mode).
11. Unit tests — optional; not yet added.

---

### 10.2 Milestone 2: Manual mode & test endpoints — **DONE**

1. ~~**Create exec router**~~ **DONE** — `backend/app/api/exec.py`, prefix `/api/v1/exec`.
2. ~~**BybitClient dependency**~~ **DONE** — `get_bybit_client()`.
3. ~~**POST /order**~~ **DONE** — supports `stopLoss`/`takeProfit`/`tpslMode` on order; optional `marketUnit` for spot.
4. ~~**POST /order/cancel**~~ **DONE**
5. ~~**GET /orders**~~ **DONE**
6. ~~**GET /wallet-balance**~~ **DONE**
7. ~~**GET /positions**~~ **DONE**
8. ~~**POST /positions/trading-stop**~~ **DONE**
9. ~~**POST /positions/set-leverage**~~ **DONE**
10. ~~**POST /positions/close**~~ **DONE**
11. ~~**Mount router**~~ **DONE** — in `main.py`.
12. Verify with curl — use `test.sh`; ongoing.

---

### 10.3 Milestone 3: Execution Service skeleton (executor-owned state)

1. **Define entry intent and executor response types**  
   - Entry intent: symbol, side, qty (from `POSITION_SIZE`), `initial_stop_price`, context (e.g. trade_id, bar time).  
   - Executor response: e.g. `{ "order_received": true, "entry_yet": false, "orderId"?: string }`; later extend with `entry_yet: true` and entry price/size when filled.

2. **Entry: market order + initial stop in one request**  
   - **Linear only:** Before placing the first entry order (or on heartbeat when no position yet), executor calls `set_linear_leverage(symbol, buyLeverage=settings.leverage)` so the symbol uses the configured leverage (e.g. 10x from `LEVERAGE` env).
   - On entry intent, executor uses **qty from `POSITION_SIZE`** only (no capping from balances). Call `create_order` with:
     - `orderType="Market"`, `slippageToleranceType="Percent"`, `slippageTolerance="0.01"` (0.01%).
     - `stopLoss=str(initial_stop_price)` in the **same** request so the exchange attaches the stop when the order fills.
   - Do **not** call `append_entry` or `add_current_trade` at this point; only register “order received” (e.g. store orderId/orderLinkId) and return “no entry yet” to the strategy.

3. **Executor owns `current.json` and `index.jsonl`**  
   - Move (or restrict) writes to `current.json` and `index.jsonl` to the **executor module** only.  
   - On each **heartbeat** (or when executor checks exchange state):
     - Query `get_linear_positions(symbol)` (and open orders if needed) to see if the pending entry order has filled and a position exists.
     - If position is open and not yet in `current.json`: executor writes `current.json` with **entry price**, **size** (from exchange), `initialStopPrice`, `currentStopPrice`, `side`, `tradeId`, etc., and appends `entry` to `index.jsonl`.
   - Strategy never writes `current.json` or `index.jsonl` in trading mode.

4. **Strategy: send intent, read state, own `entry_*.md` only**  
   - Strategy sends entry intent to executor; on response “order received, no entry yet”, strategy does **not** update `current.json`.  
   - Strategy continues to write **`entry_*.md`** (or equivalent) to log the orders/signals it issued.  
   - On each heartbeat, strategy **reads** `current.json` to see if position was opened and stop is in place; use that to drive in-memory state (e.g. “we have an open position”) and optional UI.

5. **Wire into CandleStream / heartbeat**  
   - In trading mode: when strategy emits a new entry (TradeEvent), call executor’s entry handler; executor places order (market + stopLoss, slippage 0.01%) and returns “order received, no entry yet”.  
   - Heartbeat loop: executor updates `current.json` and `index.jsonl` from exchange; strategy (or stream) reads `current.json` and exposes it to strategy for “do we have a position?” and “entry price / size”.

---

### 10.4 Milestone 4: Stop-hit wiring (trading mode)

1. **Detect stop hit (Linear)**  
   - In the executor, at the **start of each heartbeat** (inside `sync_from_exchange`): call `get_linear_positions(symbol)`.  
   - If **position size is 0** for the symbol but `current.json` still has open trade(s) for that symbol → **stop hit** (position was closed by the exchange).  
   - Executor then: (1) **Cancel any open orders** for the symbol (`get_open_orders` → `cancel_order` per order) to clear leftover SL/TP orders. (2) For each trade in `current.json`: append `exit` to `index.jsonl` and **remove the trade from `current.json`** via `remove_current_trade(...)`. (3) Log (e.g. `Executor: stop hit detected (position size 0), registering exit(s) and updating current.json` and per-trade `Executor: stop hit trade_id=... close_price=... position closed`).

2. **Detect stop hit (Spot)**  
   - Query wallet balance + open orders; infer net position. If net position is 0 but `current.json` has an open trade → treat as closed; executor cancels open orders for symbol if needed, appends exit, removes from `current.json`, and logs.

3. **Close position helper (executor)**  
   - As in § 6.2: Linear: `get_linear_positions` → if size > 0, `create_order` opposite side, qty = live size. Spot: compute live size → opposite market order.  
   - After successful close: executor calls `append_exit(...)` and `remove_current_trade(...)` (executor owns both `index.jsonl` and `current.json`).

4. **Stop-hit is executor-only**  
   - Detection and all writes (`index.jsonl` exit, `current.json` removal) are done in the executor. The strategy does not detect or write stop-hit.

---

### 10.5 Milestone 5: Linear trailing stop (trading-stop for full position)

1. **Trailing stop via `POST /v5/position/trading-stop`**  
   - When strategy emits a new stop segment (trailing move), executor:
     - Logs `stop_move` via `append_stop_move(...)` and updates `current.json`’s `currentStopPrice`.
     - Calls **`set_linear_trading_stop(symbol, stopLoss=new_stop_price)`** for the **whole position** (no partial size; Bybit applies the new stop to the full position).  
   - Same `BybitClient` as entry/close. Reference: § 12.1.6 and `test.sh` (positions/trading-stop).

2. **Handle Bybit errors**  
   - If `set_linear_trading_stop` fails (e.g. position closed externally), executor logs and on next heartbeat can remove trade from `current.json` or mark for reconciliation.

---

### 10.6 Milestone 6: Reconciliation jobs

1. **Reconciliation function**  
   - File: e.g. `backend/app/services/reconciliation.py`.  
   - Input: symbol, interval (or load all from `current.json`).  
   - Load `current.json` → expected open trades.  
   - Spot: fetch wallet balance + open orders; compute net position per symbol.  
   - Linear: fetch `position/list` for symbol(s).  
   - Compare: for each trade in `current.json`, check if exchange has a matching open position (by symbol/side/size).  
   - On mismatch: log warning with both views; optionally append exit with reason `"reconciled"` and remove from `current.json`, or set a “stale” flag.

2. **Schedule or trigger**  
   - Call reconciliation on gateway startup (after loading `current.json`).  
   - Optionally: run periodically (e.g. every 1–5 minutes) via background task or scheduler.  
   - Wire into the same process that runs the trading gateway (e.g. startup event or cron).

3. **Logging / alerting**  
   - Log reconciliation result (OK vs mismatch).  
   - Optional: emit metric or alert when mismatch is detected.

---

## 11. Integration Points with `order_block_trend_following`

The Trading Strategy module (`backend/app/services/trading_strategy/order_block_trend_following.py`) exposes **two primary integration points** that the Execution Service (executor) consumes. In **trading mode**, the executor owns `current.json` and `index.jsonl`; the strategy does not treat entry as performed until the executor has confirmed it (via `current.json`).

1. **Entry / reversal events (`TradeEvent`)**  
   - **Where in code:** inside `_open_from_candidate(...)` in `compute_order_block_trend_following`.  
   - **What it emits:** a `TradeEvent` with `side`, `time`, `bar_index`, `price`, `initial_stop_price`, `context`.  
   - **Executor hook (trading mode):**
     - Treat each `TradeEvent` on the **current bar** as a **new entry intent**.
     - Place **market order** with `slippageToleranceType="Percent"`, `slippageTolerance="0.01"`, and **`stopLoss=initial_stop_price`** in the **same** request.
     - Return to strategy **“order received, no entry yet”**; strategy does **not** update `current.json`.
     - Strategy may log the intent in **`entry_*.md`** (strategy-owned).
     - On **next heartbeat**, executor updates `current.json` (and `index.jsonl`) from exchange state; strategy **reads** `current.json` to get **entry price** and **size** and to see if position is open and stop is in place.
   - **Simulation mode:** unchanged; strategy receives current price and captures entry as today.

2. **Trailing stop moves (`StopSegment`)**  
   - **Where in code:** block `# --- Trailing stop for active position (define stop level for next bar) ---` in `compute_order_block_trend_following`.  
   - **What it emits:** `StopSegment` objects with `start_time`, `end_time`, `price`, `side`.  
   - **Executor hook (trading mode):**
     - On each heartbeat, for **new** stop segments that end on the current bar and correspond to an open trade in `current.json`:
       - Executor logs `stop_move` via `append_stop_move(...)` and updates `current.json`’s `currentStopPrice`.
       - **Linear:** call **`set_linear_trading_stop(symbol, stopLoss=new_stop_price)`** for the **whole position** (see § 12.1.6 / `test.sh`).

CandleStream’s `_apply_trade_logging(...)` sits at this boundary: it receives `trade_events` and `stop_segments` from the strategy. The executor is invoked from this layer in trading mode; the strategy reads `current.json` (written only by the executor) to know actual entry price, size, and whether the position and stop are in place.

---

## 12. Manual mode and test endpoints

Manual mode allows testing each execution primitive against Bybit **without** the strategy or trade log. Every Bybit-backed operation used by the Execution Service is exposed as a FastAPI endpoint under a dedicated prefix (e.g. `POST/GET /api/v1/exec/...`). These can be called with `curl` to verify behaviour on the exchange before wiring the strategy.

**Design principles:**

- Endpoints are **thin**: validate request, call the corresponding `BybitClient` method, return the API response (or a normalized wrapper) with status.
- No trade log or `current.json` is written in manual mode.
- Request bodies use the same concepts as the Execution Service (symbol, side, qty, category/market, etc.) so that manual calls mirror what the strategy-driven flow will do.
- All endpoints require the trading gateway to be configured (e.g. base URL and Bybit credentials in env); they do **not** require `mode=trading` or an active strategy run.

### 12.1 Endpoint list and curl examples

Base URL is the trading gateway (e.g. `http://localhost:9001`). Assume `category` is derived from gateway/config (`market`: spot or linear) or passed explicitly where the endpoint supports both.

| Method / action              | HTTP   | Path (example)                    | Purpose |
|-----------------------------|--------|-----------------------------------|--------|
| Place order                 | POST   | `/api/v1/exec/order`              | Create market/limit order (spot or linear). |
| Cancel order                | POST   | `/api/v1/exec/order/cancel`       | Cancel by `orderId` or `orderLinkId`. |
| Get open orders             | GET    | `/api/v1/exec/orders`             | List open orders for a symbol (spot or linear). |
| Wallet balance              | GET    | `/api/v1/exec/wallet-balance`     | Unified wallet balance (optional coin filter). |
| Linear positions            | GET    | `/api/v1/exec/positions`           | Current linear positions for symbol(s). |
| Set linear trading stop     | POST   | `/api/v1/exec/positions/trading-stop` | Set SL/TP/TS on a linear position. |
| Close position (composite)  | POST   | `/api/v1/exec/positions/close`    | Query live size and send closing market order (linear or spot). |

#### 12.1.1 Place order

**Request:** `POST /api/v1/exec/order`

Body (JSON): `symbol`, `side` (`Buy`/`Sell`), `orderType` (`Market`/`Limit`), `qty`, optional `price`, optional `category`.

**curl (market buy, linear):**
```bash
curl -s -X POST "http://localhost:9001/api/v1/exec/order" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","side":"Buy","orderType":"Market","qty":"0.001","category":"linear"}'
```

**curl (spot market sell):**
```bash
curl -s -X POST "http://localhost:9001/api/v1/exec/order" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","side":"Sell","orderType":"Market","qty":"0.001","category":"spot"}'
```

#### 12.1.2 Cancel order

**Request:** `POST /api/v1/exec/order/cancel`  
Body: `symbol`, `category`, and either `orderId` or `orderLinkId`.

**curl:**
```bash
curl -s -X POST "http://localhost:9001/api/v1/exec/order/cancel" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","category":"linear","orderId":"1234567890"}'
```

#### 12.1.3 Get open orders

**Request:** `GET /api/v1/exec/orders?symbol=BTCUSDT&category=linear`

**curl:**
```bash
curl -s "http://localhost:9001/api/v1/exec/orders?symbol=BTCUSDT&category=linear"
```

#### 12.1.4 Wallet balance

**Request:** `GET /api/v1/exec/wallet-balance?accountType=UNIFIED` (optional `&coin=BTC`).

**curl:**
```bash
curl -s "http://localhost:9001/api/v1/exec/wallet-balance?accountType=UNIFIED"
```

#### 12.1.5 Linear positions

**Request:** `GET /api/v1/exec/positions?symbol=BTCUSDT`

**curl:**
```bash
curl -s "http://localhost:9001/api/v1/exec/positions?symbol=BTCUSDT"
```

#### 12.1.6 Set linear trading stop

**Request:** `POST /api/v1/exec/positions/trading-stop`  
Body: `symbol`, optional `stopLoss`, `takeProfit`, `trailingStop`, `slTriggerBy`, `tpTriggerBy`.

**curl (set stop-loss only):**
```bash
curl -s -X POST "http://localhost:9001/api/v1/exec/positions/trading-stop" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","stopLoss":65000.0}'
```

#### 12.1.7 Close position (composite)

**Request:** `POST /api/v1/exec/positions/close`  
Body: `symbol`, `category`. Behaviour: same as § 6.2 (query live size, place opposite market order). No trade log in manual mode.

**curl:**
```bash
curl -s -X POST "http://localhost:9001/api/v1/exec/positions/close" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","category":"linear"}'
```

### 12.2 Implementation notes

- **Router:** Dedicated FastAPI router for `/api/v1/exec`; resolve `BybitClient` and config from app state/env.
- **Security:** Restrict manual exec endpoints in production (e.g. localhost or internal auth); curl from same machine for local testing.
- **Safety:** Place order and close position are not idempotent; use small size and testnet when available.
- **Order:** Implement `BybitClient` first, then manual endpoints and verify with curl, then wire strategy-driven execution.


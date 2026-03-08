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

### 4.2 Responsibilities

- Map strategy-level `TradeEvent` → `OrderIntent` → concrete Bybit orders (strategy-driven mode).
- Own all live **order placement / cancellation / stop management** (via `BybitClient`).
- Maintain and update (strategy-driven mode only):
  - Trade log (`index.jsonl`, `entry_*.md`).
  - `current.json` (open positions, including `currentStopPrice`).
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

### 10.1 Milestone 1: BybitClient auth & helpers

**Config & private REST**

1. **Add Bybit env to Settings**  
   - File: `backend/app/config.py` (or `backend/app/core/config.py` if present).  
   - Add: `bybit_api_key: str = ""`, `bybit_api_secret: str = ""`, `bybit_recv_window: int = 5000`, `bybit_base_url: str = "https://api.bybit.com"` (or testnet URL).  
   - Load from env: `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `BYBIT_RECV_WINDOW`, `BYBIT_BASE_URL`.

2. **Implement private request signing**  
   - File: `backend/app/services/bybit_client.py`.  
   - Add helper: `_sign_request(method, path, query_string, body) -> dict` that returns headers `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP`, `X-BAPI-RECV-WINDOW`, `X-BAPI-SIGN`.  
   - Pre-sign string: GET = `timestamp + apiKey + recvWindow + queryString`; POST = `timestamp + apiKey + recvWindow + body`.  
   - Sign with HMAC-SHA256(secret, pre_sign), hex-encode.

3. **Add generic private REST call**  
   - In `BybitClient`: `async def _request(self, method, path, params=None, json_body=None) -> dict`.  
   - Build full URL from `settings.bybit_base_url` + path; add query for GET.  
   - Call `_sign_request`, set headers, use httpx (or aiohttp) to send request.  
   - Parse JSON response; raise on HTTP error or Bybit `retCode != 0`.

**BybitClient methods (one step per method)**

4. **create_order**  
   - `POST /v5/order/create`.  
   - Params: `category`, `symbol`, `side`, `orderType`, `qty`, optional `price`, pass-through `**kwargs`.  
   - Return full response or extract `result`.

5. **cancel_order**  
   - `POST /v5/order/cancel`.  
   - Params: `category`, `symbol`, and either `orderId` or `orderLinkId`.

6. **get_open_orders**  
   - `GET /v5/order/realtime`.  
   - Query: `category`, `symbol`.  
   - Return `result.list` (or equivalent) as `list[dict]`.

7. **get_wallet_balance**  
   - `GET /v5/account/wallet-balance`.  
   - Query: `accountType` (default `UNIFIED`), optional `coin`.  
   - Return full `result` or normalized balance dict.

8. **get_linear_positions**  
   - `GET /v5/position/list`.  
   - Query: `category=linear`, `symbol`.  
   - Return list of position objects.

9. **set_linear_trading_stop**  
   - `POST /v5/position/trading-stop`.  
   - Body: `symbol`, `category=linear`, optional `stopLoss`, `takeProfit`, `trailingStop`, `slTriggerBy`, `tpTriggerBy`.  
   - Return Bybit response.

10. **Unit tests (optional but recommended)**  
    - File: e.g. `backend/tests/services/test_bybit_client.py`.  
    - Mock HTTP; test signing and parameter building for each method.  
    - If testnet available: one integration test with small order/cancel.

---

### 10.2 Milestone 2: Manual mode & test endpoints

1. **Create exec router module**  
   - File: `backend/app/api/exec.py` (or `backend/app/routers/exec.py`).  
   - Define FastAPI `APIRouter(prefix="/api/v1/exec", tags=["exec"])`.

2. **BybitClient dependency**  
   - In the same app that runs the trading gateway, ensure `BybitClient` is instantiated from settings (api_key, api_secret, base_url) and injectable (e.g. `Depends(get_bybit_client)`).  
   - Add `get_bybit_client()` that reads config and returns a `BybitClient` instance (create once per app or per request as needed).

3. **POST /order**  
   - Path: `/order` (under prefix).  
   - Pydantic body: `symbol`, `side`, `orderType`, `qty`, `price` (optional), `category` (optional).  
   - Resolve `category` from body or default from config (`market`).  
   - Call `bybit_client.create_order(...)`.  
   - Return `{"ok": True, "result": ...}` or HTTPException with Bybit error.

4. **POST /order/cancel**  
   - Body: `symbol`, `category`, optional `orderId`, optional `orderLinkId` (one required).  
   - Call `bybit_client.cancel_order(...)`.  
   - Return result.

5. **GET /orders**  
   - Query: `symbol`, `category`.  
   - Call `bybit_client.get_open_orders(...)`.  
   - Return list.

6. **GET /wallet-balance**  
   - Query: `accountType=UNIFIED`, optional `coin`.  
   - Call `bybit_client.get_wallet_balance(...)`.  
   - Return result.

7. **GET /positions**  
   - Query: `symbol`.  
   - Call `bybit_client.get_linear_positions(...)`.  
   - Return list.

8. **POST /positions/trading-stop**  
   - Body: `symbol`, optional `stopLoss`, `takeProfit`, etc.  
   - Call `bybit_client.set_linear_trading_stop(...)`.  
   - Return result.

9. **POST /positions/close**  
   - Body: `symbol`, `category`.  
   - If linear: call `get_linear_positions(symbol)`, get size/side, then `create_order(category=linear, symbol, side=opposite, orderType=Market, qty=size)`.  
   - If spot: compute net position from wallet + open orders, then place opposite market order with `qty=live_size`.  
   - Return result. Do **not** write to trade log or `current.json`.

10. **Mount router**  
    - In the FastAPI app that serves the trading gateway (e.g. main or trading entrypoint): `app.include_router(exec_router)`.

11. **Verify with curl**  
    - Start gateway with valid Bybit env (testnet recommended).  
    - Call each endpoint with curl as in § 12; confirm Bybit responds and exchange state changes as expected (e.g. place small order, then cancel; set stop on open position; close position).

---

### 10.3 Milestone 3: Execution Service skeleton

1. **Define OrderIntent**  
   - File: e.g. `backend/app/services/execution_types.py` or in `trade_log`/existing types.  
   - Fields: `symbol`, `side` (`Buy`/`Sell`), `orderType`, `qty`, optional `price`, `context` (dict, for logging).

2. **Entry handler (strategy → order)**  
   - Create module e.g. `backend/app/services/execution_service.py`.  
   - Function: `async def execute_entry(ev: TradeEvent, gateway_config, bybit_client) -> None`.  
   - Map `ev.side` → `Buy`/`Sell`; `qty` from `POSITION_SIZE` (gateway config); build `OrderIntent`; call `bybit_client.create_order(category=gateway_config.market, ...)`.  
   - On success: call `append_entry(...)` and `add_current_trade(...)` with `tradeId=str(ev.time)`, `entryTime=ev.time`, `entryPrice=ev.price`, `initialStopPrice=ev.initial_stop_price`, `currentStopPrice=ev.initial_stop_price`, `side=ev.side`.  
   - Use symbol/interval from gateway config; pass candles/graphics if `append_entry` needs them.

3. **Wire into CandleStream**  
   - File: `backend/app/services/candle_stream.py`.  
   - In `_apply_trade_logging`, after detecting a new entry (trade_id not in `logged_entry_ids`), and before or after `append_entry`: call `execute_entry(ev, gateway_config, bybit_client)`.  
   - Ensure `BybitClient` is available in the flow (inject or resolve from app state when starting the stream).  
   - Only call when `settings.mode == "trading"` and gateway is connected.

4. **Linear: set initial stop on exchange**  
   - After successful entry and `add_current_trade`, if `market == "linear"`: call `bybit_client.set_linear_trading_stop(symbol, stopLoss=ev.initial_stop_price)`.  
   - Optional: do this inside `execute_entry` or in a small helper called from `_apply_trade_logging`.

---

### 10.4 Milestone 4: Stop-hit wiring (trading mode)

1. **Detect stop hit (Linear)**  
   - In the same place that runs the heartbeat (e.g. CandleStream or a trading loop): periodically call `get_linear_positions(symbol)`.  
   - If position size is 0 for the symbol but `current.json` still has an open trade for that symbol/interval → position was closed (e.g. by stop).  
   - Alternatively: subscribe to Bybit WebSocket for position/order updates and detect stop fill.

2. **Detect stop hit (Spot)**  
   - Query wallet balance + open orders; infer net position. If net position is 0 but `current.json` has an open trade → treat as closed.

3. **Close position helper**  
   - Implement logic from § 6.2: given symbol, category, and optional “expected” trade from `current.json`:  
     - Linear: `get_linear_positions` → if size > 0, `create_order` opposite side, qty = size.  
     - Spot: compute live size from balance/orders → place opposite market order with that qty.  
   - After successful close: call `append_exit(symbol, interval, trade_id, close_time, close_price, "stop", points)` and `remove_current_trade(symbol, interval, trade_id)`.

4. **Call close from stop-hit detection**  
   - When stop hit is detected (position gone on exchange), determine which `trade_id` from `current.json` corresponds to that symbol; call close helper (to ensure any residual is closed) and always append exit and remove from `current.json`.

---

### 10.5 Milestone 5: Linear trading-stop support

1. **After stop_move log and current.json update**  
   - In `_apply_trade_logging`, after `append_stop_move` and updating `current.json`’s `currentStopPrice`:  
     - If `market == "linear"`: get live position size via `get_linear_positions(symbol)`; then call `set_linear_trading_stop(symbol, stopLoss=seg.price)` (and if Bybit supports size, pass `slSize` = live size).  
   - Use same `BybitClient` instance as for entry/close.

2. **Handle Bybit errors**  
   - If `set_linear_trading_stop` fails (e.g. position closed externally), log and optionally remove trade from `current.json` or mark for reconciliation.

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


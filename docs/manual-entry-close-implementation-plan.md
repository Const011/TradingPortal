# Manual Entry / Close – Implementation Plan

## Overview

Add a **Trading actions** panel below the indicators panel, visible only when the frontend is connected to a gateway in **trading mode**. The panel exposes buttons to manually **enter** (Long/Short) or **close** a position. Each action opens a confirmation modal; on confirm, the frontend calls backend endpoints that perform the order and update the trade log (current.json, index.jsonl), overriding or bypassing strategy-generated signals.

---

## 1. Frontend

### 1.1 Visibility and placement

- **When shown:** Only when `gatewayConfig?.mode === "trade"` and the user is connected (same condition used elsewhere for “Trading connected”).
- **Where:** In the chart view, directly **below** the existing **Indicators** row (which contains Volume Profile, S/R, OB, Structure, etc.), still inside the same top block above the chart `<div ref={containerRef}>`.
- **Layout:** A single row with a label (e.g. “Trading:”) and buttons: **Long**, **Short**, **Close**.

### 1.2 Buttons and modals

| Button | Action |
|--------|--------|
| **Long** | Open “Confirm entry” modal for direction **Long**. On confirm → call manual-entry API with `side: "long"`. |
| **Short** | Open “Confirm entry” modal for direction **Short**. On confirm → call manual-entry API with `side: "short"`. |
| **Close** | Open “Confirm close position” modal. On confirm → call manual-close API. |

### 1.3 Entry confirmation modal

- **Title:** e.g. “Confirm entry (Long)” / “Confirm entry (Short)”.
- **Content:**
  - **Take profit (optional):** Single input field (number). If the user leaves it empty, the backend does **not** set a take-profit order.
  - **Stop loss (optional override):** Single input field (number). If the user leaves it empty, the backend uses the **calculated** stop (ATR-based or current bar low/high per strategy direction). If the user enters a value, the backend uses it as the stop level.
- **Actions:** “Cancel” (close modal) and “Confirm” (submit request, then close on success or show error).

### 1.4 Close confirmation modal

- **Title:** e.g. “Close position”.
- **Content:** Short text such as “Close the current position and record the exit in the trade log?”
- **Actions:** “Cancel” and “Confirm” (call close API, then close modal or show error).

### 1.5 API usage

- **Base URL:** Use the same backend base URL as the rest of the app (gateway URL when in trading mode).
- **Manual entry:** `POST /api/v1/trading/entry` (see Backend section). Body: `{ side: "long" | "short", takeProfit?: number, stopLoss?: number }`. If `takeProfit` is omitted or null, backend does not set TP. If `stopLoss` is omitted or null, backend computes stop (ATR or bar low/high).
- **Manual close:** `POST /api/v1/trading/close`. No body (or empty object). Uses gateway’s symbol/interval from config; backend closes the position and appends the exit to the trade log.

### 1.6 Suggested stop (optional UX)

- To show the user what stop will be used before they confirm, the frontend can call `GET /api/v1/trading/suggested-stop?side=long|short` when opening the entry modal. Response: `{ entryPrice, stopPrice }` (and optionally `takeProfit` placeholder). Display “Stop will be set to: &lt;stopPrice&gt;” in the modal; if the user fills “Stop loss” override, that value is sent and used instead.

---

## 2. Backend

### 2.1 Routing and mode guard

- Add a **trading** router (or group under existing market API) under `/api/v1/trading/`.
- All endpoints below must run only when **`settings.mode == "trading"`**. If `mode != "trading"`, return `400` or `403` with a clear message (e.g. “Manual entry/close is only available when the gateway is started in trading mode”).
- Use gateway’s **symbol** and **interval** from `settings.trading_symbol` and `settings.trading_interval` (no request parameters for symbol/interval so the frontend cannot override).

### 2.2 Suggested stop (optional)

- **Endpoint:** `GET /api/v1/trading/suggested-stop?side=long|short`
- **Behaviour:**
  - Load candles from `candle_stream_hub.get_cached_candles(settings.trading_symbol, settings.trading_interval, limit=N)` (e.g. N ≥ 100 for ATR).
  - Use **last closed bar** (index `len(candles)-1`) as the “current” bar. Entry price = that bar’s close (or latest mid if you add a tick later).
  - Compute suggested stop using the same logic as manual entry (see 2.3): ATR-based stop and bar low (long) or bar high (short), with mandatory guard below/above bar low/high. Tick size from `bybit_client.get_tick_size(symbol)` for guard epsilon if available.
  - Return `{ "entryPrice": number, "stopPrice": number }`.

### 2.3 Manual entry

- **Endpoint:** `POST /api/v1/trading/entry`
- **Body:** `{ "side": "long" | "short", "takeProfit": number | null (optional), "stopLoss": number | null (optional) }`
  - If `takeProfit` is missing or null → do **not** set a take-profit order.
  - If `stopLoss` is missing or null → **compute** stop (see below). If provided → use it as the stop level (still validate e.g. long: stop &lt; entry, short: stop &gt; entry).

- **Behaviour:**
  1. **Guard:** If `settings.mode != "trading"`, return 400/403.
  2. **Candles:** Get cached candles for `settings.trading_symbol`, `settings.trading_interval` (same as suggested-stop). Last bar index = `len(candles)-1`; entry price = last bar close.
  3. **Stop calculation (when `stopLoss` not provided):**
     - **Long:** Use a stop **below** entry and **below** last bar’s low. Option A: ATR-based (e.g. `entry - ATR * mult`), then clamp to `min(computed, bar.low - tick_eps)`. Option B: bar low minus one tick. Prefer reusing or mirroring the strategy’s logic (e.g. `_compute_initial_stop_long` with minimal context: no OB/SR, or a thin wrapper that only uses ATR + bar low).
     - **Short:** Same idea above entry and above last bar’s high (ATR or bar high + tick).
  4. **TradeEvent:** Build a synthetic `TradeEvent` with `trade_id` = current Unix timestamp (seconds), `bar_index` = last bar index, `price` = entry price, `initial_stop_price` = computed or body stop, `target_price` = body takeProfit if provided else None, `side` = long/short.
  5. **Executor:** Call `submit_entry(ev, symbol, interval, client)` (existing execution_service). That handles reversal (close opposite position first), places market order with stop, writes current.json and index.jsonl (in dry run) or places real order and then trade log on fill.
  6. **Response:** Return `{ "ok": true, "tradeId": string, "message": string }` or `{ "ok": false, "message": string }`. If the executor returns “pending” (order sent but not filled yet), still return success with a message like “Order placed”.

### 2.4 Manual close

- **Endpoint:** `POST /api/v1/trading/close`
- **Body:** None (or empty).
- **Behaviour:**
  1. **Guard:** If `settings.mode != "trading"`, return 400/403.
  2. Call existing `close_position(settings.trading_symbol, settings.trading_interval, client)`. That closes the position on the exchange (or simulates in dry run) and appends the exit to index.jsonl and removes the trade from current.json.
  3. **Response:** Return the executor’s result, e.g. `{ "ok": true, "close_price": number, "points": number, "message": string }` or `{ "ok": false, "message": string }`.

### 2.5 Execution service

- **No change** to `submit_entry` or `close_position` signatures is required. Manual entry builds a `TradeEvent` and calls `submit_entry`; manual close calls `close_position(symbol, interval, client)`.
- **Optional:** Add a small helper in the strategy or in a new module (e.g. `manual_stop.py`) that, given `candles`, `bar_index`, `side`, and optional `tick_size`, returns suggested stop for “current bar” without OB/SR (bar low/high + ATR cap). Then both suggested-stop and manual-entry can call it.

---

## 3. Implementation order

1. **Backend**
   - Add `/api/v1/trading` router and mode guard.
   - Implement `POST /api/v1/trading/close` (wrap `close_position`).
   - Implement stop calculation helper for “last bar” (ATR + bar low/high, tick_size).
   - Implement `POST /api/v1/trading/entry` (candles → synthetic TradeEvent → submit_entry).
   - Optionally implement `GET /api/v1/trading/suggested-stop`.

2. **Frontend**
   - Add Trading actions panel (Long / Short / Close) below indicators, visible when `gatewayConfig?.mode === "trade"`.
   - Add “Confirm close” modal and wire Close button to `POST /api/v1/trading/close`.
   - Add “Confirm entry” modal with Take profit (optional) and Stop (optional override), wire Long/Short to `POST /api/v1/trading/entry`.
   - Optionally call suggested-stop when opening entry modal and display suggested stop in the modal.

3. **Testing**
   - With gateway in trading mode (dry run): Long → confirm → check current.json and index.jsonl; Close → confirm → check exit in index and current cleared.
   - With gateway in simulation mode: panel hidden; direct POST to trading endpoints returns 400/403.

---

## 4. Edge cases and notes

- **Reversal:** If the user clicks Long while a Short is open, `submit_entry` already closes the Short first then places Long. No extra UI needed.
- **Double entry:** Executor already enforces one pending entry per (symbol, interval). If the user clicks Long twice quickly, the second request can return “Pending entry already exists” or similar; show that in the modal or as a toast.
- **Close with no position:** Backend returns “No position to close” or “no trade in current.json”; frontend shows the message and does not change UI state.
- **Take profit:** Only set when the user has entered a value in the TP field; otherwise omit from the order so only stop loss is set.
- **Stop always set:** Entry always sends a stop level (either user override or backend-calculated) so the position is always protected.

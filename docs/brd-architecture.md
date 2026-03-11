# Trading Portal BRD + Architecture (Spot-First v1)

## 1) Purpose

Define the business requirements and implementation architecture for a local/dev-first automated crypto trading platform with:

- Python + FastAPI backend for market data, indicators, execution, and strategy analysis.
- Next.js frontend for watchlists and charting.
- Bybit v5 API as the initial exchange integration.
- OpenRouter-powered strategy review workflow with simulation safety gates.

This document is intentionally scoped to **Spot trading only** for v1 to reduce risk and speed up delivery.

## 2) Business Objectives

- Build a stable, observable platform for algorithmic trading iteration.
- Reduce manual work in strategy evaluation and parameter tuning.
- Prevent unsafe strategy changes from reaching live trading without simulation review.
- Provide clear chart-based visibility into signals, indicators, and trade outcomes.

## 3) Success Metrics (KPIs)

- **Execution reliability:** >= 99.5% successful order submission attempts (excluding exchange-side rejections).
- **State consistency:** >= 99.9% reconciliation match between local orders/positions and exchange state.
- **Data freshness:** ticker updates visible in UI within 1-2 seconds for active symbols.
- **Indicator latency:** indicator values computed and available within 500 ms after new candle ingest.
- **Strategy cycle speed:** AI suggestion -> simulation -> decision loop completed in < 5 minutes for standard runs.

## 4) Scope

### In Scope (v1)

- Bybit Spot market data ingestion (symbols, tickers, candles via REST; optional websocket later).
- Bybit Spot order lifecycle via REST (place/cancel/query).
- Position and balance tracking for Spot assets.
- Indicator computation and persistence **on the backend**; frontend receives and displays pre-calculated values.
- OpenRouter integration to analyze historical trade results and propose parameter updates.
- Simulation/backtest tool to evaluate proposed strategy changes before approval.
- Next.js portal with:
  - Ticker list, latest price, daily change.
  - TradingView Lightweight Charts rendering.
  - Indicator overlays and baseline shape annotations.

### Out of Scope (v1)

- Perpetual futures and leverage workflows.
- Multi-exchange routing.
- Fully autonomous live deployment of AI-generated strategy updates.
- Complex portfolio optimization across multiple accounts.

## 5) Stakeholders and Users

- **Trader/Operator:** monitors market, reviews strategy outcomes, approves changes.
- **Strategy Developer:** defines indicators and logic, interprets simulation output.
- **System Maintainer:** handles operations, observability, incident response.

## 6) Functional Requirements

### FR-1: Market Data Ingestion (Bybit)

- Fetch and store tradable Spot symbols and metadata.
- Ingest ticker snapshots (last price, 24h change, volume).
- Ingest candles for configured intervals (1m, 5m, 15m, 1h, 4h, 1d).
- Maintain canonical normalized model independent of exchange response shape.
- **Unified Spot market:** All chart data (REST + WebSocket) must use the same Bybit market (Spot) to avoid volume/price mismatches.
- Use Bybit REST for historical bootstrap and symbol catalog:
  - `GET /v5/market/instruments-info` for Spot symbols.
  - `GET /v5/market/kline` (category=spot) for historical candles.
- Use Bybit public WebSocket for realtime updates:
  - `wss://stream.bybit.com/v5/public/spot` with `tickers.{symbol}` for ticker list (last price, 24h change, volume24h).
  - `wss://stream.bybit.com/v5/public/spot` with `kline.{interval}.{symbol}` for chart candle updates.
- **Backend candle merge:** Backend merges REST kline (history) + kline WebSocket (current bar) into a single stream; frontend consumes only the merged candle stream for chart data. Ticker stream is used solely for the ticker list, not for chart bar updates.

### FR-2: Order Execution and Tracking

- Place market/limit Spot orders.
- Cancel open orders.
- Query open and historical orders.
- Persist all order lifecycle transitions (new, partially filled, filled, canceled, rejected).
- Use idempotency keys to avoid duplicate order placement during retries.

#### FR-2.a: Bybit Spot Trading API Integration (v1)

For v1 we integrate Bybit **v5 Spot REST** trading endpoints via an extended `BybitClient` and a dedicated **Execution Service**:

- **Private REST wiring**
  - Add authenticated helpers to `BybitClient` for signed private requests (API key/secret from `settings`), including timestamp, recvWindow, and signature.
  - Implement thin wrappers over:
    - `POST /v5/order/create` (place Spot orders; `category=spot`).
    - `POST /v5/order/cancel` (cancel specific Spot order; `category=spot`).
    - `GET /v5/order/realtime` (query open Spot orders; `category=spot`).
    - `GET /v5/account/wallet-balance` (Spot balances from unified account; derive effective Spot position size by comparing base/quote coin balances before/after trades).

- **Execution Service responsibilities**
  - Accept normalized `OrderIntent` from the Trading Strategy / gateway (symbol, side, qty, optional limit price, intended stop level).
  - Map `OrderIntent` → Bybit request:
    - Spot **entry**: `POST /v5/order/create` with `orderType="Market"` or `"Limit"`, `category="spot"`, `symbol`, `qty`, optional `price`, and `orderLinkId` = idempotency key.
    - **Stop management**: v1 keeps stop levels **locally** (in trade log + `current.json`) and closes via a separate exit order when hit, rather than native exchange stop orders.
  - Persist:
    - `OrderIntent` (pre-submission).
    - Bybit order acknowledgements and status updates (new, partial, filled, canceled, rejected).

- **Position / trade state reconciliation**
  - Periodic job (and on gateway start) will:
    1. Read open positions from the trade log’s `current.json` (local source of truth for strategy-level positions).
    2. Call `BybitClient` to fetch:
       - Spot account balances (`GET /v5/account/wallet-balance` or similar).
       - Open Spot orders (`GET /v5/order/realtime`).
    3. Compute **synthetic Spot positions** from balances and compare to `current.json`:
       - Flag drift (e.g. local open position but zero exchange quantity, or vice versa).
       - Optionally write reconciliation markers into the trade log / monitoring.
    4. Surface reconciliation status to the UI (e.g. “Exchange vs local positions: in sync / drifted”).

#### FR-2.b: Close-Position Semantics (v1)

- **Open position with stop (entry + intended stop)**
  - Strategy emits `TradeEvent` with `initial_stop_price` and entry direction; the Execution Service derives **entry size** from gateway config (`POSITION_SIZE`) and risk rules.
  - Gateway converts `TradeEvent` to `OrderIntent` (side, qty, entry price, stop level).
  - Execution Service:
    - Places entry order via `POST /v5/order/create`.
    - On fill (or immediate market execution), records a local **position** in `current.json` including:
      - `side`, `entryPrice`, `qty`, `initialStopPrice`, `symbol`, `tradeId`.
    - No native exchange stop is placed in v1; the **effective stop** is held in our state.

- **Stop hit / position close (per-bar sequence)**
  - Candle stream + strategy are still responsible for detecting when price touches the effective stop level, **but the evaluation is strictly bar-sequenced**:
    1. **On each new bar**, the strategy first checks whether the bar’s range touches the **stop level defined on the previous bar**.
       - If yes → emit a stop-hit event (OB\_STOP\_HIT in logs / exit intent upstream), close the position, and **do not** trail or recalculate the stop on that bar.
    2. With the (possibly closed) position state, the strategy evaluates **entry / reversal** conditions for the current bar and may open a new position.
    3. After entries/reversals are decided, the strategy computes the **new effective stop level for the current bar** (initial stop for new positions, breakeven/trailing for existing ones) and records it as a stop segment. This stop level is considered **active from the next bar onward** for stop-hit checks.
  - When a stop (or manual close) is triggered:
    - Gateway emits an **exit intent** (close reason: stop / manual / end_of_data).
    - Execution Service queries the **current open size on the exchange** (Spot: via wallet balances + open orders; Linear: via `GET /v5/position/list`) and submits an opposite-side market order via `POST /v5/order/create` sized to fully close the live position:
      - Long → send a Sell market order for the current open size.
      - Short (in Linear mode) → send a Buy market order for the current open size.
    - On confirmation/fill:
      - Update trade log (`index.jsonl`) and `current.json` (position removed).
      - Mark any reconciliation metadata so FR-3 jobs can verify against Bybit balances.

### FR-3: Position and Balance Tracking

- Compute synthetic Spot positions from fills/balances, and direct **Linear** positions from Bybit’s position API.
- Track realized and unrealized PnL per symbol and strategy run.
- Reconcile local state with Bybit account snapshots on schedule.

#### FR-3.a: Open Positions vs Trade Log (`current.json`)

- **Source-of-truth layering**
  - **Local strategy state:** `current.json` contains open positions per gateway (symbol, interval, side, qty, entry, stop).
  - **Exchange state (Spot):** Bybit Spot account balances and open orders from private v5 REST.
  - **Exchange state (Linear):** Bybit position info from `GET /v5/position/list?category=linear&symbol=...` plus wallet-balance for margin.
- **Reconciliation flow**
  - On a fixed schedule and on gateway startup:
    - Load `current.json` and derive expected positions.
    - For **Spot**:
      - Fetch unified-account wallet balances via `GET /v5/account/wallet-balance` and open Spot orders via `GET /v5/order/realtime?category=spot`.
      - Derive “net Spot position” from base and quote balances for each symbol.
    - For **Linear**:
      - Fetch current open positions via `GET /v5/position/list?category=linear&symbol=...` (position size, avg entry price, margin, etc).
    - Compare exchange state (Spot or Linear) to local `current.json` entries.
    - If mismatch:
      - Flag the position as “reconciliation required” with details (local vs exchange).
      - Optionally auto-mark local trades as closed with a “reconciled” reason, if policy allows.
  - Expose reconciliation status via a small backend API (`GET /api/v1/reconciliation-status`) for operator visibility.

### FR-4: Indicator Engine

- Compute configured indicators on candle close (e.g., SMA, EMA, RSI, MACD, ATR, VWAP).
- **Volume profile** is computed in the market data pipeline (CandleStreamHub) and streamed with candle snapshot/upsert events; same data is available for strategy and simulation.
- **Cumulative volume delta (CVD)** style indicator is computed alongside candles to quantify buying vs selling pressure:
  - Per bar, derives buying and selling volume from wick/body proportions, then applies EMA smoothing over a configurable length.
  - Computes EMA(buying_volume), EMA(selling_volume), a strength wave (max of the two), and a cumulative volume delta series (buy - sell).
  - Exposed to the frontend as a separate indicator payload in `graphics.cumulativeVolumeDelta` and rendered in its own pane sharing the main chart’s time axis.
- Store values keyed by symbol, timeframe, timestamp, indicator name, and parameter hash.
- Expose indicator series for frontend overlays and for simulation engine.

### FR-5: AI Strategy Review Tool (OpenRouter)

- Input: historical trade results, strategy parameters, indicator performance summary, and constraints.
- Output: structured recommendation payload (parameter deltas, rationale, confidence, risk notes).
- Enforce strict JSON schema response validation.
- Persist all prompts, responses, and model metadata for auditability.

### FR-6: Simulation Tool

- Replay historical candles and strategy logic with candidate parameters.
- Produce projected metrics: net return, max drawdown, win rate, Sharpe-like ratio, trade count.
- Compare candidate vs baseline and generate decision-ready report.
- Feed simulation summary back to AI review loop for iterative improvement.
- Support **two simulation paths** in the UI:
  - **Quick simulation (stream-based):** strategy is evaluated on the full cached window in the candle stream; suitable for fast, iterative visual checks and relative comparisons, but may have mild forward-looking bias in indicator inputs when the full history is preloaded.
  - **Precise simulation (prefix-only, no-future-leakage):** a dedicated backend API `POST /api/v1/strategies/{strategyId}/simulate-precise`:
    - Fetches candles (from cache or Bybit) for the requested `symbol`, `interval`, and `limit`.
    - Uses the `PreciseSimulator` service (`run_precise_simulation`) to recompute indicators and strategy **for each bar i on prefixes `candles[0 : i+1]` (or a trailing window ending at i)** so that no decision or marker on bar i can depend on bars with index > i.
    - Returns a full snapshot-style payload `{ candles, graphics }` compatible with the candle stream (`volumeProfile`, `supportResistance`, `orderBlocks`, `smartMoney.structure`, `strategySignals`), including entry markers and trailing stop segments/lines.
    - Is only exposed in **simulation mode**; trading gateways do not allow precise simulation.

### FR-7: Frontend Portal (Next.js) — Visualization Only

- **Display-only:** Frontend receives pre-calculated candles, indicators, and trade data from the backend; it does not compute indicators or strategy logic.
- Dashboard with symbol list, latest prices, and daily changes.
- Chart page using Lightweight Charts with:
  - Candle series (from backend stream).
  - Indicator overlays (pre-calculated on backend, e.g. volume profile).
  - Trade markers (entry/exit from execution service).
  - Basic shapes/annotations (lines, rectangles, text).
- Strategy review panel for showing AI suggestions and simulation deltas.

## 7) Non-Functional Requirements

### Reliability and Safety

- Retry policy with bounded exponential backoff for exchange/network failures.
- Dead-letter storage for failed ingestion/execution events.
- Circuit-breaker around external dependencies (Bybit, OpenRouter).
- Default live-trading safeguard: strategy updates require explicit operator approval.

### Performance

- API p95 latency target:
  - Read endpoints <= 250 ms (cached paths).
  - Write/execution endpoints <= 500 ms (excluding exchange round-trip).
- UI chart render should remain interactive for at least 5k visible candles.

### Security

- API keys stored via environment secrets, never in source control.
- Bybit v5 private REST authentication uses `X-BAPI-API-KEY`, `X-BAPI-TIMESTAMP` (ms), `X-BAPI-RECV-WINDOW`, and `X-BAPI-SIGN` headers (HMAC-SHA256 over `timestamp + apiKey + recvWindow + query/body` with the API secret).
- Backend settings include `BYBIT_API_KEY`, `BYBIT_API_SECRET`, and an optional `BYBIT_RECV_WINDOW`; these are loaded into `Settings` and used **only** in the Execution Service / `BybitClient` for request signing.
- Role-gated endpoints for execution and strategy approval actions.
- Immutable audit log for order intents, AI suggestions, approvals, and simulation runs.

### Observability

- Structured logs with correlation IDs per request/strategy run.
- Metrics: ingestion lag, order error rates, reconciliation drift, simulation duration.
- Tracing across API -> worker -> external provider calls.

## 8) Domain Model (Core Entities)

- `Symbol` (exchange, base, quote, status, precision).
- `Candle` (symbol, timeframe, open/high/low/close/volume, closeTime).
- `Ticker` (symbol, lastPrice, change24hPct, volume24h, ts).
- `OrderIntent` (idempotencyKey, strategyId, side, type, qty, price, reason).
- `Order` (exchangeOrderId, status, cumulativeQty, avgPrice, timestamps).
- `Fill` (orderId, price, qty, fee, feeAsset, ts).
- `Position` (symbol, qty, avgCost, marketValue, unrealizedPnl).
- `IndicatorValue` (symbol, timeframe, indicator, paramsHash, value, ts).
- `TradeEvent` (time, barIndex, type, side, price, targetPrice?, initialStopPrice, context) — output of Trading Strategy module; consumed by simulation and live signal modules. `initialStopPrice` is required for any order; `targetPrice` optional for close-on-target.
- `StrategyVersion` (strategyId, version, parameters, indicators, approvalState).
- `SimulationRun` (strategyVersion, datasetRange, metrics, artifacts, verdict).
- `AiSuggestion` (model, inputSummary, proposedChanges, confidence, risks).

## 9) System Architecture

### Single Frontend, Multi-Gateway: Trading vs Simulation

**Architecture principle:** One frontend on port 4000 that can connect to either a simulation or trading backend. The user selects the gateway via a control element under the "Trading Portal" caption.

#### Gateway Selection

- **Frontend:** Single instance on port 4000 only.
- **Gateway selector:** Under the caption, the user chooses:
  - **Simulation** — Connects to simulation backend (port 9000).
  - **Trade** — Connects to trading backend; user specifies the port (9001, 9002, … — one per ticker/timeframe).

#### Backend Instances

- **Simulation gateway** (backend 9000): Strategy runs on live candle stream; emits simulated trade events in the graphics payload. No order execution. Full flexibility: user can select any ticker, timeframe, and bars window.
- **Trading gateways** (backend 9001, 9002, …): Multiple instances, one per ticker/timeframe. Strategy runs on live candle stream; emits signals that lead to real orders. All trade events are **logged to a trade log**. Chart displays logged trades + current state from `current.json`. **Fixed config:** symbol, interval, and bars window are set at gateway start; frontend displays only what the gateway produces.

#### Gateway Handshake

On connect, the frontend calls `GET /api/v1/mode`. The gateway responds with:

- `mode`: `"simulation"` | `"trading"`
- If `mode=trading`: `symbol`, `interval`, `bars_window` (e.g. 2000 or 5000)

The frontend adapts:

- **Simulation:** Full controls — ticker list, timeframe selector, volume profile window, etc.
- **Trading:** Read-only display — single ticker, single timeframe, fixed bars window; controls disabled.

#### Trade Display

- **Simulation:** Strategy markers and results computed on every tick from the stream (as programmed).
- **Trading:** Trade markers and results from trade log API + current state from `current.json`.

#### Trade Log (Trading Mode Only)

Each gateway uses files keyed by symbol and timeframe so multiple gateways can run side by side:

- **Base dir:** `{TRADE_LOG_DIR}/{symbol}_{interval}/` (default `logs/trades/`, overridable via env)
- **Index** (`index.jsonl`): JSONL records for entry, stop_move, exit. The backend aggregates these into per-trade objects for the `/api/v1/trade-log` endpoint.
- **Entry snapshots** (`entry_{trade_id}.md`): One Markdown file per entry, same format as "Export for AI".
- **Current trades** (`current.json`): Open positions for gateway restart recovery. Updated on entry, stop move, exit. Read on stream start to restore state.

**Trading-mode restore + observability:** On trading gateway startup, the candle stream restores open positions from `current.json` and logs a `[TRADE_RESTORE]` record including the resolved file path, existence check, and the loaded trade(s). On every heartbeat in trading mode, the backend logs the current open position summary (tradeId, side, entryTime, entryPrice, currentStopPrice) so state continuity across restarts can be verified from logs.

**Trade log semantics (trading mode only):** The trade log records **only actual trade signals generated at the current bar** (the bar being updated by live ticks). It does **not** simulate or log historical strategy calculations. On gateway start, the strategy may run over historical candles for display purposes, but those calculations are never written to the log. Only when a live bar update (upsert) arrives and the strategy emits an entry, stop move, or exit **on that bar** is it logged. Stop moves are written whenever the executor receives a new effective stop level from the strategy for that bar and trade and the stop price actually changes; the executor simply overwrites `currentStopPrice` in `current.json` with the new level and appends a `stop_move` line in `index.jsonl` (any “never move stop against the trade” rule is enforced at the strategy layer). Additionally, **at most one entry or stop move per bar per trade** is written to the log to avoid wobble from repeated live updates.

**Executor ownership of current.json and index (trading mode):** The **Execution Service (executor)** is the single writer of `current.json` and of entry/stop_move/exit lines in `index.jsonl`. The strategy never writes these. On **entry**: strategy sends intent to the executor; executor places the order and, after fill (or in dry run, immediately), writes `current.json` and appends the entry to `index.jsonl`. On **trailing stop move**: strategy emits stop segments; the executor’s `update_stop()` is called (from the candle stream); the executor logs the move and updates `current.json` (and appends `stop_move` to `index.jsonl`). On **stop hit**: the executor detects “no open position at start of heartbeat” (exchange position size 0 while `current.json` still has open trades), then cancels any open orders for the symbol, appends exit to `index.jsonl`, removes the trade(s) from `current.json`, and logs. All of this is done in the executor; the strategy only reads `current.json` to restore state.

**Trade history API (`GET /api/v1/trade-log`):** In trading mode, the gateway exposes a trade history endpoint that returns **per-trade objects** in the same structure the simulation mode uses for chart overlays and profitability tables:

- Each object includes: `entryDateTime`, `side`, `entryPrice`, `closeDateTime`, `closePrice`, `closeReason`, `points`, `markers`, `stopSegments`, `stopLines`, and `events` (normalized from strategy output).
- Completed trades (have an `exit` record in `index.jsonl`) carry realized PnL and `closeReason = "stop" | "take_profit" | "manual"`.
- Still-open trades (present in `current.json` but without an `exit` record) are also included with `closeReason = "open"` and **temporary** `points = 0.0` so the frontend can:
  - Render entry markers and trailing stop lines on the chart.
  - Show open positions in the results table without treating them as realized PnL.

Example: Gateway BTCUSDT 60m → `logs/trades/BTCUSDT_60/`; Gateway ETHUSDT 15m → `logs/trades/ETHUSDT_15/`.

#### Data Flow: Backend Heartbeat (Simulation and Trading)

**Unified heartbeat-driven flow:** Both simulation and trading gateways use the same mechanic. The backend runs a **heartbeat process** that fetches data from Bybit at a configurable interval (seconds). On each fetch, the backend computes indicators and strategy, updates internal state, and broadcasts to connected clients. The frontend connects and receives data—it does **not** initiate fetches. The heartbeat runs regardless of whether any frontend is connected.

- **Simulation:** Symbol and interval are user-selectable; the heartbeat runs for the active symbol/interval (e.g. the one the frontend has subscribed to).
- **Trading:** Symbol and interval are fixed by gateway config; the heartbeat starts on gateway startup for that pair.

#### Configurable Parameters (Trading Gateway)

Each trading gateway is configured at startup via environment variables:

| Variable | Default | Description |
|----------|---------|--------------|
| `BACKEND_PORT` | 9001 | Port for this instance |
| `TRADING_SYMBOL` | BTCUSDT | Ticker (e.g. ETHUSDT, XRPUSDT) |
| `TRADING_INTERVAL` | 60 | Timeframe: 1, 5, 15, 60, 240, D |
| `BARS_WINDOW` | 2000 | Number of bars for chart |
| `FETCH_INTERVAL_SEC` | 60 | Data fetch frequency in seconds; heartbeat polls Bybit REST at this interval (applies to both simulation and trading) |
| `TRADE_LOG_DIR` | logs/trades | Base dir for trade log; files use `{symbol}_{interval}/` subdirs |
| `POSITION_SIZE` | 1.0 | Default position size per entry. For **Spot**, this is base-asset quantity (e.g. 0.01 BTC). For **Linear**, this is contract size or base quantity depending on the selected contract; the Execution Service is responsible for mapping this to an exact `qty` for `POST /v5/order/create`. |

Example: `TRADING_SYMBOL=ETHUSDT TRADING_INTERVAL=15 FETCH_INTERVAL_SEC=30 BACKEND_PORT=9002 ./run-dev-trading.sh`

- **Simulation gateway:** Backend port 9000; frontend uses this when "Simulation" is selected.

See `docs/single-frontend-gateway-plan.md` for the implementation plan.

### Architectural Principle: Backend Computation, Frontend Visualization

**All indicator and trade strategy calculations run on the backend.** The frontend is a thin visualization layer and does not compute indicators, signals, or strategy logic.

- **Indicators** (e.g. volume profile, SMA, RSI, order blocks) are computed in the backend market data or indicator pipeline and streamed/served to the frontend.
- **Trade strategy logic** runs exclusively on the backend (paper/live execution, simulation, backtest).
- **Frontend responsibilities:** display candles, indicator overlays, trade markers, and UI controls; stream data via WebSocket/REST; persist user preferences (e.g. chart interval, volume profile window).

This ensures a single source of truth for indicators and strategy state, consistency between chart display and live/simulation logic, and enables future headless or API-only clients.

```mermaid
flowchart LR
  nextjsUI[NextjsPortal] --> apiGateway[FastApiGateway]

  apiGateway --> marketDataSvc[MarketDataService]
  apiGateway --> executionSvc[ExecutionService]
  apiGateway --> indicatorSvc[IndicatorService]
  apiGateway --> strategySvc[StrategyService]
  apiGateway --> aiAdvisorSvc[AiAdvisorService]
  apiGateway --> simulatorSvc[SimulatorService]

  marketDataSvc --> bybitApi[BybitV5Api]
  executionSvc --> bybitApi

  marketDataSvc --> redisCache[RedisCache]
  indicatorSvc --> redisCache
  apiGateway --> redisCache

  marketDataSvc --> postgresDb[PostgreSQL]
  executionSvc --> postgresDb
  indicatorSvc --> postgresDb
  strategySvc --> postgresDb
  aiAdvisorSvc --> postgresDb
  simulatorSvc --> postgresDb

  aiAdvisorSvc --> openrouterApi[OpenRouterApi]
  aiAdvisorSvc --> simulatorSvc
  strategySvc --> simulatorSvc

  marketDataSvc --> tradingStrategy[TradingStrategyModule]
  indicatorSvc --> tradingStrategy
  tradingStrategy --> simulatorSvc
```

### Service Responsibilities

- **API Gateway (FastAPI):** auth, request validation, external API contracts, websocket fanout.
- **Market Data Service:** symbol/ticker/candle ingestion, normalization, and indicator computation (e.g. volume profile in CandleStreamHub).
- **Execution Service:** order intent -> exchange execution -> state updates.
- **Indicator Service:** compute and publish indicator time series (single source of truth for strategies and frontend). Indicators are pure computation (OB zones, structure, volume profile, S/R).
- **Trading Strategy Module:** consumes candles and pre-calculated indicators; produces **trade events** (signals) in a unified format. Strategy-agnostic: same output for historic simulation and live signal generation. See *Trading Strategy Module* below.
- **Strategy Service:** parameter versioning, approval workflow, strategy metadata.
- **Trade Log Service (trading mode):** appends entry (with Markdown snapshot), stop move, and exit to JSONL index; maintains `current.json` for open positions. Logs only real-time signals at the current bar (no historical backfill); at most one entry or stop move per bar. Read on gateway start to restore state for restart recovery.
- **AI Advisor Service:** OpenRouter calls, schema-validated suggestions, explainability metadata.
- **Simulator Service:** consumes trade events from Trading Strategy; deterministic backtests for baseline vs candidate strategies.

### Trading Strategy Module

The **Trading Strategy** module is the core signal engine. It runs at the backend and is consumed by two modes:

1. **Historic simulation + evaluation** — Simulator replays historical candles, runs strategy, maps trade events to entries/exits, computes PnL and metrics.
2. **Live trade signal generation** — A live runner subscribes to candle stream, invokes strategy on each update, produces `OrderIntent` for applicable events.

The module itself is mode-agnostic: it receives candles and indicators and outputs **TradeEvent[]** (time, type, side, price, context). Simulation and live execution are implemented in separate modules that consume these events.

**Design principles:**
- Indicators (order blocks, structure, volume profile) remain pure computation; no trade logic.
- Bar markers (boundary cross, breaker created) are **trade events** produced by the strategy module, not by indicators. Chart bar markers, when displayed, are derived from strategy output.
- Strategy output format supports both backtest (bar-by-bar replay) and live (event-driven) consumption.

**Module structure:** `backend/app/services/trading_strategy/` — types (`TradeEvent`), strategy implementations (e.g. order-block signals), optional conversion to bar markers for chart display. See `docs/trading-strategy-module-plan.md` for implementation details.

### Runtime Pattern

- FastAPI app for synchronous API.
- Background workers (Celery/RQ/Arq) for polling, reconciliation, indicator jobs, simulations.
- Redis for cache/queues; PostgreSQL for durable state.

## 10) Data Flow

### Candle Stream (Chart Data + Graphics)

1. Backend runs a **heartbeat task** that fetches candles from Bybit REST every `FETCH_INTERVAL_SEC` seconds. On each fetch, it computes indicators (volume profile, order blocks, structure, S/R) and strategy, updates internal state, and broadcasts to all connected clients.
2. Frontend subscribes to `WS /api/v1/stream/candles/{symbol}?interval=...&volume_profile_window=2000&strategy_markers=simulation|trade|off` and receives whatever the backend has (cached snapshot, then updates as heartbeat produces new data). The frontend does **not** trigger Bybit fetches.
3. `graphics` structure: `{ volumeProfile: {...}, supportResistance: { lines: [...] }, orderBlocks: {...}, smartMoney: { structure: {...} }, strategySignals?: {...} }`. Order blocks are pure indicator output. Bar markers, when included, come from the **Trading Strategy** module. In trading mode, `strategySignals` is omitted from stream; frontend fetches trade data via `GET /api/v1/trade-log`.
4. Frontend applies snapshot/upsert events; chart renders candles and graphics. No client-side indicator computation.
5. On gateway restart in trading mode, `CandleStreamHub` loads `current.json` to restore open positions.

### Ticker Stream (Ticker List Only)

1. Frontend subscribes to `WS /api/v1/stream/ticks/{symbol}`.
2. Backend proxies Bybit ticker WebSocket; used for last price, volume24h, change% in ticker list. **Not used for chart bar updates.**

### General

1. **Backend** indicator job computes values per configured symbol/timeframe and writes `IndicatorValue`; chart indicators (e.g. volume profile) are computed in the candle stream pipeline and included in stream payloads.
2. **Trading Strategy** consumes candles and indicators and produces `TradeEvent[]`. Simulation and live modules consume these events.
3. **Backend** live signal flow: strategy emits events → mapped to `OrderIntent` → Execution Service submits to Bybit.
3. Order updates and fills are persisted and reflected in positions.
4. Historical trades + indicators feed AI Advisor request.
5. AI Advisor returns schema-valid parameter proposals.
6. Simulator evaluates proposal; results are attached to suggestion.
7. Operator approves/rejects; approved versions can be activated for paper/live modes.

## 11) API Contract Draft (v1)

### Market Data

- `GET /api/v1/symbols` — tradable Spot symbols.
- `GET /api/v1/intervals` — supported kline intervals.
- `GET /api/v1/tickers?symbols=BTCUSDT,ETHUSDT` — 24h snapshots for ticker list.
- `GET /api/v1/candles?symbol=BTCUSDT&interval=1m&limit=300` — historical klines (standalone fetch).
- `WS /api/v1/stream/candles/{symbol}?interval=1&volume_profile_window=2000` — **primary chart stream:** merged snapshot + live bar upserts + pre-calculated indicators (e.g. volume profile).
- `WS /api/v1/stream/ticks/{symbol}` — ticker stream for ticker list only (last price, volume24h, change%); not used for chart bar updates.

### Trading

- `POST /api/v1/orders/intents`
- `POST /api/v1/orders/place`
- `POST /api/v1/orders/{orderId}/cancel`
- `GET /api/v1/orders?symbol=BTCUSDT&status=open`
- `GET /api/v1/positions`

### Strategy + AI + Simulation

- **Strategy data export (frontend):** User downloads bar data, indicators, orders, and trailing stops via "Export for AI" button. The Markdown file with captioned sections is fed to AI for strategy review and improvement proposals.
- **Mode and gateway config:** `GET /api/v1/mode` — Returns `{ "mode": "simulation" | "trading" }`. When `mode=trading`, also returns `{ "symbol": "BTCUSDT", "interval": "60", "bars_window": 2000 }` (fixed by gateway config).
- **Trade log (trading mode only):** `GET /api/v1/trade-log?symbol=BTCUSDT&interval=60` — Returns logged trades for chart display and results table.
- **Current trades (trading mode only):** `GET /api/v1/current-trades?symbol=BTCUSDT&interval=60` — Returns open positions from `current.json`.
- `POST /api/v1/strategies/{strategyId}/review`
- `POST /api/v1/strategies/{strategyId}/simulate`
- `GET /api/v1/simulations/{runId}`
- `POST /api/v1/strategies/{strategyId}/versions/{version}/approve`
- `GET /api/v1/ai-suggestions?strategyId=...`

## 12) Frontend Architecture (Next.js + Lightweight Charts)

### Computation Boundary

The frontend **does not** compute indicators, signals, or strategy logic. It:
- Subscribes to backend streams (candles, ticks) and displays data.
- Receives indicator data (e.g. `volumeProfile`) within stream payloads or via dedicated endpoints.
- Renders trade markers and annotations from backend-provided coordinates.

All indicator and strategy calculations run on the backend.

### UI Modules

- `MarketOverview`: watchlist/ticker table with sorting and filtering.
- `ChartWorkspace`: chart container with series, overlays, and annotation primitives.
- `StrategyWorkbench`: AI recommendation and simulation comparison panel.

### Strategy Data Export for AI Review

A **data download control** in the chart/indicators area allows exporting the current view's data for AI review and improvement proposals. The export includes:

1. **Bar data (OHLCV)** — Candle open, high, low, close, volume per bar.
2. **Calculated indicators** — Volume profile, support/resistance levels, order blocks, smart money structure (including candle trend colors).
3. **Trade orders** — Strategy-generated entry signals with price, target, initial stop, and context.
4. **Trailing stop events** — Stop level segments over time (start/end time, price, side).

Each section has a **proper caption** so that an AI can parse the document, understand the strategy context, and propose improvements. The export format is Markdown (`.md`), suitable for pasting into AI chat or feeding to the AI Advisor workflow.

### Strategy Results Calculation (Frontend)

When strategy signals are displayed, the frontend computes **trade outcomes in points**:

- **Simulation mode:** Simulates each trade against the candle data from the stream.
- **Trading mode:** Uses precomputed results from the trade log API (`GET /api/v1/trade-log`).
 - **Precise simulation mode:** When the user clicks "Precise simulate", the frontend:
   - Calls `POST /api/v1/strategies/{strategyId}/simulate-precise` via a dedicated helper.
   - Replaces the in-memory `candles` and `graphics` with the precise snapshot (including `strategySignals` built from prefix-only evaluation) and temporarily disables the live candle stream to avoid overwriting these values.
   - Computes results from the precise `strategySignals` against the returned candles for a strictly no-future-leakage backtest view.

In both cases, the logic is:

1. **Entry:** Entry price = close price of the entry bar (bar at `barIndex`).
2. **Stop hit:** Close price = close of the first bar whose range touches the effective stop level. Stop level is taken from trailing stop segments (or initial stop if no segment covers the bar).
3. **Take profit hit:** Close price = close of the first bar whose range touches the target price (when `targetPrice` is set).

For each trade, the outcome is computed as:
- **Long:** `points = closePrice - entryPrice`
- **Short:** `points = entryPrice - closePrice`

A **Strategy Results** table is rendered below the chart with columns: entry date/time, order type (long/short), close date/time, close reason (stop / take_profit / end_of_data / manual), and difference in points. A summary row shows total points and average points per trade.

### Frontend Mode Awareness

- **Gateway selector:** Control under "Trading Portal" caption — user selects Simulation or Trade; when Trade, user enters the trading gateway port (e.g. 9000).
- **Backend URL:** Derived from selection — Simulation → `http://localhost:9000`, Trade → `http://localhost:{user_port}` (e.g. 9001, 9002).
- **Caption:** Header shows "Trading Portal - SIMULATION" or "Trading Portal - TRADING" based on gateway response.
- **Trading mode:** Symbol, interval, and bars window are fixed by gateway config; ticker list, interval buttons, and volume profile window are disabled. Gateway config is fetched via `GET /api/v1/mode` on connect.

### Order Blocks and Swing Labels: Full Data, Frontend Display Control

To simplify data analysis and AI export:
- **Backend** outputs all order blocks and breaker blocks (within lookback); structure returns up to 50 swing labels.
- **Frontend** indicators panel lets the user choose how many to **draw**: Bull OB count, Bear OB count, Swing labels count. Defaults: 5, 5, 15.
- Full data remains available for export and strategy; only the chart rendering is limited.

### State Approach

- Keep top-level pages thin; isolate state in module-level providers and chart-specific components.
- Use server actions or API routes for secure backend communication.
- Stream ticker updates via websocket/SSE where possible.

### Chart and Plugin Strategy

Lightweight Charts does not provide built-in box, line, label, or shape primitives. Drawing is done via **series primitives** (plugins) using `CanvasRenderingContext2D`. Reference indicators (Order Blocks, Support/Resistance) use these Pine graphics objects:

| Pine object | Purpose | LWC approach |
|-------------|---------|--------------|
| **Box** | Order blocks, Fair Value Gaps, volume profile bars | Rectangle Drawing Tool primitive; custom primitive for programmatic boxes |
| **Line** | Structure lines (BOS/CHoCH), S/R horizontals, OB boundaries | Trend Line, Vertical Line primitives; custom for horizontal extend.both |
| **Label** | Swing labels (HH, HL, LH, LL), BOS/CHoCH, EQH/EQL | Anchored Text or custom primitive for price/time-anchored labels |
| **Shape** | Bar markers (triangle, diamond) | `setMarkers()` on series or custom primitive |
| **Volume profile** | Price-level histogram | Official Volume Profile plugin example. **Display:** Must be rendered in **inverse** orientation (bars extend leftward) and positioned **to the right** of the main chart. |
| **Custom candle colors** | 4 colors (bright/dark green, bright/dark red) by trend | Per-point `color`, `wickColor`, `borderColor` on `CandlestickData`; each bar overrides series defaults |

**Official plugin examples** (TradingView): Rectangle Drawing Tool, Trend Line, Vertical Line, Volume Profile, Anchored Text, Bands Indicator. Source: `tradingview.github.io/lightweight-charts/plugin-examples` and `github.com/tradingview/lightweight-charts/plugin-examples`.

**Implementation strategy:**
- Use series primitives for boxes, lines, labels; reuse/adapt official plugins.
- Volume profile: official Volume Profile primitive; displayed in inverse orientation, to the right of the main chart. **Computed on the backend** (indicator engine); frontend only displays pre-calculated data. Uses a configurable **window** (default 2000 bars) with recency weighting: `weight = (window - positionFromNewest) / window`. Window is passed via WebSocket query param and persisted in chart preferences.
- Custom candle colors: set `color`, `wickColor`, `borderColor` per data point in `CandlestickData`; supports 4-way coloring (e.g. swing×internal trend: bright/dark green, bright/dark red).
- **Order blocks and swing labels display limits:** Backend returns **all** order blocks and breaker blocks (within lookback). Structure returns up to 50 swing labels. The **frontend** controls how many to draw via the indicators panel: "Bull" / "Bear" (order blocks to display) and "Swings" (swing labels). This simplifies data analysis and export while keeping the chart readable.
- Status/metric tables: render outside the chart (e.g. sidebar or panel) when needed.
- Keep drawing objects in backend-serializable format (`shapeType`, `points`, `style`, `label`) for reproducibility and auditability.
- **Graphics objects extension:** Backend returns a `graphics` object containing chart primitives. Volume profile remains a specific object (drawn as-is). Generic primitives (e.g. `horizontalLine` for S/R) use a chart-agnostic schema: `{ type, price, width, extend, style }`. Frontend maps each type to Lightweight Charts drawing. See `docs/indicators-support-resistance-plan.md` and `app/schemas/chart_primitives.py`.

## 13) AI Suggestion + Simulation Guardrails

- AI outputs are recommendations, not executable commands.
- Enforce strict JSON schema and reject invalid/partial responses.
- Mandatory simulation before activation of any parameter update.
- Approval gate required for switching active strategy version.
- Maintain rollback to prior strategy version with one-step activation.
- Track overfitting risk with out-of-sample window checks in simulation reports.

## 14) Implementation Roadmap

### Phase 1: Foundation + Market Visibility

- FastAPI skeleton, PostgreSQL/Redis setup, Bybit market data ingestion.
- Next.js portal with ticker list and candle chart.
- Baseline indicators and REST read endpoints.
- Realtime tick bridge from Bybit WebSocket through backend WebSocket to frontend chart.

### Phase 1.1: UI/Feed Wiring

- Symbol switcher in frontend linked to backend market endpoints.
- **Single frontend** on port 4000 with gateway selector (Simulation | Trade + port). Simulation backend 9000; trading backend port user-specified (default 9001). See `docs/single-frontend-gateway-plan.md`.
- On symbol change: reconnect candle stream (backend sends fresh snapshot); reconnect ticker stream for ticker list.
- Lightweight Charts integration with candle series from `WS /stream/candles`; optional tick-based OHLC polish for last bar (ticker used only for last price, not volume).
- Chart viewport: call `fitContent()` only when symbol or interval changes, not on every data update, to preserve scroll/zoom position.

### Phase 2: Paper Trading Loop

- Order intent and execution modules (paper mode first).
- Order/position tracking and reconciliation jobs.
- Chart trade markers and annotation persistence.

### Phase 3: Controlled Live Spot Trading

- Live Bybit execution toggle with safety controls.
- Idempotency, retry hardening, and incident monitoring.
- Approval workflow for strategy version activation.

### Phase 4: AI + Simulation Optimization

- OpenRouter integration with structured outputs.
- Simulation comparison reports and strategy proposal UX.
- Feedback loop from simulation results back into AI review prompts.

### Trading Strategy Module (see docs/trading-strategy-module-plan.md)

- Extract bar marker logic from Order Blocks into Trading Strategy module.
- Order Blocks becomes pure indicator; strategy produces `TradeEvent[]`.
- Wire strategy output for chart bar markers (optional); prepare for simulation and live signal consumption.

## 15) Risks and Mitigations

- **API rate limits / outages:** rate-aware scheduler, retries, and fallback caching.
- **Order state drift:** scheduled reconciliation and conflict flags.
- **Backtest/live mismatch:** include slippage/fees assumptions and walk-forward testing.
- **AI overfitting or unsafe suggestions:** enforce constrained parameter bounds, mandatory simulation, human approval.
- **Chart plugin instability:** pin compatible library versions and wrap custom primitives behind internal adapter interfaces.

## 16) Acceptance Criteria

- Document clearly distinguishes v1 must-have from future capabilities.
- Service boundaries, data ownership, and flow are explicit.
- API and domain model are detailed enough for implementation kickoff.
- AI suggestion loop includes strict safety and governance controls.
- Chart architecture maps directly to required overlays and drawing primitives.


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
- Indicator computation and persistence for frontend overlays.
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

### FR-3: Position and Balance Tracking

- Compute synthetic Spot positions from fills/balances.
- Track realized and unrealized PnL per symbol and strategy run.
- Reconcile local state with Bybit account snapshots on schedule.

### FR-4: Indicator Engine

- Compute configured indicators on candle close (e.g., SMA, EMA, RSI, MACD, ATR, VWAP).
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

### FR-7: Frontend Portal (Next.js)

- Dashboard with symbol list, latest prices, and daily changes.
- Chart page using Lightweight Charts with:
  - Candle series.
  - Indicator overlays.
  - Trade markers (entry/exit).
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
- `StrategyVersion` (strategyId, version, parameters, indicators, approvalState).
- `SimulationRun` (strategyVersion, datasetRange, metrics, artifacts, verdict).
- `AiSuggestion` (model, inputSummary, proposedChanges, confidence, risks).

## 9) System Architecture

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
```

### Service Responsibilities

- **API Gateway (FastAPI):** auth, request validation, external API contracts, websocket fanout.
- **Market Data Service:** symbol/ticker/candle ingestion and normalization.
- **Execution Service:** order intent -> exchange execution -> state updates.
- **Indicator Service:** compute and publish indicator time series.
- **Strategy Service:** parameter versioning, approval workflow, strategy metadata.
- **AI Advisor Service:** OpenRouter calls, schema-validated suggestions, explainability metadata.
- **Simulator Service:** deterministic backtests for baseline vs candidate strategies.

### Runtime Pattern

- FastAPI app for synchronous API.
- Background workers (Celery/RQ/Arq) for polling, reconciliation, indicator jobs, simulations.
- Redis for cache/queues; PostgreSQL for durable state.

## 10) Data Flow

### Candle Stream (Chart Data)

1. Frontend subscribes to `WS /api/v1/stream/candles/{symbol}?interval=...`.
2. Backend `CandleStreamHub` fetches REST kline (spot), broadcasts snapshot.
3. Backend subscribes to Bybit kline WebSocket (spot); for each bar update: replace last candle or append new bar; on new bar start, refetch REST and broadcast fresh snapshot.
4. Frontend applies snapshot/upsert events to local candle state; chart renders from candles only. No accumulation of volume; replace semantics throughout.

### Ticker Stream (Ticker List Only)

1. Frontend subscribes to `WS /api/v1/stream/ticks/{symbol}`.
2. Backend proxies Bybit ticker WebSocket; used for last price, volume24h, change% in ticker list. **Not used for chart bar updates.**

### General

1. Indicator job computes values per configured symbol/timeframe and writes `IndicatorValue`.
2. Strategy generates `OrderIntent`; Execution Service submits to Bybit.
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
- `WS /api/v1/stream/candles/{symbol}?interval=1` — **primary chart stream:** merged snapshot + live bar upserts.
- `WS /api/v1/stream/ticks/{symbol}` — ticker stream for ticker list only (last price, volume24h, change%); not used for chart bar updates.

### Trading

- `POST /api/v1/orders/intents`
- `POST /api/v1/orders/place`
- `POST /api/v1/orders/{orderId}/cancel`
- `GET /api/v1/orders?symbol=BTCUSDT&status=open`
- `GET /api/v1/positions`

### Strategy + AI + Simulation

- `POST /api/v1/strategies/{strategyId}/review`
- `POST /api/v1/strategies/{strategyId}/simulate`
- `GET /api/v1/simulations/{runId}`
- `POST /api/v1/strategies/{strategyId}/versions/{version}/approve`
- `GET /api/v1/ai-suggestions?strategyId=...`

## 12) Frontend Architecture (Next.js + Lightweight Charts)

### UI Modules

- `MarketOverview`: watchlist/ticker table with sorting and filtering.
- `ChartWorkspace`: chart container with series, overlays, and annotation primitives.
- `StrategyWorkbench`: AI recommendation and simulation comparison panel.

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
| **Volume profile** | Price-level histogram | Official Volume Profile plugin example |
| **Custom candle colors** | 4 colors (bright/dark green, bright/dark red) by trend | Per-point `color`, `wickColor`, `borderColor` on `CandlestickData`; each bar overrides series defaults |

**Official plugin examples** (TradingView): Rectangle Drawing Tool, Trend Line, Vertical Line, Volume Profile, Anchored Text, Bands Indicator. Source: `tradingview.github.io/lightweight-charts/plugin-examples` and `github.com/tradingview/lightweight-charts/plugin-examples`.

**Implementation strategy:**
- Use series primitives for boxes, lines, labels; reuse/adapt official plugins.
- Volume profile: official Volume Profile primitive.
- Custom candle colors: set `color`, `wickColor`, `borderColor` per data point in `CandlestickData`; supports 4-way coloring (e.g. swing×internal trend: bright/dark green, bright/dark red).
- Status/metric tables: render outside the chart (e.g. sidebar or panel) when needed.
- Keep drawing objects in backend-serializable format (`shapeType`, `points`, `style`, `label`) for reproducibility and auditability.

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


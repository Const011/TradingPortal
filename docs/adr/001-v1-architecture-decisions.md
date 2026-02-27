# ADR-001: v1 Architecture Decisions (Spot-First)

## Status

Accepted

## Context

The project is a new automated crypto trading platform with Python/FastAPI backend and Next.js frontend. Initial requirements include Bybit data/execution integration, indicator visualization, AI-assisted strategy improvement, and simulation-based feedback.

To avoid unstable early complexity, v1 must prioritize safety, observability, and fast iteration.

## Decisions

### D1: Scope v1 to Spot only

- **Decision:** Start with Bybit Spot only, no futures/leverage in v1.
- **Why:** Simplifies risk logic, reduces margin/funding complexity, speeds delivery.
- **Consequence:** Futures-specific abstractions are deferred but kept in adapter design.

### D2: Local/dev-first deployment

- **Decision:** Optimize first for local execution using Docker Compose.
- **Why:** Faster iteration and lower operational overhead during architecture validation.
- **Consequence:** Production hardening and orchestration are addressed after stable v1 behavior.

### D3: Service-oriented modular monolith

- **Decision:** Implement one FastAPI runtime with clearly separated modules/services.
- **Why:** Lower operational burden than microservices while preserving clear boundaries.
- **Consequence:** Modules can later be extracted if scale or team size requires.

### D4: Durable + volatile storage split

- **Decision:** PostgreSQL for source of truth; Redis for caching/queues.
- **Why:** Durable audit/history plus low-latency data access and job processing.
- **Consequence:** Explicit cache invalidation and queue reliability policies required.

### D5: Order intent and idempotency

- **Decision:** Introduce `OrderIntent` before exchange placement with idempotency keys.
- **Why:** Prevent duplicate orders on retries and support full auditability.
- **Consequence:** Additional state transitions but safer execution lifecycle.

### D6: AI suggestions are advisory only

- **Decision:** OpenRouter output is schema-validated recommendation, never direct execution.
- **Why:** LLM output variance can cause unsafe behavior without deterministic gates.
- **Consequence:** Requires explicit simulation + human approval before activation.

### D7: Simulation as mandatory safety gate

- **Decision:** Every strategy parameter proposal must pass simulation comparison.
- **Why:** Limits overfitting and catches degradations before production exposure.
- **Consequence:** Simulation runtime becomes core critical-path dependency for iteration.

### D8: Lightweight Charts primitives over heavy chart SDK

- **Decision:** Use Lightweight Charts plus custom/community primitives for annotations.
- **Why:** Performance and control are strong; feature set fits v1.
- **Consequence:** Some advanced interactive drawing UX is custom work and staged later.

### D9: REST + WebSocket market data split

- **Decision:** Use Bybit REST for symbols/historical candles and Bybit WebSocket for realtime ticks.
- **Why:** REST is reliable for deterministic history fetch while WebSocket is required for low-latency updates.
- **Consequence:** Backend must own stream lifecycle, reconnection policy, and frontend fanout endpoint.

### D10: Unified Spot market for chart data

- **Decision:** Use Bybit Spot for both REST kline and kline WebSocket; never mix Spot REST with Linear WebSocket.
- **Why:** Spot and Linear are different products; mixing causes volume/price mismatches and apparent "accumulation" or "reset" artifacts.
- **Consequence:** All chart-related endpoints (candles, candle stream) use `category=spot` and `wss://stream.bybit.com/v5/public/spot` for kline topic.

### D11: Backend-owned candle merge

- **Decision:** Backend merges REST kline (history) + kline WebSocket (current bar) into a single `WS /stream/candles` endpoint; frontend consumes only this stream for chart data.
- **Why:** Single source of truth, correct period rollover, validated refetch on new bar, no double volume accumulation.
- **Consequence:** Ticker stream is used solely for ticker list; chart bar updates come exclusively from candle stream.

### D12: Chart viewport preservation

- **Decision:** Call `fitContent()` only when symbol or interval changes, not on every candle/tick update.
- **Why:** Frequent `fitContent()` resets scroll/zoom position on each tick.
- **Consequence:** Track last-fitted symbol:interval key; fit only when it changes.

### D13: Indicator drawing via series primitives

- **Decision:** Use Lightweight Charts series primitives (Canvas 2D) for boxes, lines, labels, shapes; reuse official plugin examples where possible.
- **Why:** LWC has no built-in box/line/label primitives; primitives pattern is the supported extension mechanism.
- **Consequence:** Order Blocks, FVG, S/R, volume profile require custom or adapted primitives. Custom candle colors (4-way by trend) use per-point `color`/`wickColor`/`borderColor` on `CandlestickData`. Status/metric tables render outside the chart when needed.

### D14: Backend-only computation for indicators and strategy

- **Decision:** All indicator and trade strategy calculations run on the backend. Frontend is a thin visualization layer: it receives pre-calculated data and renders it.
- **Why:** Single source of truth for indicators and strategy state; consistency between chart display and live/simulation logic; same indicator data powers strategy execution and frontend; enables future headless/API-only clients.
- **Consequence:** Indicators (volume profile, SMA, RSI, etc.) are computed in the backend market data or indicator pipeline and streamed/served to the frontend. Trade strategy logic runs exclusively on the backend. Frontend does not implement indicator or strategy algorithms.

### D15: Graphics objects extension

- **Decision:** Backend returns a `graphics` object grouping all chart-drawing primitives. Volume profile stays as a specific object (structure unchanged). New primitives (e.g. S/R horizontal lines) use a generic chart-agnostic schema: `{ type, price, width, extend, style }`.
- **Why:** Extensible output format; frontend maps each type to LWC drawing; volume profile remains optimized for its use case; new primitives (boxes, labels, vertical lines) can be added without changing VP.
- **Consequence:** Stream payload has `graphics: { volumeProfile, supportResistance: { lines } }`. Frontend consumes `graphics.volumeProfile` and `graphics.supportResistance.lines`; draws VP as before and S/R as horizontal line primitives.

## Alternatives Considered

- **Microservices from day one:** rejected for early operational complexity.
- **Direct AI-to-live strategy updates:** rejected for unacceptable risk.
- **Full-featured commercial charting stack:** rejected for lock-in and unnecessary v1 overhead.

## Guardrails

- Feature flag for live order placement.
- Approval workflow for strategy version activation.
- Out-of-sample checks in simulation reports.
- Immutable audit records for AI recommendations and operator decisions.

## Review Trigger

Revisit this ADR when one of the following occurs:

- Futures/leverage support is prioritized.
- Throughput requires splitting modules into separate deployables.
- Multi-exchange routing becomes an explicit requirement.
- Annotation UX requirements exceed primitive-based implementation limits.
- Indicator overlays (Order Blocks, S/R, volume profile) require different drawing approach than series primitives.
- Frontend needs to run indicator or strategy logic (would require revisiting D14).


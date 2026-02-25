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


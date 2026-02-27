# Trading Strategy Module — Implementation Plan

## 1. Purpose

Implement a new **Trading Strategy** module that:

- Runs at the backend
- Consumes candles and pre-calculated indicators
- Produces **trade events** (signals) in a unified format
- Is agnostic to consumption mode: historic simulation or live signal generation
- Reuses the bar-marker logic currently in Order Blocks (boundary cross, breaker creation) as signal sources

**Boundary:** The module produces events only. Simulation, evaluation, and live execution are implemented in separate modules that consume these events.

---

## 2. Scope

### In Scope

- New `trading_strategy` service module under `backend/app/services/`
- `TradeEvent` schema (time, type, side, price, context)
- Order Block–based signal detector (boundary cross, breaker creation) extracted from `order_blocks.py`
- Removal of bar markers from Order Blocks module
- Output format suitable for both simulation and live modes
- Optional conversion of `TradeEvent[]` → `BarMarkerData[]` for chart display (when strategy output is streamed for visualization)

### Out of Scope

- Simulation engine (replay, metrics, evaluation)
- Live trade execution / OrderIntent creation
- Additional strategy types (FVG, structure-only, etc.) — extensible for later

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Backend (FastAPI)                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  CandleStreamHub                                                              │
│       │                                                                       │
│       ├──► indicators (order_blocks, smart_money, volume_profile, S/R)         │
│       │         │                                                             │
│       │         └──► graphics (OB boxes, structure lines, VP, S/R)           │
│       │                                                                       │
│       └──► trading_strategy (optional)                                         │
│                 │                                                             │
│                 └──► TradeEvent[] ──► [optional] ──► BarMarkerData[] for chart │
│                                                                               │
│  Simulation Module (separate)         Live Signal Module (separate)            │
│       │                                       │                               │
│       └──► consumes TradeEvent[]               └──► consumes TradeEvent[]     │
│             for backtest/evaluation                    for OrderIntent        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Design Decisions

### D1: Order Blocks → Indicators Only

- **Order Blocks** module becomes a pure indicator: computes OB zones (bullish/bearish, active/breakers) only.
- No bar markers, no trade-signal logic inside Order Blocks.
- Chart bar markers, if needed, are derived from strategy output.

### D2: Strategy Consumes Indicators, Produces Events

- **Trading Strategy** receives `candles` and optionally pre-computed `indicators` (order blocks, structure, etc.).
- If indicators are not provided, the strategy may compute them internally (or require them).
- Output: `list[TradeEvent]` — a time-ordered list of events.

### D3: Shared OB State Machine

- The events (boundary cross, breaker creation) require the same per-bar OB state as Order Blocks.
- **Option A:** Duplicate OB loop in strategy → violates DRY.
- **Option B:** Refactor Order Blocks to expose a step-by-step iterator; strategy consumes it and emits events.
- **Chosen: Option B** — refactor `order_blocks` to expose `iter_order_blocks_with_state(candles)` that yields `(bar_index, candle, bullish_ob, bearish_ob, ...)` per bar.  
  - `compute_order_blocks()` runs this iterator and reduces to final OB primitives.
  - `compute_order_block_signals()` runs the same iterator and collects `TradeEvent`s.

### D4: Unified TradeEvent Schema

- Every event has: `time`, `bar_index`, `type`, `side`, `price`, `target_price`, `initial_stop_price`, `context`.
- `type`: e.g. `OB_BULLISH_BOUNDARY_CROSS`, `OB_BEARISH_BOUNDARY_CROSS`, `OB_BULLISH_BREAKER_CREATED`, `OB_BEARISH_BREAKER_CREATED`.
- `side`: `"long"` | `"short"` | `None` (informational).
- `target_price`: optional; used to issue close orders when the price target is achieved. `None` for strategies without a fixed target.
- `initial_stop_price`: required for any event that leads to an order. No orders may be applied without a stop; downstream modules must enforce this.
- `context`: flexible dict (ob_top, ob_bottom, ob_loc, etc.) for audit and downstream logic.

---

## 5. Module Structure

```
backend/app/
  services/
    indicators/
      order_blocks.py        # Pure OB zones; refactored with iter_order_blocks_with_state
    trading_strategy/
      __init__.py
      types.py               # TradeEvent, StrategyContext
      order_block_signals.py # OB-based signal detector (uses iter_order_blocks_with_state)
      bar_markers.py         # TradeEvent[] → BarMarkerData[] for chart (optional)
```

---

## 6. Data Schemas

### TradeEvent (Python)

```python
@dataclass
class TradeEvent:
    time: int              # Unix seconds (candle close time)
    bar_index: int         # Index in candles list
    type: str              # e.g. OB_BULLISH_BOUNDARY_CROSS, OB_BEARISH_BREAKER_CREATED
    side: str | None       # "long" | "short" | None
    price: float           # Price at event (e.g. close) — entry price for orders
    target_price: float | None  # Optional; used to issue close orders when target is achieved
    initial_stop_price: float   # Required for any event leading to an order; no orders without stop
    context: dict          # type-specific: ob_top, ob_bottom, ob_loc, etc.
```

### Event Types (Order Block Strategy)

| type                         | side  | Description                                      |
|-----------------------------|-------|--------------------------------------------------|
| `OB_BULLISH_BOUNDARY_CROSS`  | long  | Price broke above bullish OB (successful breakout)|
| `OB_BEARISH_BOUNDARY_CROSS`  | short | Price broke below bearish OB                     |
| `OB_BULLISH_BREAKER_CREATED` | short | Bullish OB invalidated (price wicked below)      |
| `OB_BEARISH_BREAKER_CREATED` | long  | Bearish OB invalidated (price wicked above)      |

**Price fields:** Strategy implementations must set `initial_stop_price` for every actionable event. For OB-based signals, typical defaults: long entries — stop below OB bottom (or below swing low); short entries — stop above OB top (or above swing high). `target_price` is strategy-dependent (e.g. next structure level, OB top + ATR multiple, or `None` for trailing/other exits).

---

## 7. Implementation Phases

### Phase 1: Refactor Order Blocks

1. Refactor `order_blocks.py`:
   - Extract `_iter_order_blocks_with_state(candles, ...)` → generator yielding `(i, c, bullish_ob, bearish_ob, ...)` plus any per-bar metadata.
   - `compute_order_blocks()` iterates over this generator and builds final OB primitives; **remove** all bar marker collection.
2. Update `candle_stream.py`: remove `barMarkers` from `orderBlocks` payload.
3. Update frontend: remove bar markers from `OrderBlocksData` type and chart rendering (or source from strategy when wired).

### Phase 2: Trading Strategy Module

1. Create `trading_strategy/types.py` with `TradeEvent` and related types.
2. Create `trading_strategy/order_block_signals.py`:
   - `compute_order_block_signals(candles, **ob_params) -> list[TradeEvent]`
   - Uses `_iter_order_blocks_with_state` from `order_blocks`; at each step, detect boundary cross / breaker creation and append to events.
3. Create `trading_strategy/bar_markers.py`:
   - `trade_events_to_bar_markers(events: list[TradeEvent]) -> list[dict]`
   - Maps event types to `{time, type, position, shape, color}` for Lightweight Charts.

### Phase 3: Wire Strategy to Candle Stream (Optional)

- Add optional `include_trade_signals: bool` to candle stream params.
- When True, call `compute_order_block_signals(candles)` and `trade_events_to_bar_markers(events)`.
- Add `barMarkers` (or `tradeSignals`) to graphics from strategy output.
- Frontend continues to render bar markers when provided.

### Phase 4: Simulation and Live Hooks (Future)

- Simulation module: calls `compute_order_block_signals(candles)` on historical data; maps events to entries/exits; evaluates PnL.
- Live module: subscribes to candle stream; on each snapshot/upsert, runs strategy; emits `OrderIntent` on applicable events.

---

## 8. API Surface

### Trading Strategy (Internal)

```python
# order_block_signals.py
def compute_order_block_signals(
    candles: list[Candle],
    swing_length: int = 20,
    keep_breakers: bool = True,
) -> list[TradeEvent]: ...

# bar_markers.py (for chart display)
def trade_events_to_bar_markers(events: list[TradeEvent]) -> list[dict]: ...
```

### Order Blocks (Refactored)

```python
# order_blocks.py (internal)
def _iter_order_blocks_with_state(candles, ...):  # Generator
    ...

def compute_order_blocks(candles, ...) -> dict:
    # No barMarkers in return
    return {"bullish": [...], "bearish": [...], "bullishBreakers": [...], "bearishBreakers": [...]}
```

---

## 9. Migration and Backward Compatibility

- **Graphics payload:** `orderBlocks` no longer includes `barMarkers`.
- **Chart:** Bar markers will be absent until Phase 3 wires strategy output. User can enable "Trade signals" (or keep "Bar markers") as a toggle that requires strategy computation.
- **Types:** Remove `barMarkers` from `OrderBlocksData`; add optional `barMarkers` from `tradeSignals` or `graphics.tradeSignals` when strategy is wired.

---

## 10. Risks and Mitigations

| Risk                          | Mitigation                                              |
|-------------------------------|---------------------------------------------------------|
| OB refactor breaks chart OB   | Keep `compute_order_blocks()` behavior identical       |
| Strategy loop duplicates OB   | Use shared iterator; single source of truth            |
| Event schema too rigid        | Use `context: dict` for extensibility                 |

---

## 11. Acceptance Criteria

- [ ] Order Blocks module returns only OB primitives (no bar markers).
- [ ] Trading Strategy module produces `TradeEvent[]` from OB-based signals.
- [ ] `TradeEvent` schema supports simulation and live modes.
- [ ] Bar markers for chart can be derived from strategy output when wired.
- [ ] Architecture document updated to reflect Trading Strategy module.

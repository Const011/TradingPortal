# Order Blocks + Smart Money Concepts — Implementation Plan

Based on `docs/indicators-pine/orderblocs.pine` (LuxAlgo inspired). This document defines the distinct logic pieces, required graphic primitives, and how they integrate with backend computation.

---

## 1. Distinct Logic Pieces

The Pine script combines several SMC (Smart Money Concepts) and Order Block components. Each can be implemented and rendered independently.

### 1.1 Order Blocks (OB)

**Logic:**
- Swing detection via `swings(len)`: `high[len] > ta.highest(len)` → swing high at `bar_index[len]`; `low[len] < ta.lowest(len)` → swing low.
- **Bullish OB**: When `close > swing_high.y` (break above swing high), scan from swing bar to current bar; find candle with minimum low; OB = `(max(high of that range), min(low of that range))` at that candle's bar.
- **Bearish OB**: When `close < swing_low.y`, find candle with maximum high; OB = `(max, min)` at that bar.
- **Breaker**: When price breaks below bullish OB bottom (or above bearish OB top), OB becomes "breaker" — historical part stays solid color; from break bar onward, dashed "break" color extends right.
- **Cleanup**: Remove OBs when price closes beyond opposite boundary; remove OBs older than lookback (e.g. 380 bars).

**Parameters:**
- `swingLookback` (length) — default 10
- `showBull`, `showBear` — last N OBs to display (default 3 each)
- `useBody` — use candle body (OHLC) vs wick (HL) for OB bounds

---

### 1.2 Structure Lines (BOS / CHoCH)

**Logic:**
- **Pivot detection**: `leg(size)` — new bearish leg when `high[size] > ta.highest(size)`, new bullish leg when `low[size] < ta.lowest(size)`. Pivots: `swingHigh`, `swingLow` (swing structure, e.g. 50 bars) and `internalHigh`, `internalLow` (internal structure, 5 bars).
- **Break of Structure (BOS)**: Price crosses pivot in the *same* direction as trend — confirms trend.
- **Change of Character (CHoCH)**: Price crosses pivot *against* prior trend — trend reversal.
- **Signal**: On `ta.crossover(close, swingHigh)` → if `swingTrend == BEARISH` then CHoCH (bullish), else BOS (bullish). On `ta.crossunder(close, swingLow)` → if `swingTrend == BULLISH` then CHoCH (bearish), else BOS (bearish). Same for internal structure.
- **Display**: Horizontal line from pivot bar to current bar at pivot price; label (BOS/CHoCH) at midpoint. Internal = dashed, Swing = solid.

**Parameters:**
- `swingLength` — default 50
- `internalLength` — 5 (fixed)
- `showInternals`, `showStructure` — toggles
- `showSwingBull/Bear`, `showInternalBull/Bear` — ALL | BOS | CHoCH filter

---

### 1.3 Swing Point Labels (HH, HL, LH, LL)

**Logic:**
- When a new pivot forms (`startOfNewLeg`), compare `currentLevel` vs `lastLevel`:
  - Swing high: `currentLevel > lastLevel` → HH, else → LH
  - Swing low: `currentLevel < lastLevel` → LL, else → HL
- Draw label at `(pivot_time, pivot_price)` with text HH/HL/LH/LL.

**Parameters:**
- `showSwings` — toggle
- `swingsLength` — same as structure swing length

---

### 1.4 Equal Highs / Lows (EQH, EQL)

**Logic:**
- When pivot forms and `|pivot.currentLevel - newLevel| < threshold * atr(200)`:
  - Equal High: at swing high pivot
  - Equal Low: at swing low pivot
- Draw dotted line from pivot to current bar at the equal level; label EQH or EQL.

**Parameters:**
- `equalHighsLowsLength` — bars to confirm (default 5)
- `equalHighsLowsThreshold` — ATR multiple (default 0.1)
- `equalHighsLowsSize` — label size

---

### 1.5 Fair Value Gaps (FVG)

**Logic:**
- **Bullish FVG**: `low > high[2]` and `close > high[2]`; gap between `high[2]` and `low`. Optional threshold: `barDeltaPercent > threshold`.
- **Bearish FVG**: `high < low[2]` and `close < low[2]`; gap between `low[2]` and `high`.
- **Fill/Delete**: Bullish FVG filled when `low < bottom`; Bearish when `high > top`. Remove from display.
- **Extend**: FVG boxes extend right by `extendBars` (e.g. 2500 bars equivalent in time).

**Parameters:**
- `fairValueGapsThreshold` — auto (cumulative delta %) or manual
- `fairValueGapsExtend` — extension (default 2500)
- Colors for bullish/bearish FVG

---

### 1.6 Candle Coloring

**Logic:**
- 4-way: `swingTrend × internalTrend`:
  - Both bullish → swingBullishColor (bright green)
  - Swing bullish, internal bearish → internalBullishColor (dark green)
  - Swing bearish, internal bullish → internalBearishColor (dark red)
  - Both bearish → swingBearishColor (bright red)

**Output:** Per-candle `color`, `wickColor`, `borderColor` overrides on `CandlestickData`.

---

### 1.7 Position Indicators (Shapes)

**Logic:** Boundary cross and breaker events (trade signals):
- `bullish_boundary_crossed` → triangle up, below bar
- `bearish_boundary_crossed` → triangle down, above bar
- `bullish_breaker_created` → diamond, below bar
- `bearish_breaker_created` → diamond, above bar

**Architecture note:** As of the Trading Strategy module plan, these events are **trade events** produced by the Trading Strategy module (not by Order Blocks). Order Blocks remains a pure indicator (OB zones only). Bar markers for chart display are derived from strategy output when wired. See `docs/trading-strategy-module-plan.md`.

---

### 1.8 Data Output (Plots / Table)

**Logic:**
- Plots: active OB counts, breaker counts, nearest OB levels, structure signals, trend values, FVG signals, etc. Used for strategies and data window.
- Status table: Summary of metrics (active OBs, break signals, trends, etc.).

**Backend fit:** These are numeric/boolean outputs. Can be computed on backend and sent as a separate `orderBlocks` data object. Table/metrics can be rendered in a frontend panel, not on the chart.

---

## 2. Graphic Primitives Required

| Element | Pine Object | Primitive Type | Schema |
|---------|-------------|----------------|--------|
| **Order Blocks** | `box` | `box` | `{ type: "box", topLeft: { time, price }, bottomRight: { time, price }, fillColor, borderColor?, extend?: "right" }` |
| **Order Block (breaker)** | `box` + `line` | `box` (×2) + `horizontalLine` (×4) | Two boxes (historical + extended), four horizontal lines for boundaries |
| **Structure lines** | `line` + `label` | `lineSegment` + `label` | Line: `{ type: "lineSegment", from: { time, price }, to: { time, price }, color, style }`. Label: `{ type: "label", time, price, text, color, style?: "up" \| "down" }` |
| **Swing labels** | `label` | `label` | `{ type: "label", time, price, text: "HH" \| "HL" \| "LH" \| "LL", color }` |
| **Equal H/L** | `line` + `label` | `lineSegment` + `label` | Same as structure |
| **Fair Value Gaps** | `box` (×2 per FVG) | `box` | Two boxes per FVG (top half, bottom half of gap) |
| **Candle colors** | `plotcandle` | Per-point override | Extend `CandlestickData` with optional `color`, `wickColor`, `borderColor` |
| **Position shapes** | `plotshape` | `barMarker` | `{ type: "barMarker", time, position: "above" \| "below", shape: "triangleUp" \| "triangleDown" \| "diamond", color }` |

### 2.1 New Primitives to Add

```python
# chart_primitives.py extensions

# Box (for OB, FVG)
def box(
    top_left: dict,      # { "time": int, "price": float }
    bottom_right: dict,  # { "time": int, "price": float }
    fill_color: str,
    border_color: str | None = None,
    extend: Literal["none", "right"] = "none",
) -> dict: ...

# Line segment (time-bounded; for structure, EQH/EQL)
# JSON: { "from": { "time", "price" }, "to": { "time", "price" }, ... }
def line_segment(
    from_pt: dict,  # { "time": int, "price": float }
    to_pt: dict,
    color: str,
    width: float = 1,
    style: LineStyle = "solid",
) -> dict: ...

# Label (anchored to time/price)
def label(
    time: int,
    price: float,
    text: str,
    color: str,
    style: Literal["up", "down"] = "up",
    size: Literal["tiny", "small", "normal"] = "small",
) -> dict: ...

# Bar marker (shape at bar)
def bar_marker(
    time: int,
    position: Literal["above", "below"],
    shape: Literal["triangleUp", "triangleDown", "diamond"],
    color: str,
) -> dict: ...
```

### 2.2 LWC Mapping

| Primitive | LWC Approach |
|-----------|--------------|
| `box` | Custom series primitive (Rectangle) — `createChart` + `series.attachPrimitive` with box-drawing logic |
| `lineSegment` | Custom primitive or Polyline; LWC has no built-in line-with-two-points; use primitive |
| `label` | Custom primitive for price/time-anchored text; or LWC `createPriceLine` with `title` for simple cases |
| `barMarker` | `series.setMarkers()` — supports shapes at specific times |

---

## 3. Backend Computation Model

### 3.1 Principle

All indicator logic runs on the backend. Frontend receives:
- `candles` — base OHLCV (with optional per-point color overrides for trend coloring)
- `graphics` — `{ volumeProfile, supportResistance, orderBlocks, smartMoney }`

### 3.2 Graphics Structure (Proposed)

```json
{
  "graphics": {
    "volumeProfile": { "time": 1730000000, "profile": [...], "width": 6 },
    "supportResistance": { "lines": [...] },
    "orderBlocks": {
      "bullish": [
        {
          "top": 70200, "bottom": 69800,
          "startTime": 1729999000, "breakTime": null,
          "breaker": false,
          "fillColor": "rgba(33,87,243,0.2)",
          "breakColor": null
        }
      ],
      "bearish": [...]
    },
    "smartMoney": {
      "structure": { "lines": [...], "labels": [...] },
      "swingLabels": [...],
      "equalHighsLows": { "lines": [...], "labels": [...] },
      "fairValueGaps": [
        { "top": 70100, "bottom": 69900, "startTime": 1729998000, "endTime": 1729998500, "bias": "bullish", "fillColor": "..." }
      ],
      "barMarkers": [
        { "time": 1729998200, "position": "below", "shape": "triangleUp", "color": "#2196F3" }
      ]
    }
  }
}
```

**Note:** `barMarkers` are produced by the **Trading Strategy** module (trade events → chart markers), not by the Order Blocks indicator. When wired, they may appear under `graphics.tradeSignals` or `graphics.orderBlocks.barMarkers` (sourced from strategy).

### 3.3 Candle Color Overrides

Two options:
- **A)** Backend computes trend per bar, returns `candleColors: { [time]: { color, wickColor, borderColor } }`; frontend merges into `CandlestickData` when building chart data.
- **B)** Backend returns enriched candles: `candles: [{ time, open, high, low, close, volume, color?, wickColor?, borderColor? }]`; frontend uses as-is.

**Recommendation:** Option B — keep candles self-contained; backend adds optional color fields when `showTrendInput` is enabled.

---

## 4. Implementation Phases

### Phase 1: Backend — Order Blocks Only (MVP)

1. **`order_blocks.py`**  
   - Swing detection (`swings(len)`) in Python.  
   - Bullish/bearish OB formation and breaker logic.  
   - Return list of OB dicts: `{ top, bottom, startTime, breakTime?, breaker, fillColor, breakColor? }`.

2. **`chart_primitives.py`**  
   - Add `box()` helper.

3. **Wire into stream**  
   - Add `orderBlocks` to `graphics` when candles available.  
   - Query params: `ob_swing_length`, `ob_show_bull`, `ob_show_bear`, `ob_use_body`.

4. **Frontend**  
   - Box primitive plugin (or adapt LWC Rectangle example).  
   - Draw boxes from `graphics.orderBlocks`.  
   - Indicator toggle "Order Blocks".

---

### Phase 2: Backend — Structure (BOS/CHoCH)

1. **`smart_money_structure.py`**  
   - Pivot detection (internal + swing).  
   - BOS/CHoCH logic.  
   - Return `{ lines: [...], labels: [...] }`.

2. **`chart_primitives.py`**  
   - Add `line_segment()`, `label()` helpers.

3. **Wire into stream**  
   - Add `smartMoney.structure` to graphics.

4. **Frontend**  
   - Line segment + label primitives.  
   - Toggles: Show Structure, Bullish/Bearish (ALL|BOS|CHoCH).

---

### Phase 3: Swing Labels + Equal Highs/Lows

1. **Backend**  
   - Extend structure logic for HH/HL/LH/LL labels.  
   - Add EQH/EQL detection and output.

2. **Frontend**  
   - Label primitive; reuse line primitive for EQH/EQL.

---

### Phase 4: Fair Value Gaps

1. **`fair_value_gaps.py`**  
   - FVG detection; fill/delete logic.  
   - Return list of FVG boxes.

2. **Wire + frontend**  
   - Add `smartMoney.fairValueGaps`; draw boxes.

---

### Phase 5: Candle Coloring + Bar Markers

1. **Backend**  
   - Compute swing/internal trend per bar.  
   - Return `candleColors` or enriched candles.  
   - Add bar markers (boundary cross, breaker created).

2. **Frontend**  
   - Apply colors to `CandlestickData`.  
   - Use `series.setMarkers()` for bar markers.

---

### Phase 6: Data Output + Status Table

1. **Backend**  
   - Compute and return `orderBlocksData`: active counts, nearest levels, signals, etc.

2. **Frontend**  
   - Optional metrics panel (table) showing OB status, trends, signals.  
   - No chart drawing; pure data display.

---

## 5. File Layout

```
backend/
  app/
    services/
      indicators/
        volume_profile.py         # existing
        support_resistance.py     # existing
        order_blocks.py           # NEW: Phase 1
        smart_money_structure.py  # NEW: Phase 2 (includes swing labels, EQH/EQL in Phase 3)
        fair_value_gaps.py       # NEW: Phase 4
    schemas/
      chart_primitives.py         # extend with box, lineSegment, label, barMarker

frontend/
  src/
    lib/
      chart-plugins/
        volume-profile.ts         # existing
        graphics/
          box-primitive.ts        # NEW: draw boxes
          line-segment-primitive.ts
          label-primitive.ts
    components/
      indicator-control-panel.tsx # extend with OB, SMC toggles
```

---

## 6. Parameters Summary (Query / Config)

| Param | Default | Description |
|-------|---------|-------------|
| `ob_swing_length` | 10 | Swing lookback for OB |
| `ob_show_bull` | 3 | Last N bullish OBs |
| `ob_show_bear` | 3 | Last N bearish OBs |
| `ob_use_body` | false | Use body vs wick |
| `smc_swing_length` | 50 | Swing structure length |
| `smc_show_structure` | true | BOS/CHoCH lines |
| `smc_show_internals` | true | Internal structure |
| `smc_show_swings` | true | HH/HL/LH/LL labels |
| `smc_show_equal_hl` | true | EQH/EQL |
| `smc_equal_threshold` | 0.1 | ATR multiple |
| `fvg_show` | true | Fair value gaps |
| `fvg_extend` | 2500 | Extend FVG right |
| `show_trend_colors` | true | Candle coloring |

---

## 7. Dependencies & Edge Cases

- **Historical buffer**: Pine uses `maxLookback = 380`. Backend has full candle history; can apply similar cap for display (e.g. last 500 bars) to limit primitives.
- **Time units**: Pine uses `time` (seconds) and `bar_index`. Backend candles have `time` in ms; use `time // 1000` for chart compatibility.
- **Merge overlapping OBs**: Pine has `mergeOverlappingOBs` (commented out). Optional enhancement.
- **Confluence filter**: Internal structure has optional `internalFilterConfluenceInput` (bullish/bearish bar body check). Add as param in Phase 2.

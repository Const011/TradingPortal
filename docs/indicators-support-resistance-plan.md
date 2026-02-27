# Support/Resistance from Volume Profile Minima — Implementation Plan

## 1. Algorithm Summary (from Pine)

The Pine script `supportresistance.pine` derives S/R levels from the **minima** of the (smoothed) volume profile histogram:

1. **Volume profile** — Same concept as ours: bucket volume by price, with recency weighting.
2. **Triangular smoothing** — Apply a triangular-weighted moving average (window W) to reduce noise before minima detection.
3. **Local minima detection** — For each bucket index `i` in `[M, N-M-1]`, a point is a minimum if its volume is strictly less than all neighbors in `[i-M, i+M]` (vicinity M).
4. **Line strength (width)** — For each minimum:
   - **Left cluster**: buckets from previous minimum (or start) to current minimum - 1.
   - **Right cluster**: buckets from current minimum + 1 to next minimum (or end).
   - `clusters_avg = (left_sum + right_sum) / (left_size + right_size)`
   - `volume_ratio = clusters_avg / minima_volume` — lower minimum → higher ratio → stronger level.
   - `line_width = max(1, min(volume_ratio * multiplier, max_width))`
5. **Output** — Horizontal lines at each minimum's price, extended both ways, with width proportional to strength.

---

## 2. Python Implementation

### 2.1 Triangular smoothing

```python
def smooth_triangular(values: list[float], window_size: int) -> list[float]:
    """Triangular-weighted moving average. Handles boundaries by clamping."""
    n = len(values)
    if n == 0:
        return []
    w = max(3, window_size if window_size % 2 == 1 else window_size + 1)
    half = w // 2
    # Build triangular weights, normalize
    weights = [half + 1 - abs(j - half) for j in range(w)]
    total = sum(weights)
    weights = [x / total for x in weights]
    out = []
    for i in range(n):
        s = 0.0
        for j, wj in enumerate(weights):
            idx = i + j - half
            idx = max(0, min(n - 1, idx))  # clamp
            s += values[idx] * wj
        out.append(s)
    return out
```

### 2.2 Local minima detection

Options:
- **Pure Python loop** (no deps): Iterate `i` from `M` to `n-M-1`; check `vol[i] < vol[j]` for all `j` in `[i-M, i+M]`, `j != i`.
- **NumPy `argrelmin`** (if we add numpy): `argrelmin(vols, order=M)`.
- **SciPy** — Overkill for this.

**Recommendation:** Pure Python for zero extra dependencies. NumPy is acceptable if already in the stack.

### 2.3 Strength calculation

Follow the Pine logic: for each minimum index `k`, compute left/right cluster sums, then `volume_ratio`, then `line_width` with optional `width_multiplier` and `max_width` params.

---

## 3. Generic Graphics Object Schema

Backend returns **chart-agnostic** primitives; frontend maps them to Lightweight Charts drawing calls.

### 3.1 Horizontal line (S/R, etc.)

```json
{
  "type": "horizontalLine",
  "price": 69500.5,
  "width": 4,
  "extend": "both",
  "color": "rgba(51, 33, 243, 0.24)",
  "style": "solid"
}
```

- `price` — Y-coordinate (price).
- `width` — Line thickness (1–10 or px); encodes strength.
- `extend` — `"left"` | `"right"` | `"both"`.
- `color` — Optional; frontend can apply theme.
- `style` — `"solid"` | `"dashed"` | `"dotted"`.

### 3.2 Vertical line (future)

```json
{
  "type": "verticalLine",
  "time": 1730000000,
  "width": 1,
  "color": "#333",
  "style": "dashed"
}
```

### 3.3 Box (future, e.g. order blocks)

```json
{
  "type": "box",
  "topLeft": { "time": 1730000000, "price": 70000 },
  "bottomRight": { "time": 1730001000, "price": 69800 },
  "fillColor": "rgba(0,0,0,0.1)",
  "borderColor": "#333"
}
```

### 3.4 Extension strategy

Define a small union type:

```python
# backend schemas
HorizontalLine = {
    "type": "horizontalLine",
    "price": float,
    "width": float,
    "extend": "left" | "right" | "both",
    "color": str | None,
    "style": "solid" | "dashed" | "dotted",
}

ChartPrimitive = HorizontalLine | VerticalLine | Box | ...
```

Frontend has a `renderChartPrimitive(primitive, chart, series)` that switches on `type` and draws accordingly.

---

## 4. Integration with Volume Profile

### 4.1 Option A: Extend volume profile response

Current: `{ time, profile, width }`

Extended:

```json
{
  "time": 1730000000,
  "profile": [...],
  "width": 6,
  "supportResistance": {
    "lines": [
      { "type": "horizontalLine", "price": 69200.5, "width": 6, "extend": "both", "style": "solid" },
      { "type": "horizontalLine", "price": 68850.0, "width": 3, "extend": "both", "style": "solid" }
    ]
  }
}
```

### 4.2 Option B: Separate indicators payload

```json
{
  "event": "snapshot",
  "candles": [...],
  "volumeProfile": { ... },
  "indicators": {
    "supportResistance": {
      "lines": [ ... ]
    }
  }
}
```

**Recommendation:** Option A for now — S/R is derived from VP, so bundling is natural. Option B fits when we add more indicators (e.g. order blocks).

### 4.3 Parameters

Expose as query params or config:
- `vicinity` (M) — default 9.
- `smoothing_window` (W) — default 8.
- `width_multiplier` — default 1.0.
- `max_width` — default 10.

---

## 5. File Layout

```
backend/
  app/
    services/
      indicators/
        volume_profile.py      # existing + optional S/R export
        support_resistance.py  # NEW: minima detection, line strength, returns primitives
    schemas/
      chart_primitives.py      # NEW: HorizontalLine, etc.
```

`support_resistance.py`:
- `compute_support_resistance_lines(profile: list[dict], low: float, high: float, num_buckets: int, ...) -> list[HorizontalLine]`
- Uses smoothed profile; finds minima; computes strength; returns generic primitives.

---

## 6. Implementation Order

1. **`smooth_triangular`** — Add to `volume_profile.py` or a shared `indicators/utils.py`.
2. **`compute_support_resistance_lines`** — New module, pure Python.
3. **`chart_primitives` schema** — Pydantic models for `HorizontalLine`, etc.
4. **Wire into CandleStreamHub** — Call S/R after VP; add `supportResistance.lines` to payload.
5. **Frontend primitive renderer** — Map `horizontalLine` to LWC line drawing.
6. **UI toggle** — "Support/Resistance" in indicator panel, similar to Volume Profile.

---

## 7. Minima Detection — Python Options

| Approach | Pros | Cons |
|----------|------|------|
| **Pure Python loop** | No deps | Slightly more code |
| **NumPy `argrelmin`** | Clean, tested | Requires numpy |
| **Smoothing only** | Simpler | May miss/merge minima |

**Recommendation:** Start with pure Python vicinity scan (matches Pine). If we add NumPy later, we can replace with `argrelmin` for consistency and performance.

---

## 8. Edge Cases

- **Few minima** — Normal; may return empty list.
- **Flat regions** — Use strict `<` so plateaus are not minima.
- **Boundary** — Skip first/last M buckets (like Pine).
- **Very low minima_volume** — Guard against division; use `volume_ratio = clusters_avg / max(minima_volume, 1e-10)`.

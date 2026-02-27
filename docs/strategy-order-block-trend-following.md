# Order Block Trend-Following Strategy

Strategy that generates buy/sell signals based on order blocks, candle trend coloring, confirmation rules, and blocking conditions. Documented for **bullish** direction; **bearish** is symmetric (all logic reversed).

---

## 1. Trend Filter (Candle Coloring)

- **Direction constraint:** We only trade in the direction of the current trend.
- **Trend source:** Candle coloring (swing × internal trend from Smart Money structure).
- **Bullish** (green candles) → only **buy** orders.
- **Bearish** (red candles) → only **sell** orders.
- No signals against the trend.

---

## 2. Primary Signal Triggers (Bullish)

A **buy** signal can originate from:

1. **Price broke up from a bullish order block** — Price interacted with the OB zone (low ≤ OB top) and closed above the OB top with a bullish candle. Captures retests (open in zone), breakouts from below, and breakouts with wicks into the zone.
2. **Trend continued and crossed a breaker block** — A bearish OB that became a breaker (price wicked above it) now acts as support; price crosses back above it in line with bullish trend.

Both cases are treated as breakout/continuation above a significant level.

---

## 3. Confirmation (Either/Or)

A trigger is confirmed only if **one** of the following holds:

1. **Two consecutive closes above the block** — Price closed above the OB/breaker top for 2 consecutive bars.
2. **Volume spike** — Volume on the crossing bar is **≥ N × volume average** (N is a parameter, e.g. 2.0).

If neither holds, the signal is not emitted.

---

## 4. Blocking Conditions

Even when a signal is triggered and confirmed, **block** the order if:

1. **Nearby bearish OB:** A bearish OB (active or breaker) is closer to the current price than `BLOCK_OB_DISTANCE_MULT × width` of the triggering OB.  
   - `width` = OB top − OB bottom.  
   - Default multiplier = 2.

2. **Strong support below:** A support line (from S/R) with strength **≥ min_strength** is closer to the current price than `BLOCK_SR_DISTANCE_MULT × width` of the triggering OB.  
   - Strength = line `width` from volume profile S/R (higher = stronger).  
   - Default multiplier = 2.  
   - `min_strength` is a parameter.

---

## 5. Initial Stop Level

For a **long** entry:

- **Option A:** OB bottom of the triggering block.
- **Option B:** Below the closest support line, with gap = `(entry_price − support_price) / 2`.

Choose the **higher** of the two (closer to entry, tighter risk).

For a **short** entry: use OB top or above resistance with the same logic reversed.

---

## 6. Trailing Stop

When price crosses a **higher** level (support line, OB top, or breaker block acting as support):

- **Levels considered:** S/R support lines, bullish OB tops, bearish breaker bottoms (support when broken).
- **Confirmation required** before moving stop (reduces false moves from noise):
  - **Option A:** Unusual volume on the crossing bar (`volume ≥ N × avg volume`).
  - **Option B:** N consecutive closes above the level (`trail_consecutive_closes`, default 2–3).
- **New stop** = `level − trail_param × (level − previous_stop)`
- Default `trail_param` = 0.75 (3/4).

For **short**: levels = S/R resistance, bearish OB bottoms, bullish breaker tops. Confirmation by volume spike or N consecutive closes below the level.

---

## 7. Parameters

| Parameter                 | Default | Description                                                |
|---------------------------|---------|------------------------------------------------------------|
| `volume_spike_mult`       | 2.0     | Volume on crossing bar ≥ N × avg volume for confirmation  |
| `consecutive_closes`        | 2       | Consecutive closes above block for entry confirmation     |
| `trail_consecutive_closes`  | 2       | Consecutive closes above/below level for trail confirmation|
| `block_ob_distance_mult`  | 2.0     | Block if bearish OB within N × trigger OB width           |
| `block_sr_distance_mult`  | 2.0     | Block if strong support within N × trigger OB width       |
| `min_sr_strength`         | 4.0     | Min S/R line width to count as “strong” support            |
| `trail_param`             | 0.75    | Trailing stop: level − N × (level − prev_stop)             |

---

## 8. Output

- **Entry events:** `side`, `price`, `target_price` (optional), `initial_stop_price`, `context`.
- **Stop segments:** For chart display, emit `(startTime, endTime, price)` segments showing the active stop level over time.
- **Markers:**
  - Arrow up = buy entry
  - Arrow down = sell entry
  - **Bold dashed horizontal line** = current stop level. Implemented as line segments from `(startTime, price)` to `(endTime, price)` with dashed style; each segment shows the stop level for that time range. When the stop is trailed, a new segment starts at the trail bar.
- **Requirement:** Volume Profile must be enabled (for S/R levels). Strategy markers will not appear without it.

---

## 9. Bearish Symmetry

For **sell** signals:

- Trigger: price interacted with bearish OB zone (high ≥ OB bottom) and closed below with bearish candle, or crossed below a breaker that acts as resistance.
- Confirmation: 2 consecutive closes below, or volume spike.
- Blocking: nearby bullish OB, strong resistance above.
- Initial stop: OB top or above closest resistance with gap/2.
- Trailing: when price crosses a lower level (resistance, lower OB), move stop down.

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

1. **Price broke up from a bullish order block** — Price touched the **entry zone** (OB bottom to OB bottom + N×OB height) and closed above the OB top with a bullish candle. The entry zone extends from the OB lower boundary upward by N×OB height (`entry_zone_mult`, default 1.2); the enablement (breaker) still uses the original OB boundaries (bottom + width).
2. **Trend continued and crossed a breaker block** — A bearish OB that became a breaker (price wicked above it) now acts as support; price crosses back above it in line with bullish trend.

Both cases are treated as breakout/continuation above a significant level.

**OB entry limit:** Each order block can generate at most **N** entry signals (default 2). This protects against stale price ranges that produce many signals when price oscillates back and forth. Once an OB has triggered N times, it no longer emits boundary-crossed events.

---

## 3. Confirmation (Either/Or)

A trigger is confirmed only if **one** of the following holds:

1. **N consecutive closes above the block** — Price closed above the OB/breaker top for N consecutive bars (trigger bar + next bar). N = `consecutive_closes` (default 2).
2. **Volume spike** — Volume on the crossing bar is **≥ N × volume average**. N = `volume_spike_mult` (default 1.5).

If neither holds, the signal is not emitted. When confirmation is deferred (pending), it may only complete on the **bar immediately after** the trigger; if that bar does not close beyond the level, the pending signal is discarded.

**Price-beyond-OB requirement:** Even when confirmed, entry is only allowed if price has moved beyond the OB in the correct direction:
- **Long:** `close > OB top` (price must be above the block).
- **Short:** `close < OB bottom` (price must be below the block).

This prevents entering when price moved away from the OB in the wrong direction (e.g. shorting when price broke above a bearish OB).

---

## 4. Blocking Conditions

Even when a signal is triggered and confirmed, **block** the order if:

1. **Nearby active opposite OB:** A **strong** (active, non-breaker) opposite OB is too close.  
   - **Long:** Block if active bearish OB below entry (breakers excluded — they act as support when broken).  
   - **Short:** Block if active bullish OB above entry (breakers excluded).  
   - Distance threshold: `BLOCK_OB_DISTANCE_MULT × width` of the triggering OB.  
   - `width` = OB top − OB bottom. Default multiplier = 2.

2. **Strong S/R in direction of trade:**  
   - **Long:** Block if strong **resistance above** entry (ceiling would cap the move).  
   - **Short:** Block if strong **support below** entry (floor would cap the short).  
   - Strength = line `width` from volume profile S/R (higher = stronger).  
   - Distance threshold: `BLOCK_SR_DISTANCE_MULT × width`. `min_strength` is a parameter.

---

## 5. Initial Stop Level

For a **long** entry:

- **Option A:** OB bottom of the triggering block.
- **Option B:** Below the closest support line, with gap = `(entry_price − support_price) / 2`.

- **Mandatory:** Stop cannot be higher than `entry_candle.low − 1` (must be below the bar's low).

Choose the **higher** of the two (closer to entry, tighter risk). The ATR cap further tightens when the structural stop would be very wide.

For a **short** entry: use OB top or above resistance with the same logic reversed. ATR cap: `entry + atr_stop_mult × ATR`. **Mandatory:** Stop cannot be lower than `entry_candle.high + 1` (must be above the bar's high).
---

## 6. Trailing Stop

**Position open price** = entry bar close (we enter on bar close when conditions are met; cannot open at bar open).

When price is above a **higher** level (support line, OB top, or breaker block acting as support):

- **Breakeven (relaxed):** Trail toward position open (entry bar close) when current bar closes above it. No volume spike or consecutive closes required.
- **Levels considered:** S/R support lines (filtered by `trail_sr_min_strength`), bullish OB tops, bearish breaker bottoms (support when broken), position open, breakeven target (`entry_close + frac × (entry_close − entry_open)`).
- **Confirmation required** for S/R/OB levels (reduces false moves from noise). Either:
  - **Option A:** One bar with close above the level **and** unusual volume (`volume ≥ N × avg volume`).
  - **Option B:** N consecutive bars closed above the level (`trail_consecutive_closes`, default 2).
- **New stop** = `level − trail_param × (level − previous_stop)`
- Default `trail_param` = 0.75 (3/4).

For **short**: breakeven when close below entry; levels = S/R resistance, bearish OB bottoms, bullish breaker tops, position open, breakeven target. Confirmation by volume spike (one bar) or N consecutive closes below the level.

---

## 7. Parameters

| Parameter                 | Default | Description                                                |
|---------------------------|---------|------------------------------------------------------------|
| `entry_zone_mult`         | 1.2     | Entry zone extends OB boundary by N×OB height (bullish: up from bottom; bearish: down from top) |
| `volume_spike_mult`       | 1.5     | Volume on crossing bar ≥ N × avg volume for confirmation  |
| `consecutive_closes`      | 2       | Consecutive closes above block for entry confirmation     |
| `trail_consecutive_closes`  | 2       | Consecutive closes above/below level for trail confirmation|
| `block_ob_distance_mult`  | 2.0     | Block if bearish OB within N × trigger OB width           |
| `block_sr_distance_mult`  | 2.0     | Block if strong support within N × trigger OB width       |
| `min_sr_strength`         | 4.0     | Min S/R line width to count as “strong” support            |
| `trail_sr_min_strength`   | 0.0     | Min S/R line width for trailing levels; 0 = include all    |
| `trail_param`             | 0.75    | Trailing stop: level − N × (level − prev_stop)             |
| `max_ob_entry_signals`    | 2       | Max entry signals per OB; prevents stale zones from repeated triggers |
| `atr_length`              | 14      | ATR period for stop cap                                    |
| `atr_stop_mult`           | 2.0     | Cap initial stop at entry ± N × ATR; 0 = disabled           |
| `breakeven_body_frac`     | 0.1     | Trail toward entry + N×(close−open); 0 = disabled            |

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

- Trigger: price touched the **entry zone** (OB top − N×OB height to OB top) and closed below with bearish candle, or crossed below a breaker that acts as resistance. The entry zone extends from the OB upper boundary downward by N×OB height (`entry_zone_mult`). The same OB entry limit (max N signals per OB) applies.
- Confirmation: N consecutive closes below (trigger bar + next bar), or volume spike on the crossing bar. N = `consecutive_closes`.
- Price-beyond-OB: `close < OB bottom` (no short when price is above or inside the block).
- Blocking: nearby active bullish OB (not breaker), strong support below.
- Initial stop: OB top or above closest resistance with gap/2.
- Trailing: when price crosses a lower level (resistance, lower OB), move stop down.

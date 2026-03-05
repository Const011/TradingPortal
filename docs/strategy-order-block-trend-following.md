# Order Block Trend-Following Strategy

Strategy that generates buy/sell signals based on order blocks, candle trend coloring, confirmation rules, and blocking conditions. Documented for **bullish** direction; **bearish** is symmetric (all logic reversed).

---

## 1. Trend Filter (Candle Coloring)

- **Trend source:** Candle coloring from Smart Money structure.
- The strategy receives a **single color per candle** and interprets it as:
  - **Bullish color** → allowed direction for **long** entries and long→short reversals.
  - **Bearish color** → allowed direction for **short** entries and short→long reversals.
- Any other/mixed color is treated as **neutral** and will not open new positions or trigger reversals (see Section 4 for the blocking rule).

---

## 2. Primary Signal Triggers (Bullish)

A **buy** signal can originate from:

1. **Price broke up from a bullish order block** — Price touched the **entry zone** (OB bottom to OB bottom + N×OB height) and closed above the OB top with a bullish candle. The entry zone extends from the OB lower boundary upward by N×OB height (`entry_zone_mult`, default 1.2); the enablement (breaker) still uses the original OB boundaries (bottom + width).
2. **Trend continued and crossed a breaker block** — A bearish OB that became a breaker (price wicked above it) now acts as support; price crosses back above it in line with bullish trend.

Both cases are treated as breakout/continuation above a significant level.

**OB entry limit:** Each order block can generate at most **N** actual trade entries (default 2). This protects against stale price ranges when price oscillates back and forth. The cap counts only **confirmed entries** (trades that were opened), not boundary crosses that were considered but not confirmed (e.g. pending signals that lost confirmation, or triggers that were blocked). Boundary-crossed events are emitted freely; the limit is enforced only when opening a position.

---

## 3. Entry Conditions

Entry when **both** of the following are true for the last N bars (N = `consecutive_closes`, default 2), **excluding** the initial warm-up window of `warmup_bars` (default 1000 bars). No trades are opened before bar index `warmup_bars`:

1. **OB event (strong OBs only):** On any of the last N bars, there is an order block boundary cross or breaker event for an order block **whose strength is above a relative threshold** (see Section 7).
2. **Volume spike:** On any of the last N bars, there is a bar in the direction of trade (bullish for long, bearish for short) with volume ≥ `volume_spike_mult` × average volume of previous 10 bars (default 1.2).

We watch these conditions over the last N bars; if both are true → entry.

**Reversal:** If a position is already open and the **opposite** direction’s entry conditions are met on the current bar, the strategy **closes the open position and opens in the reverse direction** on the same bar (close price). Only one position is held at a time; a new long (short) signal while flat opens long (short); a new short (long) signal while long (short) reverses to short (long).

---

## 4. Blocking Conditions

Even when a signal is triggered and confirmed, **block** the order if (each can be enabled/disabled via parameters):

1. **Trend mismatch (hard filter, always on):**  - confirmed substantial improvement
   - **Long:** Block if the current candle color is **not bullish** (the strategy’s `is_bull` flag is False).
   - **Short:** Block if the current candle color is **not bearish** (the strategy’s `is_bear` flag is False).
   This matches the implementation where entries and reversals are only allowed when the candle color is classified as bullish (for longs) or bearish (for shorts); all other colors are blocked for that direction.

2. **Nearby active opposite OB** (`block_opposite_ob_enabled`, default True):  
   - **Long:** Block if active bearish OB below entry (breakers excluded — they act as support when broken).  
   - **Short:** Block if active bullish OB above entry (breakers excluded).  
   - Distance threshold: `block_ob_distance_mult × width` of the triggering OB (default 2).

3. **Strong S/R in direction of trade** (`block_sr_enabled`, default True):  
   - **Long:** Block if strong **resistance above** entry (ceiling would cap the move).  
   - **Short:** Block if strong **support below** entry (floor would cap the short).  
   - Strength = line `width` from volume profile S/R (higher = stronger).  
   - Distance threshold: `block_sr_distance_mult × width`. `min_sr_strength` is a parameter.

---

## 5. Initial Stop Level

For a **long** entry:

- **Option A:** OB bottom of the triggering block.
- **Option B:** Below the closest support line, with gap = `(entry_price − support_price) / 2`.


Choose the **higher** of the two (closer to entry, tighter risk). The ATR cap further tightens when the structural stop would be very wide.\

- **Mandatory:** Stop cannot be higher than `entry_candle.low − 1` (must be below the bar's low).

For a **short** entry: use OB top or above resistance with the same logic reversed. ATR cap: `entry + atr_stop_mult × ATR`. **Mandatory:** Stop cannot be lower than `entry_candle.high + 1` (must be above the bar's high).
---

## 6. Trailing Stop

**Position open price** = entry bar close (we enter on bar close when conditions are met; cannot open at bar open).

When price is above a **higher** level (support line, OB top, or breaker block acting as support):

- **Breakeven (relaxed):** Trail toward `entry + 0.1×|entry_bar_close − entry_bar_open|` when current bar closes above that level. No volume spike or consecutive closes required.
- **Levels considered:** S/R support lines (filtered by `trail_sr_min_strength`), bullish OB tops, bearish breaker bottoms (support when broken), position open, breakeven target (`entry_close + frac × (entry_close − entry_open)`).
- **Confirmation required** for S/R/OB levels (reduces false moves from noise). Either:
  - **Option A:** One bar with close above the level **and** unusual volume (`volume ≥ N × avg volume`).
  - **Option B:** N consecutive bars closed above the level (`trail_consecutive_closes`, default 2).
- **New stop** = `level − trail_param × (level − previous_stop)`
- Default `trail_param` = 0.75 (3/4).

For **short**: breakeven when close below `entry − 0.1×|entry_bar_close − entry_bar_open|`; levels = S/R resistance, bearish OB bottoms, bullish breaker tops, position open, breakeven target. Confirmation by volume spike (one bar) or N consecutive closes below the level.

---

## 7. Parameters

| Parameter                 | Default | Description                                                |
|---------------------------|---------|------------------------------------------------------------|
| `entry_zone_mult`         | 1.2     | Entry zone extends OB boundary by N×OB height (bullish: up from bottom; bearish: down from top) |
| `volume_spike_mult`       | 1.2     | Bar volume ≥ N × avg volume for confirmation              |
| `volume_confirmation_lookback` | 10 | Bars for volume average (previous N bars)                  |
| `consecutive_closes`      | 2       | Window size for entry: last N bars checked for OB event + volume spike conditions |
| `trail_consecutive_closes`  | 2       | Consecutive closes above/below level for trail confirmation|
| `block_opposite_ob_enabled` | True   | Enable blocking by nearby opposite OB                     |
| `block_sr_enabled`         | True   | Enable blocking by strong S/R in direction of trade       |
| `block_ob_distance_mult`  | 2.0     | Block if opposite OB within N × trigger OB width |
| `block_sr_distance_mult`  | 2.0     | Block if strong S/R within N × trigger OB width            |
| `entry_price_range_mult`  | 2.0     | Legacy: previously constrained entry close to N × OB width of OB; currently unused in entry logic |
| `min_sr_strength`         | 4.0     | Min S/R line width to count as “strong” support            |
| `trail_sr_min_strength`   | 0.0     | Min S/R line width for trailing levels; 0 = include all    |
| `trail_param`             | 0.75    | Trailing stop: level − N × (level − prev_stop)             |
| `max_ob_entry_signals`    | 2       | Max **actual trade entries** per OB; counts only confirmed trades, not boundary crosses |
| `atr_length`              | 14      | ATR period for stop cap                                    |
| `atr_stop_mult`           | 2.0     | Cap initial stop at entry ± N × ATR; 0 = disabled           |
| `breakeven_body_frac`     | 0.1     | Trail toward entry + N×(close−open); 0 = disabled            |
| `warmup_bars`             | 1000    | Number of initial bars used for indicator warm-up; no entries are taken before this bar index |
| `min_ob_strength`         | 0.0     | **Relative OB strength filter (strategy only)**. When > 0, the strategy uses only order blocks whose strength is greater than `min_ob_strength × average_strength` across **all** identified order blocks. The indicator itself keeps all blocks; filtering is applied only at the strategy layer. |

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

- Trigger: price touched the **entry zone** (OB top − N×OB height to OB top) and closed below with bearish candle, or crossed below a breaker that acts as resistance. The entry zone extends from the OB upper boundary downward by N×OB height (`entry_zone_mult`). The same OB entry limit (max N **actual trades** per OB) applies.
- Entry: same 2 conditions (OB event and volume spike) over last N bars. Reversal (long→short or short→long) on opposite signal applies as in Section 3.
- Blocking: `block_opposite_ob_enabled`, `block_sr_enabled` (both default True).
- Initial stop: OB top or above closest resistance with gap/2.
- Trailing: when price crosses a lower level (resistance, lower OB), move stop down.

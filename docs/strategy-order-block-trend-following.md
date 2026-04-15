# Order Block Trend-Following Strategy

Strategy that generates buy/sell signals based on order blocks, candle trend coloring, confirmation rules, and blocking conditions. Documented for **bullish** direction; **bearish** is symmetric (all logic reversed).

---

## 1. Trend Filter (Candle Coloring)

- **Trend source:** Candle coloring from Smart Money structure (one hex per bar).
- **Current tuning:** The bullish and bearish color sets in code both include **all** structure hues, so candle-color alignment is **not** used to discriminate direction; **CVD, risk/reward, and OB/volume rules** (Sections 3–4) carry entry and reversal discipline instead.
- Legacy helpers `is_bull` / `is_bear` remain for optional logging; they are not an extra gate on **reversals** (see Section 3).

---

## 2. Primary Signal Triggers (Bullish)

A **buy** signal can originate from:

1. **Price broke up from a bullish order block** — Price touched the **entry zone** (OB bottom to OB bottom + N×OB height) and closed above the OB top with a bullish candle. The entry zone extends from the OB lower boundary upward by N×OB height (`entry_zone_mult`, default 1.0); the enablement (breaker) still uses the original OB boundaries (bottom + width).
2. **Trend continued and crossed a breaker block** — A bearish OB that became a breaker (price wicked above it) now acts as support; price crosses back above it in line with bullish trend.

Both cases are treated as breakout/continuation above a significant level.

### OB event detection (per bar)

Order in the implementation matches the intended rules:

1. **Registration:** For bar index `i`, the bullish and bearish OB lists are the **complete** sets for that bar (the pivot-based iterator has already appended any block whose `formation_bar` is `i`).
2. **Same-bar evaluation:** Every **eligible** non-breaker block in those lists is tested for the touch + directional-close rules, **including** blocks that first appear on `i`. Eligibility is: anchor `loc < i` **or** `formation_bar == i` (so a block formed on the current bar is never skipped merely because `loc >= i`).
3. **Emit:** `bullish_boundary_crossed` / `bearish_boundary_crossed` is emitted **only** when the bar intersects the entry band **and** the close confirms direction (bullish candle above the OB top for longs; bearish candle below the OB bottom for shorts). **Formation on `i` alone does not emit a boundary cross** — the candle must still satisfy the touch-and-exit geometry on that bar.

Breaker-created events (`*_breaker_created`) follow their separate rules (wick through level, or `break_loc == i`).

**OB entry limit:** Each order block can generate at most **N** actual trade entries (default 2). This protects against stale price ranges when price oscillates back and forth. The cap counts only **confirmed entries** (trades that were opened), not boundary crosses that were considered but not confirmed (e.g. pending signals that lost confirmation, or triggers that were blocked). Boundary-crossed events are emitted freely; the limit is enforced only when opening a position.

---

## 3. Entry Conditions

Entry when **all** of the following are true for the last N bars (N = `consecutive_closes`, default 2), **excluding** the initial warm-up window of `warmup_bars` (default 1000 bars). No trades are opened before bar index `warmup_bars`:

1. **OB event (strong OBs only):** On any of the last N bars, there is an order block boundary cross or breaker event for an order block **whose strength is above a relative threshold** (see Section 7). Boundary crosses are defined in Section 2 (“OB event detection”): touch of entry band plus directional close, including for OBs first listed on that bar.
2. **Volume spike:** On any of the last N bars, there is a bar in the direction of trade (bullish for long, bearish for short) with volume ≥ `volume_spike_mult` × average volume of previous 10 bars (default 1.5).
3. **CVD impulse (anti-chop filter):** The **Cumulative Volume Delta (CVD)** over the last `cvd_sequence_bars` candles (default 1 in current tuning) must show a **consistent directional run**:
   - **Long:** For each of the last `cvd_sequence_bars` bars, point CVD `delta` is **non-negative** (≥ 0).
   - **Short:** Symmetric: for each of the last `cvd_sequence_bars` bars, CVD `delta` is **non-positive** (≤ 0).
4. **Risk/reward vs. opposite OB (target-based filter):**
   - **Long:** The take-profit target is set at the **nearest eligible target level above the entry**. Eligible long-side target levels are:
     - a **bearish order block boundary** (`ob.bottom`) above the entry whose **strength is not less than `min_ob_strength × triggering_bullish_ob_strength`**
     - a **resistance line** above the entry whose `width` is at least `target_sr_min_strength`
     The strategy chooses whichever eligible target is **closer** to the entry. This allows nearby strong resistance lines to cap trend-following longs even when the nearest acceptable bearish OB is farther away. The initial stop is computed as in Section 5. The trade is only opened if the reward:risk ratio meets or exceeds `rr_min`:
     \[
       \frac{\text{target\_price} - \text{entry}}{\text{entry} - \text{stop}} \ge \text{rr\_min}
     \]
   - **Short:** Symmetric: target is the **nearest eligible target level below the entry**:
     - a **bullish order block boundary** (`ob.top`) below the entry whose **strength is not less than `min_ob_strength × triggering_bearish_ob_strength`**
     - a **support line** below the entry whose `width` is at least `target_sr_min_strength`
     The strategy chooses whichever eligible target is **closer** to the entry; trade is only opened if:
     \[
       \frac{\text{entry} - \text{target\_price}}{\text{stop} - \text{entry}} \ge \text{rr\_min}
     \]
   - If no opposite-direction OB boundary exists (no clear structural target), the strategy leaves `target_price` unset and **does not apply** this risk/reward filter.

We watch these conditions over the last N bars; if all are true → entry.

**Reversal:** If a position is open **long** (**short**) and the strategy has built a valid **short** (**long**) entry candidate for this bar, it runs the **same opening path** as when flat (`_open_from_candidate`): CVD sequence, RR vs. target, etc. Only if that path **accepts** the opposite trade does the strategy flip (replace position and emit the new entry event). If filters block the opposite entry, the existing position is **kept**. There is no separate reversal-only candle-color check. The strategy emits a **`REVERSAL_CLOSE`** event for the closed trade id on that bar so simulation **results** can attribute P/L to **Reversal** instead of running the old leg until stop/TP.

**Forced closure (exit only, not a reversal):** After the entry bar, if **stop** and **take-profit** have not already closed the trade on this bar, the position closes when **all** are true:

- The bar’s range **intersects** at least one active **opposite** order block (long → bearish OB span; short → bullish OB span), using the same strength-filtered OB lists as the rest of the bar.
- A **volume spike in the opposite direction** to the open trade: for a **long**, a **bearish** candle (`close < open`) with `volume ≥ forced_closure_volume_mult ×` average volume over the last `volume_confirmation_lookback` bars (same averaging window as volume confirmation; current bar excluded from the average). For a **short**, a **bullish** candle with the same volume rule.
- Default **`forced_closure_volume_mult`** is **3.0** (vs. **1.5** for entry `volume_spike_mult`).

On trigger, the strategy emits **`FORCED_CLOSE`** and flatlines; no new position opens. Simulation summaries treat close reason as **Forced closure** at the bar’s **close** price.

---

## 4. Blocking Conditions

Even when a signal is triggered and confirmed, **block** the order if:

1. **Optional candle-color alignment:** With overlapping bull/bear color sets, this branch does not materially restrict entries; rely on the rules below.
2. **Insufficient CVD sequence (chop guard):**
   - **Long:** Even when OB + volume spike conditions are met, **skip** the entry if the last `cvd_sequence_bars` CVD `delta` bars do **not** satisfy the long CVD impulse rule above (i.e. there are not enough same-direction CVD bars, or CVD is weakening too much).
   - **Short:** Symmetric; skip if the last `cvd_sequence_bars` CVD `delta` bars do not satisfy the short CVD impulse rule.

This rule protects the strategy from entering during **sawtooth / choppy** behaviour where OB triggers and volume spikes appear but the underlying CVD flow is alternating direction or weakening, indicating lack of sustained participation.

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
- **Levels considered:** S/R support lines (filtered by `trail_sr_min_strength`), bullish OB tops, position open, breakeven target (`entry_close + frac × (entry_close − entry_open)`), and **previous bar’s low** when it is above the current stop (higher low = support). With default `keep_breakers=False`, bearish breaker bottoms are not included (crossed OBs are removed from the list).
- **Confirmation required** for S/R/OB/prev-bar levels (reduces false moves from noise). Either:
  - **Option A:** One bar with close above the level **and** unusual volume (`volume ≥ N × avg volume`).
  - **Option B:** N consecutive bars closed above the level (`trail_consecutive_closes`, default 2).
- **New stop** = `level − trail_param × (level − previous_stop)`. When the level is **previous bar’s low**, the strategy uses a more relaxed multiplier `trail_param_prev_bar` (default 0.9) instead of `trail_param` (default 0.7). This helps **end the position after price has been locked in a range**: trailing off the last bar’s low (long) or high (short) with a gentler move allows the stop to catch up when price chops instead of relying only on S/R or OB levels.
- Default `trail_param` = 0.7; default `trail_param_prev_bar` = 0.9.

For **short**: breakeven when close below `entry − 0.1×|entry_bar_close − entry_bar_open|`; levels = S/R resistance, bearish OB bottoms, position open, breakeven target, and **previous bar’s high** when it is below the current stop (lower high = resistance). With default `keep_breakers=False`, bullish breaker tops are not included. Same confirmation and relaxed param for prev-bar high (`trail_param_prev_bar`).

---

## 7. Parameters

| Parameter                      | Default | Description                                                                                                                          |
|--------------------------------|---------|--------------------------------------------------------------------------------------------------------------------------------------|
| `entry_zone_mult`              | 1.0     | Entry zone extends OB boundary by N×OB height (bullish: up from bottom; bearish: down from top)                                     |
| `volume_spike_mult`            | 1.5     | Bar volume ≥ N × avg volume for confirmation                                                                                        |
| `volume_confirmation_lookback` | 10      | Bars for volume average (previous N bars)                                                                                           |
| `consecutive_closes`           | 2       | Window size for entry: last N bars checked for OB event + volume spike conditions                                                   |
| `trail_consecutive_closes`     | 2       | Consecutive closes above/below level for trail confirmation                                                                         |
| `min_sr_strength`              | 4.0     | Min S/R line width to count as “strong” support                                                                                     |
| `target_sr_min_strength`       | 4.0     | Min S/R line width required for a support/resistance line to be considered as a take-profit target candidate.                      |
| `trail_sr_min_strength`        | 0.0     | Min S/R line width for trailing levels; 0 = include all                                                                             |
| `trail_param`                  | 0.7     | Trailing stop: level − N × (level − prev_stop) for S/R/OB levels                                                                    |
| `trail_param_prev_bar`         | 0.9     | Same formula when level is previous bar’s low (long) or high (short); more relaxed to help exit when price is locked in a range    |
| `max_ob_entry_signals`         | 2       | Max **actual trade entries** per OB; counts only confirmed trades, not boundary crosses                                            |
| `atr_length`                   | 14      | ATR period for stop cap                                                                                                             |
| `atr_stop_mult`                | 2.0     | Cap initial stop at entry ± N × ATR; 0 = disabled                                                                                   |
| `breakeven_body_frac`          | 0.1     | Trail toward entry + N×(close−open); 0 = disabled                                                                                   |
| `warmup_bars`                  | 1000    | Number of initial bars used for indicator warm-up; no entries are taken before this bar index                                      |
| `min_ob_strength`              | 0.75    | **Relative OB strength filter (strategy only)**. When > 0, the strategy uses only order blocks whose strength is greater than `min_ob_strength × average_strength` across **all** identified order blocks. The indicator itself keeps all blocks; filtering is applied only at the strategy layer. |
| `keep_breakers`                | False   | **Whether to keep OBs after price closes beyond them.** When **False** (default, both indicator and strategy): an order block is **removed** from the list once price **closes** beyond its level (bullish OB when close > OB top, bearish OB when close < OB bottom). Only those OBs disappear; others stay. When **True**: OBs that have been crossed stay in the list so breaker bottoms (long) and breaker tops (short) can be used as trailing levels. The strategy uses **False** by default (crossed breakers disabled), so trailing levels are S/R, active OBs, entry, breakeven target, and previous bar low/high only. |
| `cvd_length`                   | 7      | Length (bars) of the EMA used to smooth buying and selling volume in the CVD indicator (must match backend CVD length to align visuals and logic). |
| `cvd_sequence_bars`            | 1      | Number of **preceding CVD bars** in the same direction required before allowing an entry; acts as a sequence-length filter against choppy CVD.    |
| `rr_min`                       | 2.5    | Minimum acceptable **reward:risk** ratio when using OB-based take-profit targets. Trades with risk/reward < `rr_min` are blocked.                |

COLORS disabled (all colors allow entry)

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

- Trigger: price touched the **entry zone** (OB top − N×OB height to OB top) and closed below with bearish candle, or crossed below a breaker that acts as resistance. The entry zone extends from the OB upper boundary downward by N×OB height (`entry_zone_mult`). Section 2 (“OB event detection”) applies symmetrically: blocks formed on the signal bar are in the list before evaluation, and `bearish_boundary_crossed` requires band touch + bearish close — not formation alone. The same OB entry limit (max N **actual trades** per OB) applies.
- Entry: same conditions as Section 3 (OB event, volume spike, CVD, RR). Reversal uses that **full** opposite-side path when a position is already open the other way, as in Section 3.
- Initial stop: OB top or above closest resistance with gap/2.
- Trailing: when price crosses a lower level (resistance, lower OB), move stop down.

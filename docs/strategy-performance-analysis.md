# Order Block Trend-Following: Performance Analysis & Improvement Proposals

**Data source:** Strategy export (BTCUSDT 60m, ~2013 bars)  
**Trades:** 11 total (9 short, 2 long)

---

## 1. Trade Statistics Summary

| # | Side | Entry | Initial Stop | Risk ($) | Risk (%) | OB Width |
|---|------|-------|--------------|----------|----------|----------|
| 1 | short | 87,233 | 90,640 | 3,407 | 3.9% | ~2,226 |
| 2 | short | 87,158 | 89,485 | 2,327 | 2.7% | ~1,755 |
| 3 | short | 88,321 | 89,085 | 764 | 0.9% | ~585 |
| 4 | long | 93,468 | 90,120 | 3,348 | 3.6% | ~1,013 |
| 5 | short | 77,852 | 79,420 | 1,568 | 2.0% | ~775 |
| 6 | short | 75,709 | 79,358 | 3,649 | 4.8% | ~3,649 |
| 7 | short | 65,917 | 68,428 | 2,512 | 3.8% | ~952 |
| 8 | short | 69,714 | 71,132 | 1,418 | 2.0% | ~632 |
| 9 | short | 66,872 | 69,239 | 2,367 | 3.5% | ~869 |
| 10 | short | 67,830 | 68,472 | 642 | 0.9% | ~603 |
| 11 | long | 63,824 | 62,500 | 1,324 | 2.1% | ~605 |

**Observations:**
- **Risk range:** 0.9% to 4.8% per trade. Several shorts have 3.5–4.8% risk.
- **Wide OBs → wide stops:** When OB top (short) or bottom (long) is far from entry, initial stop is large.
- **No volatility adjustment:** Stops are purely structural (OB boundaries, S/R). No ATR or volatility scaling.
- **Trailing helps but is slow:** `trail_param=0.75` moves stop 75% toward the new level; confirmation (2 closes or volume) can delay lock-in of profits.

---

## 2. Root Causes of Poor Performance

### 2.1 Wide Initial Stops

- **Cause:** Initial stop = OB top (short) or OB bottom (long), or S/R-based alternative. Large OBs (e.g. 90061–86414 ≈ 3,647 width) force stops far from entry.
- **Effect:** Each losing trade costs 2–5% of position. A few losses erase many small wins.
- **Risk/reward asymmetry:** Even with trailing, early wicks can hit the wide stop before price moves favorably.

### 2.2 No Take-Profit Target

- **Cause:** Strategy exits only via stop (initial or trailed). No fixed target.
- **Effect:** Winners depend entirely on trailing. In choppy markets, price may reverse before trailing can lock profit, turning potential winners into break-even or small losses.
- **Opportunity:** A 1.5R or 2R target would close strong moves earlier and improve expectancy.

### 2.3 Late Entry (Confirmation Delay)

- **Cause:** Entry requires 2 consecutive closes beyond OB or volume spike. By then, the best part of the move may be over.
- **Effect:** Entries occur after a pullback or continuation, increasing the chance of entering near local extremes.
- **Trade-off:** Stricter confirmation reduces false signals but can worsen entry quality.

### 2.4 Trailing Confirmation Too Slow

- **Cause:** Trail requires volume spike **or** N consecutive closes below (short) / above (long) the level. In low-volume or ranging conditions, confirmation is delayed.
- **Effect:** Stop trails slowly; retracements can hit the stop before the new level is confirmed.
- **`trail_param=0.75`:** Moving only 75% toward the level leaves room for wicks to hit the stop.

### 2.5 No Filter for OB Quality

- **Cause:** All OBs that pass blocking conditions are valid. No filter on OB width vs. volatility.
- **Effect:** Very wide OBs (e.g. >2× ATR) are low-probability zones; price can oscillate and trigger multiple false signals.
- **Existing mitigation:** `max_ob_entry_signals=2` limits repeats but does not filter initial quality.

### 2.6 Trend Filter May Be Lagging

- **Cause:** Smart Money Structure candle colors (swing × internal) can lag. Trend may already be reversing when the signal appears.
- **Effect:** Entries in late-trend conditions have higher failure rate.

---

## 3. Proposed Improvements

### 3.1 Cap Initial Stop with ATR (High Impact) — **IMPLEMENTED**

**Idea:** Limit stop distance to `min(OB-based stop, entry ± N × ATR)`.

- ATR (14-period) added to strategy.
- **Short:** `stop = min(structural_stop, entry + atr_stop_mult * ATR)`.
- **Long:** `stop = max(structural_stop, entry - atr_stop_mult * ATR)`.
- **Parameters:** `atr_length=14`, `atr_stop_mult=2.0`. Set `atr_stop_mult=0` to disable.

**Rationale:** Prevents 4–5% risk on single trades when OB is very wide. Keeps risk aligned with volatility.

**To verify:** Run simulation (BTCUSDT 60m), export strategy data, and compare trade list and initial stops with previous export.

---

### 3.2 OB Width Filter (Medium Impact)

**Idea:** Skip entries when OB width > `max_ob_width_atr_mult × ATR`.

- **Parameter:** `max_ob_width_atr_mult` (default 2.0).
- **Logic:** If `ob_top - ob_bottom > max_ob_width_atr_mult * ATR`, do not emit signal for that OB.
- **Rationale:** Very wide OBs are noisy; tighter OBs tend to be higher quality.

---

### 3.3 Optional Take-Profit Target (Medium Impact)

**Idea:** Add optional `target_r_mult` (e.g. 1.5 or 2.0). If price reaches `entry + R × target_r_mult` (long) or `entry - R × target_r_mult` (short), close the trade.

- **Parameter:** `target_r_mult` (default `None` = disabled).
- **Logic:** On each bar, if favorable move ≥ `initial_risk × target_r_mult`, close at target.
- **Rationale:** Locks in profits on strong moves and improves win rate.

---

### 3.4 Tighter Stop Alternative for Wide OBs (Medium Impact)

**Idea:** When OB-based stop is wide, use a structural alternative.

- **Short:** If `ob_top - entry > atr_mult * ATR`, use `entry + atr_mult * ATR` or `ob_bottom + buffer` (whichever is tighter).
- **Long:** Symmetric.
- **Rationale:** Keeps stop near the zone without extending to full OB when OB is large.

---

### 3.5 Faster Trailing (Lower Impact)

**Idea:** Make trailing more responsive.

- **Option A:** Reduce `trail_param` from 0.75 to 0.5 (move stop 50% toward level).
- **Option B:** Reduce `trail_consecutive_closes` from 2 to 1 when volume is above average.
- **Option C:** Add `trail_immediate_on_strong_close` — if close is 1.5× ATR beyond level with volume spike, trail immediately without waiting for N closes.

**Rationale:** Locks in profits sooner and reduces give-back on reversals.

---

### 3.6 Stricter Entry Confirmation (Lower Impact, Higher Selectivity)

**Idea:** Require both volume and structure for entry.

- **Parameter:** `require_volume_and_structure: bool = False`.
- When `True`: require volume spike **and** 2 consecutive closes (not either/or).
- **Rationale:** Fewer but higher-quality setups. May reduce total trades.

---

## 4. Implementation Priority

| Priority | Improvement | Effort | Expected Impact |
|----------|-------------|--------|-----------------|
| 1 | ATR stop cap | Medium | High – limits large losses |
| 2 | OB width filter | Low | Medium – filters weak setups |
| 3 | Take-profit target | Low | Medium – improves expectancy |
| 4 | Tighter stop for wide OBs | Medium | Medium – same as #1, alternative approach |
| 5 | Faster trailing | Low | Low–Medium |
| 6 | Stricter confirmation | Low | Low (fewer trades) |

---

## 5. Recommended First Steps

1. **Add ATR to the strategy** and implement the stop cap (`atr_stop_mult`).
2. **Add OB width filter** (`max_ob_width_atr_mult`).
3. **Backtest** with the export data to compare before/after.
4. **Optionally** add `target_r_mult` and tune based on backtest results.

---

*Analysis based on strategy export from 2026-03-02. Re-run backtests after changes to validate.*

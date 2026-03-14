# Pine Script: main chart + separate section in **one** script

## Topic (TradingView)

**Name:** *Display visuals on the chart from your pane scripts*  
**Mechanism:** parameter **`force_overlay`** on outputs (since ~June 2024).

**Official post:**  
<https://www.tradingview.com/blog/en/display-visuals-on-chart-from-pane-scripts-45290/>

## Core rule

- `indicator(..., overlay = true)`  → **everything** defaults to the **main price chart** (no dedicated pane for that script).
- `indicator(..., overlay = false)` → TradingView gives that script its **own pane** (separate chart section). **Plots** go there by default.

You **cannot** set `overlay` to both true and false in one declaration. So:

| Goal | What you do |
|------|------------------|
| **Separate section** (e.g. CVD, RSI) | `overlay = false` → normal **`plot()`** calls (no `force_overlay`) |
| **Main chart** (S/R, vol profile boxes) from the same script | `line.new` / `box.new` / `plot` with **`force_overlay = true`** |

## One script → two visual areas

1. Declare **`overlay = false`** so the script has **one indicator pane** (the “separate chart section”).
2. Put **pane-only** series there: e.g. cumulative buy/sell EMAs, delta columns — **do not** pass `force_overlay` on those plots.
3. Put **main-chart** drawings on price: **`force_overlay = true`** on every `line.new`, `box.new`, etc.

That uses **one indicator slot** and still shows:

- **Main chart:** vol profile + support/resistance (overlay drawings).
- **Pane:** cumulative volume delta (and any other non–force_overlay plots).

## Limits

- **One pane per script** — you do not get two stacked panes from a single `indicator()`.
- If the platform is **not** TradingView (or an old build without `force_overlay`), this pattern does not apply; only TV’s documented API guarantees it.

## Reference script

`supportresistance_cvd_combined.pine` — `overlay = false`, CVD plots in pane, S/R + profile with `force_overlay = true`.

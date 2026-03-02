/** Strategy data export for AI review. Sections have captions for AI to parse and propose improvements. */

import type {
  Candle,
  VolumeProfileData,
  SupportResistanceData,
  OrderBlocksData,
  SmartMoneyStructureData,
  StrategySignalsData,
} from "@/lib/types/market";

/** Format Unix timestamp to local time "YYYY-MM-DD HH:mm:ss". */
function formatTimestampLocal(ts: number, unit: "s" | "ms" = "s"): string {
  const ms = unit === "s" ? ts * 1000 : ts;
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  const sec = String(d.getSeconds()).padStart(2, "0");
  return `${y}-${m}-${day} ${h}:${min}:${sec}`;
}

export type StrategyExportInput = {
  symbol: string;
  interval: string;
  candles: Candle[];
  volumeProfile: VolumeProfileData | null;
  supportResistance: SupportResistanceData | null;
  orderBlocks: OrderBlocksData | null;
  structure: SmartMoneyStructureData | null;
  strategySignals: StrategySignalsData | null;
};

/**
 * Build markdown export with captioned sections for AI review.
 * AI can parse this to understand the strategy context and propose improvements.
 */
export function buildStrategyExportMarkdown(input: StrategyExportInput): string {
  const exportedAt = formatTimestampLocal(Math.floor(Date.now() / 1000), "s");
  const lines: string[] = [];

  lines.push("# Strategy Data Export");
  lines.push("");
  lines.push(`**Symbol:** ${input.symbol} | **Interval:** ${input.interval} | **Exported:** ${exportedAt}`);
  lines.push("");
  lines.push("---");
  lines.push("");

  // 1. Bar Data (OHLCV)
  lines.push("## 1. Bar Data (OHLCV)");
  lines.push("");
  lines.push("Candle data with open, high, low, close and volume per bar.");
  lines.push("");
  if (input.candles.length > 0) {
    lines.push("| time | open | high | low | close | volume |");
    lines.push("|------|------|------|-----|-------|--------|");
    for (const c of input.candles) {
      const timeStr = formatTimestampLocal(Math.floor(c.time / 1000), "s");
      lines.push(`| ${timeStr} | ${c.open} | ${c.high} | ${c.low} | ${c.close} | ${c.volume} |`);
    }
  } else {
    lines.push("*No candle data.*");
  }
  lines.push("");
  lines.push("---");
  lines.push("");

  // 2. Calculated Indicators
  lines.push("## 2. Calculated Indicators");
  lines.push("");

  lines.push("### 2.1 Volume Profile");
  lines.push("");
  if (input.volumeProfile) {
    lines.push(`Time: ${formatTimestampLocal(input.volumeProfile.time, "s")} | Width: ${input.volumeProfile.width}`);
    lines.push("");
    lines.push("| price | volume |");
    lines.push("|-------|--------|");
    for (const p of (input.volumeProfile.profile ?? []).slice(0, 50)) {
      lines.push(`| ${p.price} | ${p.vol} |`);
    }
    if ((input.volumeProfile.profile ?? []).length > 50) {
      lines.push(`| ... (${(input.volumeProfile.profile ?? []).length - 50} more rows) |`);
    }
  } else {
    lines.push("*Volume profile not available.*");
  }
  lines.push("");

  lines.push("### 2.2 Support / Resistance Levels");
  lines.push("");
  if (input.supportResistance?.lines && input.supportResistance.lines.length > 0) {
    lines.push("| price | width | style |");
    lines.push("|-------|-------|-------|");
    for (const l of input.supportResistance.lines) {
      lines.push(`| ${l.price} | ${l.width} | ${l.style ?? "solid"} |`);
    }
  } else {
    lines.push("*No S/R levels.*");
  }
  lines.push("");

  lines.push("### 2.3 Order Blocks");
  lines.push("");
  if (input.orderBlocks) {
    const allObs = [
      ...(input.orderBlocks.bullish ?? []).map((o) => ({ ...o, list: "bullish" })),
      ...(input.orderBlocks.bearish ?? []).map((o) => ({ ...o, list: "bearish" })),
      ...(input.orderBlocks.bullishBreakers ?? []).map((o) => ({ ...o, list: "bullishBreakers" })),
      ...(input.orderBlocks.bearishBreakers ?? []).map((o) => ({ ...o, list: "bearishBreakers" })),
    ];
    if (allObs.length > 0) {
      lines.push("| list | top | bottom | initiationTime | structureBreakTime | breakerTime | breaker |");
      lines.push("|------|-----|--------|-----------------|--------------------|------------|---------|");
      for (const o of allObs) {
        const init = o.initiationTime != null ? formatTimestampLocal(o.initiationTime, "s") : "-";
        const struct = o.structureBreakTime != null ? formatTimestampLocal(o.structureBreakTime, "s") : "-";
        const breaker = o.breakerTime != null ? formatTimestampLocal(o.breakerTime, "s") : "-";
        lines.push(`| ${(o as { list: string }).list} | ${o.top} | ${o.bottom} | ${init} | ${struct} | ${breaker} | ${o.breaker} |`);
      }
    } else {
      lines.push("*No order blocks.*");
    }
  } else {
    lines.push("*Order blocks not available.*");
  }
  lines.push("");

  lines.push("### 2.4 Smart Money Structure");
  lines.push("");
  if (input.structure) {
    const lineCount = (input.structure.lines ?? []).length;
    const labelCount = (input.structure.labels ?? []).length;
    const swingCount = (input.structure.swingLabels ?? []).length;
    lines.push(`Structure lines: ${lineCount} | Labels: ${labelCount} | Swing labels: ${swingCount}`);
    if (input.structure.candleColors && Object.keys(input.structure.candleColors).length > 0) {
      lines.push(`Candle trend colors: ${Object.keys(input.structure.candleColors).length} bars`);
    }
  } else {
    lines.push("*Structure not available.*");
  }
  lines.push("");
  lines.push("---");
  lines.push("");

  // 3. Trade Orders
  lines.push("## 3. Trade Orders (Entry Signals)");
  lines.push("");
  lines.push("Strategy-generated buy/sell signals with entry price, target and stop.");
  lines.push("");
  if (input.strategySignals?.events && input.strategySignals.events.length > 0) {
    lines.push("| time | barIndex | type | side | price | targetPrice | initialStopPrice | context |");
    lines.push("|------|----------|------|------|-------|-------------|------------------|---------|");
    for (const e of input.strategySignals.events) {
      const ctx = JSON.stringify(e.context ?? {});
      const timeStr = formatTimestampLocal(e.time, "s");
      lines.push(`| ${timeStr} | ${e.barIndex} | ${e.type} | ${e.side ?? "-"} | ${e.price} | ${e.targetPrice ?? "-"} | ${e.initialStopPrice} | ${ctx} |`);
    }
  } else {
    lines.push("*No trade orders in this run.*");
  }
  lines.push("");
  lines.push("---");
  lines.push("");

  // 4. Trailing Stop Events
  lines.push("## 4. Trailing Stop Events");
  lines.push("");
  lines.push("Stop level over time. Each segment shows the active stop from startTime to endTime at the given price.");
  lines.push("");
  if (input.strategySignals?.stopSegments && input.strategySignals.stopSegments.length > 0) {
    lines.push("| startTime | endTime | price | side |");
    lines.push("|-----------|---------|-------|------|");
    for (const s of input.strategySignals.stopSegments) {
      const startStr = formatTimestampLocal(s.startTime, "s");
      const endStr = formatTimestampLocal(s.endTime, "s");
      lines.push(`| ${startStr} | ${endStr} | ${s.price} | ${s.side} |`);
    }
  } else {
    lines.push("*No trailing stop segments.*");
  }
  lines.push("");
  lines.push("---");
  lines.push("");
  lines.push("*End of export. AI: Use this data to review the strategy logic and propose improvements.*");

  return lines.join("\n");
}

/** Trigger browser download of strategy data as a .md file. */
export function downloadStrategyData(input: StrategyExportInput): void {
  const content = buildStrategyExportMarkdown(input);
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `strategy-data-${input.symbol}-${input.interval}-${Date.now()}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

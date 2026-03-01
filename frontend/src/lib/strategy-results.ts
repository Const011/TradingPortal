/**
 * Strategy results calculation: simulates each trade against candle data
 * to compute outcome in points (entry bar close, stop/TP bar close).
 */

import type { Candle } from "@/lib/types/market";
import type {
  StrategyTradeEventData,
  StrategyStopSegmentData,
} from "@/lib/types/market";

export type CloseReason = "stop" | "take_profit" | "end_of_data";

export type StrategyTradeResult = {
  /** Entry bar index */
  barIndex: number;
  /** Entry date/time (ISO string) */
  entryDateTime: string;
  /** Order type */
  side: "long" | "short";
  /** Entry price (close of entry bar) */
  entryPrice: number;
  /** Close price (close of bar that touched stop/TP, or last bar) */
  closePrice: number;
  /** Close bar index */
  closeBarIndex: number;
  /** Close date/time (ISO string) */
  closeDateTime: string;
  /** Why the trade closed */
  closeReason: CloseReason;
  /** Difference in points: long = closePrice - entryPrice, short = entryPrice - closePrice */
  points: number;
};

export type StrategyResultsSummary = {
  trades: StrategyTradeResult[];
  totalPoints: number;
  avgPointsPerTrade: number;
};

/** Normalize timestamp to seconds (handles ms and ms) */
function toSeconds(t: number): number {
  return t >= 1e12 ? t / 1000 : t;
}

/** Get effective stop price for a bar at barTimeSec from the given segments */
function getStopPriceForBar(
  barTimeSec: number,
  side: string,
  initialStop: number,
  segments: StrategyStopSegmentData[]
): number {
  const relevant = segments.filter((s) => s.side === side);
  if (relevant.length === 0) return initialStop;
  // Find segment where barTimeSec is in [startTime, endTime]
  const covering = relevant.find(
    (s) => barTimeSec >= s.startTime && barTimeSec <= s.endTime
  );
  if (covering) return covering.price;
  // Before first segment: use initial stop
  const beforeFirst = relevant.filter((s) => barTimeSec < s.startTime);
  if (beforeFirst.length === relevant.length) return initialStop;
  // After last segment: use last segment's price
  const afterLast = relevant.filter((s) => barTimeSec > s.endTime);
  if (afterLast.length === relevant.length) {
    const last = relevant[relevant.length - 1];
    return last.price;
  }
  // Between segments: use the segment that ended before this bar (or initial)
  const endedBefore = relevant
    .filter((s) => s.endTime < barTimeSec)
    .sort((a, b) => b.endTime - a.endTime);
  return endedBefore.length > 0 ? endedBefore[0].price : initialStop;
}

/** Compute strategy results from trades, candles, and stop segments */
export function computeStrategyResults(
  events: StrategyTradeEventData[],
  candles: Candle[],
  stopSegments: StrategyStopSegmentData[]
): StrategyResultsSummary {
  const trades: StrategyTradeResult[] = [];

  for (const ev of events) {
    if (ev.side !== "long" && ev.side !== "short") continue;
    const entryBarIndex = ev.barIndex;
    if (entryBarIndex < 0 || entryBarIndex >= candles.length) continue;

    const entryCandle = candles[entryBarIndex];
    const entryPrice = entryCandle.close;
    const entryTimeSec = toSeconds(entryCandle.time);
    const entryDateTime = new Date(entryTimeSec * 1000).toISOString();

    const targetPrice = ev.targetPrice ?? null;
    const initialStop = ev.initialStopPrice;

    let closePrice = entryPrice;
    let closeBarIndex = entryBarIndex;
    let closeReason: CloseReason = "end_of_data";

    for (let i = entryBarIndex + 1; i < candles.length; i++) {
      const bar = candles[i];
      const barTimeSec = toSeconds(bar.time);
      const stopPrice = getStopPriceForBar(
        barTimeSec,
        ev.side,
        initialStop,
        stopSegments
      );

      let stopHit = false;
      let tpHit = false;

      if (ev.side === "long") {
        stopHit = bar.low <= stopPrice;
        tpHit = targetPrice != null && bar.high >= targetPrice;
      } else {
        stopHit = bar.high >= stopPrice;
        tpHit = targetPrice != null && bar.low <= targetPrice;
      }

      // Stop is checked first (intra-bar precedence)
      if (stopHit) {
        closePrice = bar.close;
        closeBarIndex = i;
        closeReason = "stop";
        break;
      }
      if (tpHit) {
        closePrice = bar.close;
        closeBarIndex = i;
        closeReason = "take_profit";
        break;
      }
    }

    if (closeReason === "end_of_data" && entryBarIndex < candles.length - 1) {
      const lastBar = candles[candles.length - 1];
      closePrice = lastBar.close;
      closeBarIndex = candles.length - 1;
    }

    const closeTimeSec = toSeconds(candles[closeBarIndex].time);
    const closeDateTime = new Date(closeTimeSec * 1000).toISOString();

    const points =
      ev.side === "long" ? closePrice - entryPrice : entryPrice - closePrice;

    trades.push({
      barIndex: entryBarIndex,
      entryDateTime,
      side: ev.side,
      entryPrice,
      closePrice,
      closeBarIndex,
      closeDateTime,
      closeReason,
      points,
    });
  }

  const totalPoints = trades.reduce((sum, t) => sum + t.points, 0);
  const avgPointsPerTrade =
    trades.length > 0 ? totalPoints / trades.length : 0;

  return {
    trades,
    totalPoints,
    avgPointsPerTrade,
  };
}

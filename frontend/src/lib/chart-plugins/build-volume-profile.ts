import type { Time } from "lightweight-charts";
import type { Candle } from "@/lib/types/market";
import type { VolumeProfileData, VolumeProfileDataPoint } from "./volume-profile";

const DEFAULT_BUCKETS = 500;

/**
 * Builds volume profile data from candles by distributing each candle's volume
 * across the price levels it touched (high to low). This matches standard VP methodology
 * better than typical-price bucketing. Profile is sorted by price descending for rendering.
 */
export function buildVolumeProfileFromCandles(
  candles: Candle[],
  time: Time,
  width: number = 6,
  numBuckets: number = DEFAULT_BUCKETS
): VolumeProfileData | null {
  if (candles.length === 0) return null;

  const low = Math.min(...candles.map((c) => c.low));
  const high = Math.max(...candles.map((c) => c.high));
  const range = high - low;
  if (range <= 0) return null;

  const bucketSize = range / numBuckets;
  const buckets = new Map<number, number>();

  for (const c of candles) {
    const cLow = Math.max(c.low, low);
    const cHigh = Math.min(c.high, high);
    const cRange = cHigh - cLow;
    if (cRange <= 0) continue;

    const startIdx = Math.max(0, Math.floor((cLow - low) / bucketSize));
    const endIdx = Math.min(numBuckets - 1, Math.floor((cHigh - low) / bucketSize));
    const levelsTouched = endIdx - startIdx + 1;
    const volPerLevel = c.volume / levelsTouched;

    for (let idx = startIdx; idx <= endIdx; idx++) {
      const existing = buckets.get(idx) ?? 0;
      buckets.set(idx, existing + volPerLevel);
    }
  }

  const profile: VolumeProfileDataPoint[] = Array.from({ length: numBuckets }, (_, idx) => ({
    price: low + (idx + 0.5) * bucketSize,
    vol: buckets.get(idx) ?? 0,
  })).sort((a, b) => b.price - a.price);

  if (profile.length < 2) return null;

  return {
    time,
    profile,
    width,
  };
}
